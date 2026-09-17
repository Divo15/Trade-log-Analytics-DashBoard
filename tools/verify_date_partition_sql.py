"""Smoke-test the DuckDB SQL used by the R6 date-partitioned cache."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import duckdb


def quoted(path: Path) -> str:
    return path.as_posix().replace("'", "''")


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--market-data", type=Path)
args = parser.parse_args()

with TemporaryDirectory(prefix="r6-partition-check-") as folder:
    root = Path(folder)
    source = root / "source.parquet"
    target = root / "partitioned"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE chain AS SELECT * FROM (VALUES
                ('05/01/2023 09:18:00', 18000, 100.0, 101.0),
                ('12/01/2023 09:18:00', 18100, 110.0, 111.0)
            ) AS values(datetime, strike, ce_close, pe_close)
            """
        )
        connection.execute(f"COPY chain TO '{quoted(source)}' (FORMAT PARQUET)")
        connection.execute(
            f"""
            COPY (
                SELECT
                    CAST(STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) AS trade_date,
                    STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                    CAST(strike AS INTEGER) AS strike,
                    ce_close,
                    pe_close
                FROM read_parquet('{quoted(source)}')
            ) TO '{quoted(target)}' (
                FORMAT PARQUET,
                PARTITION_BY (trade_date),
                COMPRESSION ZSTD
            )
            """
        )
        partitions = sorted(path.name for path in target.iterdir() if path.is_dir())
        assert partitions == ["trade_date=2023-01-05", "trade_date=2023-01-12"], partitions
        row = connection.execute(
            "SELECT ts, strike FROM read_parquet(?)",
            [str(target / "trade_date=2023-01-05" / "*.parquet")],
        ).fetchone()
        assert str(row[0]) == "2023-01-05 09:18:00" and row[1] == 18000, row
    finally:
        connection.close()

print("PASS: typed date partitions and direct single-day reads")

if args.market_data:
    chain = args.market_data.resolve() / "nifty_chain"
    sources = sorted(chain.glob("*.parquet"))
    if not sources:
        parser.error(f"No Parquet files found in {chain}")
    with TemporaryDirectory(prefix="r6-real-partitions-") as folder:
        target = Path(folder) / "partitioned"
        source_glob = quoted(chain / "*.parquet")
        connection = duckdb.connect(":memory:")
        started = perf_counter()
        try:
            connection.execute(
                f"""
                COPY (
                    SELECT
                        CAST(STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) AS trade_date,
                        STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                        CAST(strike AS INTEGER) AS strike,
                        ce_close,
                        pe_close
                    FROM read_parquet('{source_glob}')
                ) TO '{quoted(target)}' (
                    FORMAT PARQUET,
                    PARTITION_BY (trade_date),
                    COMPRESSION ZSTD
                )
                """
            )
            elapsed = perf_counter() - started
            source_signature = connection.execute(
                f"""
                SELECT COUNT(*), BIT_XOR(HASH(
                    STRPTIME(datetime, '%d/%m/%Y %H:%M:%S'),
                    CAST(strike AS INTEGER), ce_close, pe_close
                )) FROM read_parquet('{source_glob}')
                """
            ).fetchone()
            cached_signature = connection.execute(
                """
                SELECT COUNT(*), BIT_XOR(HASH(ts, strike, ce_close, pe_close))
                FROM read_parquet(?)
                """,
                [str(target / "*" / "*.parquet")],
            ).fetchone()
            assert source_signature == cached_signature, (source_signature, cached_signature)
            partitions = sum(path.is_dir() for path in target.iterdir())
            size = sum(path.stat().st_size for path in target.rglob("*.parquet"))
        finally:
            connection.close()
    print(
        f"PASS: real data {source_signature[0]} rows, {partitions} date partitions, "
        f"{size / 1024**2:.1f} MiB cache built in {elapsed:.2f}s"
    )
