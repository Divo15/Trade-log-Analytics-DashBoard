"""Independent DuckDB calculations over a validated canonical trade log."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import duckdb

from trade_log_exporter import CSV_COLUMNS, ExportReceipt, TradeLogError, TradeRecord, validate_trade_log_csv


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [
        {name: _json_value(value) for name, value in zip(columns, row)}
        for row in cursor.fetchall()
    ]


def _longest_streak(values: list[float], *, winning: bool) -> int:
    longest = current = 0
    for value in values:
        matches = value > 0 if winning else value < 0
        current = current + 1 if matches else 0
        longest = max(longest, current)
    return longest


def _longest_underwater(drawdowns: list[float]) -> int:
    longest = current = 0
    for drawdown in drawdowns:
        current = current + 1 if drawdown < -1e-9 else 0
        longest = max(longest, current)
    return longest


def analyze_trade_log(
    csv_path: str | Path | None = None,
    *,
    validated_receipt: ExportReceipt | None = None,
    records: Iterable[TradeRecord] | None = None,
) -> dict[str, Any]:
    """Calculate dashboard metrics from a validated CSV or canonical records.

    Sweep rows pass validated records directly, avoiding a CSV and manifest for
    every combination. A selected row is still rerun with normal file exports.
    """
    memory_rows = list(records) if records is not None else None
    if memory_rows is not None:
        if csv_path is not None or validated_receipt is not None:
            raise ValueError("Use either records or a validated CSV, not both")
        digest = hashlib.sha256()
        for record in memory_rows:
            digest.update(json.dumps(record.to_csv_row(), sort_keys=True).encode("utf-8"))
        receipt = ExportReceipt(
            output_path=Path("<in-memory>"), manifest_path=Path("<in-memory>"),
            row_count=len(memory_rows), sha256=digest.hexdigest(),
        )
        path = None
    else:
        if csv_path is None:
            raise ValueError("csv_path or records is required")
        path = Path(csv_path).resolve()
        receipt = validated_receipt or validate_trade_log_csv(path)
    if receipt.row_count == 0:
        raise TradeLogError("The trade log is valid but contains no completed trades")

    connection = duckdb.connect(":memory:")
    try:
        if memory_rows is None:
            connection.execute(
                "CREATE TABLE source AS SELECT * FROM read_csv(?, header=true, all_varchar=true)",
                [str(path)],
            )
        else:
            columns = ", ".join(f'"{column}" VARCHAR' for column in CSV_COLUMNS)
            connection.execute(f"CREATE TABLE source ({columns})")
            connection.executemany(
                f"INSERT INTO source VALUES ({', '.join('?' for _ in CSV_COLUMNS)})",
                [tuple(record.to_csv_row()[column] for column in CSV_COLUMNS) for record in memory_rows],
            )
        connection.execute(
            """
            CREATE TEMP TABLE legs AS
            SELECT
                run_id,
                trade_id,
                COALESCE(NULLIF(batch_id, ''), trade_id) AS batch_key,
                (batch_id IS NULL OR batch_id = '') AS is_unbatched,
                batch_id,
                leg_id,
                strategy,
                symbol,
                side,
                CAST(entry_time AS TIMESTAMP) AS entry_time,
                CAST(exit_time AS TIMESTAMP) AS exit_time,
                CAST(quantity AS DOUBLE) AS quantity,
                CAST(entry_price AS DOUBLE) AS entry_price,
                CAST(exit_price AS DOUBLE) AS exit_price,
                CAST(multiplier AS DOUBLE) AS multiplier,
                CAST(fees AS DOUBLE) AS fees,
                CASE
                    WHEN side = 'LONG' THEN
                        (CAST(exit_price AS DOUBLE) - CAST(entry_price AS DOUBLE))
                    ELSE
                        (CAST(entry_price AS DOUBLE) - CAST(exit_price AS DOUBLE))
                END * CAST(quantity AS DOUBLE) * CAST(multiplier AS DOUBLE) AS gross_pnl
            FROM source
            """
        )
        connection.execute(
            """
            CREATE TEMP TABLE batches AS
            SELECT
                batch_key,
                MIN(entry_time) AS entry_time,
                MAX(exit_time) AS exit_time,
                COUNT(*) AS legs,
                SUM(gross_pnl) AS gross_pnl,
                SUM(fees) AS fees,
                SUM(gross_pnl - fees) AS net_pnl
            FROM legs
            GROUP BY batch_key, is_unbatched
            """
        )
        connection.execute(
            """
            CREATE TEMP TABLE daily AS
            SELECT
                CAST(exit_time AS DATE) AS day,
                COUNT(*) AS batches,
                SUM(gross_pnl) AS gross_pnl,
                SUM(fees) AS fees,
                SUM(net_pnl) AS net_pnl
            FROM batches
            GROUP BY day
            """
        )

        overview = _rows(
            connection.execute(
                """
                SELECT
                    ANY_VALUE(run_id) AS run_id,
                    ANY_VALUE(strategy) AS strategy,
                    MIN(entry_time) AS start_time,
                    MAX(exit_time) AS end_time,
                    COUNT(*) AS leg_count,
                    (SELECT COUNT(*) FROM batches) AS batch_count,
                    COUNT(DISTINCT CAST(exit_time AS DATE)) AS traded_days,
                    COUNT(DISTINCT symbol) AS symbols,
                    SUM(gross_pnl) AS gross_pnl,
                    SUM(fees) AS fees,
                    SUM(gross_pnl - fees) AS net_pnl
                FROM legs
                """
            )
        )[0]

        stats = _rows(
            connection.execute(
                """
                SELECT
                    AVG(net_pnl) AS average_batch_pnl,
                    MEDIAN(net_pnl) AS median_batch_pnl,
                    AVG(CASE WHEN net_pnl > 0 THEN net_pnl END) AS average_win_batch,
                    AVG(CASE WHEN net_pnl < 0 THEN net_pnl END) AS average_loss_batch,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                    100.0 * SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) AS win_rate,
                    SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END)
                        / NULLIF(ABS(SUM(CASE WHEN net_pnl < 0 THEN net_pnl ELSE 0 END)), 0)
                        AS profit_factor,
                    MAX(net_pnl) AS best_batch,
                    MIN(net_pnl) AS worst_batch
                FROM batches
                """
            )
        )[0]

        daily = _rows(
            connection.execute(
                """
                WITH curve AS (
                    SELECT
                        *,
                        SUM(net_pnl) OVER (ORDER BY day) AS equity
                    FROM daily
                ), peaks AS (
                    SELECT
                        *,
                        GREATEST(0, MAX(equity) OVER (ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
                            AS peak
                    FROM curve
                )
                SELECT *, equity - peak AS drawdown
                FROM peaks
                ORDER BY day
                """
            )
        )
        day_values = [float(row["net_pnl"]) for row in daily]
        overview["traded_days"] = len(daily)
        winning_days = [value for value in day_values if value > 0]
        losing_days = [value for value in day_values if value < 0]
        day_mean = sum(day_values) / len(day_values)
        if len(day_values) > 1:
            variance = sum((value - day_mean) ** 2 for value in day_values) / (len(day_values) - 1)
            day_stddev = math.sqrt(variance)
            sharpe = day_mean / day_stddev * math.sqrt(252) if day_stddev else None
        else:
            sharpe = None

        batch_series = _rows(
            connection.execute(
                """
                SELECT batch_key, entry_time, exit_time, legs, gross_pnl, fees, net_pnl
                FROM batches
                ORDER BY exit_time, batch_key
                """
            )
        )
        batch_values = [float(row["net_pnl"]) for row in batch_series]
        stats.update(
            {
                "sharpe_traded_days": sharpe,
                "max_drawdown": min((float(row["drawdown"]) for row in daily), default=0.0),
                "best_day": max(day_values),
                "worst_day": min(day_values),
                "average_day_pnl": day_mean,
                "longest_win_streak": _longest_streak(batch_values, winning=True),
                "longest_loss_streak": _longest_streak(batch_values, winning=False),
                "win_days": len(winning_days),
                "loss_days": len(losing_days),
                "breakeven_days": len(day_values) - len(winning_days) - len(losing_days),
                "day_loss_rate": 100.0 * len(losing_days) / len(day_values),
                "day_win_rate": 100.0 * len(winning_days) / len(day_values),
                "average_win_day": sum(winning_days) / len(winning_days) if winning_days else None,
                "average_loss_day": sum(losing_days) / len(losing_days) if losing_days else None,
                "day_risk_reward": (
                    (sum(winning_days) / len(winning_days))
                    / abs(sum(losing_days) / len(losing_days))
                    if winning_days and losing_days
                    else None
                ),
                "median_day_pnl": float(
                    connection.execute("SELECT MEDIAN(net_pnl) FROM daily").fetchone()[0]
                ),
                "longest_underwater_traded_days": _longest_underwater(
                    [float(row["drawdown"]) for row in daily]
                ),
            }
        )

        sorted_winning_days = sorted(winning_days, reverse=True)
        gross_winning_days = sum(sorted_winning_days)
        best_day = max(day_values)
        worst_day = min(day_values)
        concentration = {
            "gross_winning_days": gross_winning_days,
            "best_day_share_pct": (
                100.0 * best_day / gross_winning_days if gross_winning_days else None
            ),
            "top_three_days_share_pct": (
                100.0 * sum(sorted_winning_days[:3]) / gross_winning_days
                if gross_winning_days
                else None
            ),
            "net_without_best_day": float(overview["net_pnl"]) - best_day,
            "net_without_best_and_worst": (
                float(overview["net_pnl"]) - best_day - worst_day if len(day_values) > 1 else 0.0
            ),
        }

        monthly = _rows(
            connection.execute(
                """
                WITH month_curve AS (
                    SELECT
                        STRFTIME(day, '%Y-%m') AS month,
                        day,
                        net_pnl,
                        SUM(net_pnl) OVER (
                            PARTITION BY STRFTIME(day, '%Y-%m') ORDER BY day
                        ) AS month_equity
                    FROM daily
                ), month_peaks AS (
                    SELECT
                        *,
                        GREATEST(0, MAX(month_equity) OVER (
                            PARTITION BY month ORDER BY day
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        )) AS month_peak
                    FROM month_curve
                )
                SELECT
                    month,
                    SUM(net_pnl) AS net_pnl,
                    MAX(net_pnl) AS best_day,
                    MIN(net_pnl) AS worst_day,
                    GREATEST(0, MAX(month_equity)) AS high,
                    ARG_MAX(month_equity, day) AS close,
                    MIN(month_equity - month_peak) AS max_drawdown,
                    COUNT(*) AS traded_days
                FROM month_peaks
                GROUP BY month
                ORDER BY month
                """
            )
        )
        symbols = _rows(
            connection.execute(
                """
                SELECT symbol, COUNT(*) AS legs, SUM(gross_pnl - fees) AS net_pnl
                FROM legs
                GROUP BY symbol
                ORDER BY ABS(net_pnl) DESC
                LIMIT 12
                """
            )
        )

        return {
            "validation": {
                "status": "passed",
                "schema_version": receipt.schema_version,
                "row_count": receipt.row_count,
                "sha256": receipt.sha256,
            },
            "assumptions": {
                "fees_included": float(overview["fees"]) > 0,
                "fees_note": "Brokerage and statutory charges are not included. Fees are zero."
                if float(overview["fees"]) == 0
                else "Net P&L subtracts the fees supplied by the backtest.",
                "sharpe_note": "Annualised from traded-day P&L; no risk-free rate applied.",
            },
            "overview": overview,
            "statistics": stats,
            "concentration": concentration,
            "daily": daily,
            "monthly": monthly,
            "symbols": symbols,
            "batches": batch_series[-100:],
        }
    finally:
        connection.close()
