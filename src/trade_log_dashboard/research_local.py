"""Trusted, deterministic market-data evidence for the Research controller.

This module owns filesystem and DuckDB access. Model sessions receive its bounded
JSON output and never need shell, Python, or direct dataset access.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

import duckdb


TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"


def _json_value(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(cursor):
    names = [item[0] for item in cursor.description]
    return [
        {name: _json_value(value) for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _describe(connection, source):
    return [
        {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
        for row in connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
        ).fetchall()
    ]


def _column_map(schema):
    return {row["name"].lower(): row["name"] for row in schema}


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def _quality(connection, source, schema, keys):
    columns = [row["name"] for row in schema]
    null_sql = ", ".join(
        f"SUM(CASE WHEN {_identifier(name)} IS NULL THEN 1 ELSE 0 END) AS {_identifier(name)}"
        for name in columns
    )
    null_row = connection.execute(
        f"SELECT {null_sql} FROM read_parquet(?)", [str(source)]
    ).fetchone()
    nulls = {name: int(value or 0) for name, value in zip(columns, null_row)}
    key_names = [name for name in keys if name in columns]
    duplicate_rows = None
    if key_names:
        key_sql = ", ".join(_identifier(name) for name in key_names)
        duplicate_rows = connection.execute(
            f"SELECT COUNT(*) - COUNT(DISTINCT ({key_sql})) FROM read_parquet(?)",
            [str(source)],
        ).fetchone()[0]
    return {
        "rows": int(connection.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(source)]).fetchone()[0]),
        "null_counts": nulls,
        "duplicate_rows_by_key": int(duplicate_rows) if duplicate_rows is not None else None,
        "duplicate_key": key_names,
    }


def _timestamp_coverage(connection, source):
    return _rows(connection.execute(
        f"""
        SELECT
          MIN(TRY_STRPTIME(datetime, '{TIMESTAMP_FORMAT}')) AS first_timestamp,
          MAX(TRY_STRPTIME(datetime, '{TIMESTAMP_FORMAT}')) AS last_timestamp,
          COUNT(*) FILTER (WHERE TRY_STRPTIME(datetime, '{TIMESTAMP_FORMAT}') IS NULL) AS invalid_timestamps,
          COUNT(DISTINCT CAST(TRY_STRPTIME(datetime, '{TIMESTAMP_FORMAT}') AS DATE)) AS trading_days
        FROM read_parquet(?)
        """,
        [str(source)],
    ))[0]


def _dte_distribution(connection, source):
    return _rows(connection.execute(
        """
        SELECT DTE AS dte, COUNT(*) AS rows,
               COUNT(DISTINCT CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE)) AS trading_days
        FROM read_parquet(?)
        GROUP BY DTE ORDER BY DTE
        """,
        [str(source)],
    ))


def _summary_behaviour(connection, source, columns):
    required = {"datetime", "future_open", "future_close", "straddle_future", "dte"}
    if not required.issubset(columns):
        return {"available": False, "reason": f"Required fields are missing: {sorted(required - set(columns))}"}
    by_dte = _rows(connection.execute(
        """
        WITH bars AS (
          SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                 future_open, future_close, straddle_future, DTE
          FROM read_parquet(?)
        ), daily AS (
          SELECT CAST(ts AS DATE) AS session_date,
                 CAST(MEDIAN(DTE) AS BIGINT) AS dte,
                 ARG_MIN(future_open, ts) AS underlying_open,
                 ARG_MAX(future_close, ts) AS underlying_close,
                 ARG_MIN(straddle_future, ts) AS premium_open,
                 ARG_MAX(straddle_future, ts) AS premium_close
          FROM bars WHERE ts IS NOT NULL GROUP BY CAST(ts AS DATE)
        ), metrics AS (
          SELECT *, 100.0 * (underlying_close / NULLIF(underlying_open, 0) - 1) AS underlying_return_pct,
                    100.0 * (premium_close / NULLIF(premium_open, 0) - 1) AS straddle_change_pct
          FROM daily
        )
        SELECT dte, COUNT(*) AS sessions,
               ROUND(AVG(underlying_return_pct), 5) AS average_underlying_return_pct,
               ROUND(MEDIAN(underlying_return_pct), 5) AS median_underlying_return_pct,
               ROUND(QUANTILE_CONT(underlying_return_pct, 0.1), 5) AS p10_underlying_return_pct,
               ROUND(QUANTILE_CONT(underlying_return_pct, 0.9), 5) AS p90_underlying_return_pct,
               ROUND(AVG(straddle_change_pct), 5) AS average_straddle_change_pct,
               ROUND(MEDIAN(straddle_change_pct), 5) AS median_straddle_change_pct,
               ROUND(100.0 * AVG(CASE WHEN straddle_change_pct < 0 THEN 1.0 ELSE 0.0 END), 3) AS straddle_decay_session_pct
        FROM metrics WHERE underlying_open > 0 AND premium_open > 0
        GROUP BY dte ORDER BY dte
        """,
        [str(source)],
    ))
    overnight = _rows(connection.execute(
        """
        WITH bars AS (
          SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                 future_open, future_close, straddle_future
          FROM read_parquet(?)
        ), daily AS (
          SELECT CAST(ts AS DATE) AS session_date,
                 ARG_MIN(future_open, ts) AS underlying_open,
                 ARG_MAX(future_close, ts) AS underlying_close,
                 ARG_MIN(straddle_future, ts) AS premium_open,
                 ARG_MAX(straddle_future, ts) AS premium_close
          FROM bars WHERE ts IS NOT NULL GROUP BY CAST(ts AS DATE)
        ), gaps AS (
          SELECT session_date,
                 100.0 * (LEAD(underlying_open) OVER (ORDER BY session_date) / NULLIF(underlying_close, 0) - 1) AS underlying_gap_pct,
                 100.0 * (LEAD(premium_open) OVER (ORDER BY session_date) / NULLIF(premium_close, 0) - 1) AS straddle_gap_pct
          FROM daily
        )
        SELECT COUNT(underlying_gap_pct) AS transitions,
               ROUND(AVG(underlying_gap_pct), 5) AS average_underlying_gap_pct,
               ROUND(MEDIAN(underlying_gap_pct), 5) AS median_underlying_gap_pct,
               ROUND(QUANTILE_CONT(underlying_gap_pct, 0.05), 5) AS p05_underlying_gap_pct,
               ROUND(QUANTILE_CONT(underlying_gap_pct, 0.95), 5) AS p95_underlying_gap_pct,
               ROUND(AVG(straddle_gap_pct), 5) AS average_straddle_gap_pct,
               ROUND(MEDIAN(straddle_gap_pct), 5) AS median_straddle_gap_pct
        FROM gaps
        """,
        [str(source)],
    ))[0]
    invalid_prices = _rows(connection.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE future_open <= 0 OR future_close <= 0) AS nonpositive_underlying_rows,
          COUNT(*) FILTER (WHERE straddle_future <= 0) AS nonpositive_straddle_rows
        FROM read_parquet(?)
        """,
        [str(source)],
    ))[0]
    return {
        "available": True,
        "definitions": {
            "underlying_return_pct": "First future_open to last future_close within each session.",
            "straddle_change_pct": "First to last straddle_future value within each session; negative means premium decay.",
            "overnight_gap_pct": "Prior session last value to next session first value; consecutive rows are not asserted to be adjacent exchange sessions.",
        },
        "session_behaviour_by_dte": by_dte,
        "overnight_behaviour": overnight,
        "invalid_price_counts": invalid_prices,
    }


def _atm_option_behaviour(connection, summary_source, chain_source, summary_columns, chain_columns):
    required_summary = {"datetime", "future_atm", "dte"}
    required_chain = {"datetime", "strike", "ce_close", "pe_close", "dte"}
    if not required_summary.issubset(summary_columns) or not required_chain.issubset(chain_columns):
        return {"available": False, "reason": "ATM join fields or CE/PE close fields are missing."}
    rows = _rows(connection.execute(
        """
        WITH summary AS (
          SELECT datetime, future_atm, DTE FROM read_parquet(?)
        ), chain AS (
          SELECT datetime, strike, ce_close, pe_close, DTE FROM read_parquet(?)
        ), atm AS (
          SELECT TRY_STRPTIME(c.datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                 c.DTE, c.ce_close + c.pe_close AS atm_combined_close
          FROM chain c JOIN summary s
            ON c.datetime = s.datetime AND c.strike = s.future_atm
          WHERE c.ce_close > 0 AND c.pe_close > 0
        ), daily AS (
          SELECT CAST(ts AS DATE) AS session_date,
                 CAST(MEDIAN(DTE) AS BIGINT) AS dte,
                 ARG_MIN(atm_combined_close, ts) AS entry_premium,
                 ARG_MAX(atm_combined_close, ts) AS exit_premium
          FROM atm WHERE ts IS NOT NULL GROUP BY CAST(ts AS DATE)
        )
        SELECT dte, COUNT(*) AS sessions,
               ROUND(AVG(100.0 * (exit_premium / NULLIF(entry_premium, 0) - 1)), 5) AS average_atm_combined_change_pct,
               ROUND(MEDIAN(100.0 * (exit_premium / NULLIF(entry_premium, 0) - 1)), 5) AS median_atm_combined_change_pct,
               ROUND(100.0 * AVG(CASE WHEN exit_premium < entry_premium THEN 1.0 ELSE 0.0 END), 3) AS atm_decay_session_pct
        FROM daily WHERE entry_premium > 0 GROUP BY dte ORDER BY dte
        """,
        [str(summary_source), str(chain_source)],
    ))
    return {
        "available": True,
        "definition": "Exact timestamp and exact future_atm strike join; first-to-last CE close + PE close per session. This is descriptive, not a fill-model backtest.",
        "session_behaviour_by_dte": rows,
    }


def build_market_profile(market_data, dataset, target):
    """Write a bounded evidence profile and return its path."""
    root = Path(market_data).resolve()
    target = Path(target)
    summary = root / dataset["summary_file"]
    chain_folder = root / dataset["chain_folder"]
    chain_files = sorted(chain_folder.glob("*.parquet"))
    if not summary.is_file() or not chain_files:
        raise ValueError("Selected dataset is missing its configured summary or option-chain parquet files.")
    chain_glob = chain_folder / "*.parquet"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads TO 4")
        summary_schema = _describe(connection, summary)
        chain_schema = _describe(connection, chain_glob)
        summary_columns = _column_map(summary_schema)
        chain_columns = _column_map(chain_schema)
        profile = {
            "version": 1,
            "dataset": dataset,
            "sources": {
                "summary": str(summary),
                "chain_glob": str(chain_glob),
                "chain_files": len(chain_files),
                "chain_bytes": sum(path.stat().st_size for path in chain_files),
            },
            "methodology": [
                "All measurements were computed locally by the trusted dashboard controller with DuckDB.",
                "No model had direct filesystem, shell, Python, or parquet access.",
                "Descriptive premium changes are not fills, trades, P&L, margin, or proof of profitability.",
                "Timestamps are parsed only with DD/MM/YYYY HH:MM:SS; invalid values are counted.",
            ],
            "summary": {
                "schema": summary_schema,
                "quality": _quality(connection, summary, summary_schema, [summary_columns.get("datetime", "")]),
                "coverage": _timestamp_coverage(connection, summary),
                "dte_distribution": _dte_distribution(connection, summary) if "dte" in summary_columns else [],
            },
            "chain": {
                "schema": chain_schema,
                "quality": _quality(connection, chain_glob, chain_schema, [chain_columns.get("datetime", ""), chain_columns.get("strike", "")]),
                "coverage": _timestamp_coverage(connection, chain_glob),
                "dte_distribution": _dte_distribution(connection, chain_glob) if "dte" in chain_columns else [],
            },
            "observations": {
                "summary_behaviour": _summary_behaviour(connection, summary, summary_columns),
                "atm_option_behaviour": _atm_option_behaviour(
                    connection, summary, chain_glob, summary_columns, chain_columns
                ),
            },
            "known_unknowns": [
                "Column names do not establish economic units or exchange provenance.",
                "The profile does not infer bid/ask spreads, market impact, margin, brokerage, taxes, or historical lot-size changes.",
                "A generated strategy must still be executed by the trusted dashboard worker before review can pass.",
            ],
        }
    finally:
        connection.close()
    encoded = json.dumps(profile, indent=2, ensure_ascii=False, default=_json_value)
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Local market profile exceeded its 2 MiB evidence limit.")
    target.write_text(encoded + "\n", encoding="utf-8")
    return target


def _experiment_parameters(assignment, dataset):
    dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", assignment or "")
    entry = re.search(r"entry_(?:decision_)?time\s*=\s*(\d{2}:\d{2})", assignment or "", re.I)
    exit_ = re.search(r"exit_(?:decision_)?time\s*=\s*(\d{2}:\d{2})", assignment or "", re.I)
    dte_min = re.search(r"dte_min\s*=\s*(\d+)", assignment or "", re.I)
    dte_max = re.search(r"dte_max\s*=\s*(\d+)", assignment or "", re.I)
    return {
        "start_date": dates[0] if dates else dataset.get("start_date"),
        "end_date": dates[1] if len(dates) > 1 else dataset.get("end_date"),
        "entry_time": entry.group(1) if entry else "09:30",
        "exit_time": exit_.group(1) if exit_ else "15:15",
        "dte_min": int(dte_min.group(1)) if dte_min else 0,
        "dte_max": int(dte_max.group(1)) if dte_max else 99,
    }


def run_fixed_contract_audit(market_data, dataset, target, assignment=""):
    """Measure one fixed-strike intraday short-ATM pair without claiming fills."""
    root = Path(market_data).resolve()
    target = Path(target)
    summary = root / dataset["summary_file"]
    chain_glob = root / dataset["chain_folder"] / "*.parquet"
    parameters = _experiment_parameters(assignment, dataset)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads TO 4")
        connection.execute(
            """
            CREATE TEMP TABLE summary_bars AS
            SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                   CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) AS session_date,
                   future_atm, DTE
            FROM read_parquet(?)
            WHERE CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE)
                  BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND DTE BETWEEN ? AND ?
            """,
            [str(summary), parameters["start_date"], parameters["end_date"], parameters["dte_min"], parameters["dte_max"]],
        )
        connection.execute(
            """
            CREATE TEMP TABLE chain_bars AS
            SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                   CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) AS session_date,
                   strike, ce_close, pe_close, DTE
            FROM read_parquet(?)
            WHERE CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE)
                  BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND DTE BETWEEN ? AND ?
            """,
            [str(chain_glob), parameters["start_date"], parameters["end_date"], parameters["dte_min"], parameters["dte_max"]],
        )
        connection.execute(
            """
            CREATE TEMP TABLE audit_rows AS
            WITH entry_summary AS (
              SELECT * FROM summary_bars
              WHERE STRFTIME(ts, '%H:%M') = ?
              QUALIFY ROW_NUMBER() OVER (PARTITION BY session_date ORDER BY ts) = 1
            ), entries AS (
              SELECT s.session_date, s.ts AS entry_ts, s.future_atm AS strike, s.DTE,
                     c.ce_close AS entry_ce, c.pe_close AS entry_pe
              FROM entry_summary s LEFT JOIN chain_bars c
                ON c.ts = s.ts AND c.strike = s.future_atm
            ), exits AS (
              SELECT e.*, c.ts AS exit_ts, c.ce_close AS exit_ce, c.pe_close AS exit_pe
              FROM entries e LEFT JOIN chain_bars c
                ON c.session_date = e.session_date AND c.strike = e.strike
               AND STRFTIME(c.ts, '%H:%M') = ?
              QUALIFY ROW_NUMBER() OVER (PARTITION BY e.session_date ORDER BY c.ts NULLS LAST) = 1
            ), path AS (
              SELECT e.session_date,
                     MAX(c.ce_close + c.pe_close) FILTER (WHERE c.ce_close > 0 AND c.pe_close > 0) AS max_combined,
                     MIN(c.ce_close + c.pe_close) FILTER (WHERE c.ce_close > 0 AND c.pe_close > 0) AS min_combined,
                     COUNT(*) FILTER (WHERE c.ce_close IS NULL OR c.pe_close IS NULL) AS missing_path_rows
              FROM exits e LEFT JOIN chain_bars c
                ON c.session_date = e.session_date AND c.strike = e.strike
               AND c.ts BETWEEN e.entry_ts AND e.exit_ts
              GROUP BY e.session_date
            )
            SELECT e.*, p.max_combined, p.min_combined, p.missing_path_rows,
                   e.entry_ce + e.entry_pe AS entry_combined,
                   e.exit_ce + e.exit_pe AS exit_combined,
                   (e.entry_ce + e.entry_pe) - (e.exit_ce + e.exit_pe) AS short_change_points,
                   100.0 * ((e.entry_ce + e.entry_pe) - (e.exit_ce + e.exit_pe)) /
                     NULLIF(e.entry_ce + e.entry_pe, 0) AS short_change_pct,
                   100.0 * (p.max_combined - (e.entry_ce + e.entry_pe)) /
                     NULLIF(e.entry_ce + e.entry_pe, 0) AS max_adverse_excursion_pct,
                   100.0 * ((e.entry_ce + e.entry_pe) - p.min_combined) /
                     NULLIF(e.entry_ce + e.entry_pe, 0) AS max_favourable_excursion_pct
            FROM exits e LEFT JOIN path p USING (session_date)
            """,
            [parameters["entry_time"], parameters["exit_time"]],
        )
        overview = _rows(connection.execute(
            """
            SELECT COUNT(*) AS eligible_summary_sessions,
                   COUNT(*) FILTER (WHERE entry_ce > 0 AND entry_pe > 0) AS valid_entries,
                   COUNT(*) FILTER (WHERE NOT COALESCE(entry_ce > 0 AND entry_pe > 0, FALSE)) AS missing_or_invalid_entries,
                   COUNT(*) FILTER (WHERE entry_ce > 0 AND entry_pe > 0 AND exit_ce > 0 AND exit_pe > 0) AS resolved_exits,
                   COUNT(*) FILTER (WHERE entry_ce > 0 AND entry_pe > 0 AND NOT COALESCE(exit_ce > 0 AND exit_pe > 0, FALSE)) AS unresolved_exits,
                   ROUND(AVG(short_change_points) FILTER (WHERE exit_combined > 0), 5) AS average_short_change_points,
                   ROUND(MEDIAN(short_change_points) FILTER (WHERE exit_combined > 0), 5) AS median_short_change_points,
                   ROUND(AVG(short_change_pct) FILTER (WHERE exit_combined > 0), 5) AS average_short_change_pct,
                   ROUND(MEDIAN(short_change_pct) FILTER (WHERE exit_combined > 0), 5) AS median_short_change_pct,
                   ROUND(100.0 * AVG(CASE WHEN short_change_points > 0 THEN 1.0 ELSE 0.0 END)
                     FILTER (WHERE exit_combined > 0), 3) AS positive_short_change_pct,
                   ROUND(AVG(max_adverse_excursion_pct) FILTER (WHERE exit_combined > 0), 5) AS average_max_adverse_excursion_pct,
                   ROUND(QUANTILE_CONT(max_adverse_excursion_pct, .95) FILTER (WHERE exit_combined > 0), 5) AS p95_max_adverse_excursion_pct,
                   SUM(missing_path_rows) FILTER (WHERE exit_combined > 0) AS missing_path_rows
            FROM audit_rows
            """
        ))[0]
        by_dte = _rows(connection.execute(
            """
            SELECT DTE AS dte, COUNT(*) AS resolved_sessions,
                   ROUND(AVG(short_change_pct), 5) AS average_short_change_pct,
                   ROUND(MEDIAN(short_change_pct), 5) AS median_short_change_pct,
                   ROUND(100.0 * AVG(CASE WHEN short_change_points > 0 THEN 1.0 ELSE 0.0 END), 3) AS positive_short_change_pct,
                   ROUND(QUANTILE_CONT(max_adverse_excursion_pct, .95), 5) AS p95_max_adverse_excursion_pct
            FROM audit_rows WHERE entry_combined > 0 AND exit_combined > 0 GROUP BY DTE ORDER BY DTE
            """
        ))
        by_year = _rows(connection.execute(
            """
            SELECT YEAR(session_date) AS year, COUNT(*) AS resolved_sessions,
                   ROUND(AVG(short_change_pct), 5) AS average_short_change_pct,
                   ROUND(MEDIAN(short_change_pct), 5) AS median_short_change_pct,
                   ROUND(100.0 * AVG(CASE WHEN short_change_points > 0 THEN 1.0 ELSE 0.0 END), 3) AS positive_short_change_pct,
                   ROUND(QUANTILE_CONT(max_adverse_excursion_pct, .95), 5) AS p95_max_adverse_excursion_pct
            FROM audit_rows WHERE entry_combined > 0 AND exit_combined > 0 GROUP BY YEAR(session_date) ORDER BY year
            """
        ))
        report = {
            "version": 1,
            "experiment": "fixed_contract_intraday_atm_short_pair_audit",
            "parameters": parameters,
            "assignment": assignment,
            "methodology": [
                "The controller selected future_atm at the configured entry minute, then tracked that exact strike's CE and PE close fields through the configured exit minute.",
                "All entry eligibility uses only the entry timestamp. Missing entry or exit legs remain counted; future completeness never filters entries.",
                "Short change is entry combined close minus exit combined close in raw dataset price points. It is descriptive and is not canonical trade P&L.",
                "No slippage, fees, multiplier, taxes, margin or lot size are applied because their units/application are not established by the parquet schema.",
                "Intraday adverse/favourable excursion uses synchronized CE+PE close marks for the fixed entry strike.",
            ],
            "overview": overview,
            "by_dte": by_dte,
            "by_year": by_year,
            "limitations": [
                "The dataset contains no explicit expiry identifier in the inspected chain schema; dataset-folder expiry semantics remain an external contract assumption.",
                "Close fields are decision marks, not executable bid/ask fills.",
                "This audit has no SL, TP, re-entry or overnight state and does not prove profitability.",
            ],
        }
    finally:
        connection.close()
    encoded = json.dumps(report, indent=2, ensure_ascii=False, default=_json_value)
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Local experiment report exceeded its 2 MiB evidence limit.")
    target.write_text(encoded + "\n", encoding="utf-8")
    return target
