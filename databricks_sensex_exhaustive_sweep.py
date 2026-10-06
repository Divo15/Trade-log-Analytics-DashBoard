"""SENSEX DTE-0 exhaustive sweep with memory-bounded Delta output.

The sweep includes combined SL/TP and independent per-leg SL only. Per-leg
take-profit is intentionally disabled. Re-entry limits are swept separately
for leg SL, combined SL, and combined profit booking. Databricks helpers can
stream every screening result to Delta without retaining the full result set
in driver memory. Ranking is intentionally outside this strategy module.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

import types

_base = types.SimpleNamespace()
exec(compile("\"\"\"SENSEX DTE-0 absolute-premium short-straddle exhaustive sweep.\r\n\r\nRules preserved from the supplied image and reference implementation:\r\n* DTE=0 current-expiry data from the dashboard-selected dataset.\r\n* Entry time sweeps 09:30-11:00 inclusive in five-minute increments.\r\n* Short CE and PE are selected independently closest to the configured absolute\r\n  premium from Rs 200-Rs 400, on the ATM-or-outward side of the ATM strike.\r\n* Individual-leg stop loss and take-profit each sweep 20%-100% in 5% steps,\n  measured from that leg's entry premium. Each leg exits independently; the\n  remaining leg continues until its own exit or end of day.\n* Overall re-entry sweeps zero through four across the entire cycle and occurs\n  on the next market bar after the cycle has no remaining open legs.\n* 1.0% slippage is applied to entries and exits.\n* Remaining open legs close at the first available 15:15-or-later bar, falling\r\n  back to the last available bar when the dataset has no such timestamp.\r\n\"\"\"\r\n\r\nfrom __future__ import annotations\r\n\r\nfrom collections.abc import Sequence\nfrom datetime import datetime, timedelta\nimport os\nfrom pathlib import Path\nfrom time import perf_counter\r\nfrom typing import Any\r\nfrom zoneinfo import ZoneInfo\r\n\r\nimport numpy as np\nimport pandas as pd\n\ntry:\n    import numba\n    from numba import njit, prange\nexcept ImportError:  # The original Python runner remains available without Numba.\n    numba = None\n    njit = None\n    prange = range\n\r\n\r\nSTRATEGY_CONTRACT_VERSION = \"2\"\r\nRUN_MODE = \"sweep\"\r\n\r\nPREMIUM_PRICES = tuple(range(200, 401, 25))\r\nLEG_SL_PCTS = tuple(range(20, 101, 5))\nLEG_TP_PCTS = tuple(range(20, 101, 5))\nREENTRIES = (0, 1, 2, 3, 4)\r\nENTRY_TIMES = tuple(\r\n    f\"{minutes // 60:02d}:{minutes % 60:02d}:00\"\r\n    for minutes in range(9 * 60 + 30, 11 * 60 + 1, 5)\r\n)\r\n\r\nSWEEP_PARAMETER_NAMES = {\r\n    \"premium_price\",\r\n    \"leg_sl_pct\",\r\n    \"leg_tp_pct\",\n    \"overall_reentries\",\r\n    \"entry_start\",\r\n}\r\n\r\n\r\nclass _SweepGrid(Sequence):\r\n    \"\"\"Materialize one Cartesian-product mapping at a time during iteration.\"\"\"\r\n\r\n    _dimensions = (\r\n        PREMIUM_PRICES,\r\n        LEG_SL_PCTS,\r\n        LEG_TP_PCTS,\n        REENTRIES,\r\n        ENTRY_TIMES,\r\n    )\r\n\r\n    def __len__(self) -> int:\r\n        result = 1\r\n        for values in self._dimensions:\r\n            result *= len(values)\r\n        return result\r\n\r\n    def __getitem__(self, index):\r\n        if isinstance(index, slice):\r\n            return tuple(self[position] for position in range(*index.indices(len(self))))\r\n        if index < 0:\r\n            index += len(self)\r\n        if index < 0 or index >= len(self):\r\n            raise IndexError(index)\r\n        selected = []\r\n        for values in reversed(self._dimensions):\r\n            index, offset = divmod(index, len(values))\r\n            selected.append(values[offset])\r\n        premium, leg_sl, leg_tp, reentries, entry_start = reversed(selected)\n        return {\r\n            \"premium_price\": float(premium),\r\n            \"leg_sl_pct\": float(leg_sl),\r\n            \"leg_tp_pct\": float(leg_tp),\n            \"overall_reentries\": int(reentries),\r\n            \"entry_start\": str(entry_start),\r\n        }\r\n\r\n\r\nSWEEP_PARAMETER_SETS = _SweepGrid()\r\nSWEEP_COMBINATION_COUNT = len(SWEEP_PARAMETER_SETS)\r\n\r\n\r\nALLOWED_PARAMETERS = {\r\n    *SWEEP_PARAMETER_NAMES,\r\n    \"lot_size\",\r\n}\r\n\r\nSTRATEGY_NAME = \"sensex-dte0-absolute-premium-short-straddle\"\r\nENTRY_START = \"09:30:00\"\r\nEXIT_TIME = \"15:15:00\"\r\nLEG_SL_PCT = 30.0\nLEG_TP_PCT = 30.0\nOVERALL_REENTRIES = 3\r\nSLIPPAGE = 0.01\nMARGIN_BASIS_RS = 300_000\r\nDEFAULT_LOT_SIZE_BY_SYMBOL = {\r\n    \"NIFTY\": 65,\r\n    \"SENSEX\": 20,\r\n}\r\nTIMEZONE = ZoneInfo(\"Asia/Kolkata\")\n\n\ndef _spark_to_pandas(frame) -> pd.DataFrame:\n    \"\"\"Convert a Spark DataFrame without relying on Serverless Arrow settings.\"\"\"\n    rows = (row.asDict(recursive=True) for row in frame.toLocalIterator())\n    return pd.DataFrame.from_records(rows)\n\n\ndef _format_duration(seconds: float) -> str:\n    total = max(0, int(seconds))\r\n    hours, remainder = divmod(total, 3600)\r\n    minutes, seconds = divmod(remainder, 60)\r\n    if hours:\r\n        return f\"{hours}h {minutes:02d}m {seconds:02d}s\"\r\n    if minutes:\r\n        return f\"{minutes}m {seconds:02d}s\"\r\n    return f\"{seconds}s\"\r\n\r\n\r\nclass _DayProgress:\r\n    \"\"\"Report progress by trading day without printing from dashboard runs.\"\"\"\r\n\r\n    def __init__(self, total: int, reporter=None):\r\n        self.total = total\r\n        self.reporter = reporter\r\n        self.completed = 0\r\n        self.started = perf_counter()\r\n        self.last_fallback_refresh = 0.0\r\n        if reporter is not None:\r\n            self.bar = None\r\n            reporter(0, total, \"trading day\", \"Running backtest\")\r\n            return\r\n        try:\r\n            from tqdm.auto import tqdm\r\n        except ImportError:\r\n            self.bar = None\r\n        else:\r\n            self.bar = tqdm(\r\n                total=total,\r\n                desc=\"SENSEX Backtest\",\r\n                unit=\" trading day\",\r\n                dynamic_ncols=True,\r\n                mininterval=1.0,\r\n                smoothing=0.1,\r\n            )\r\n\r\n    def update(self) -> None:\r\n        self.completed += 1\r\n        if self.reporter is not None:\r\n            self.reporter(self.completed, self.total, \"trading day\", \"Running backtest\")\r\n            return\r\n        elapsed = perf_counter() - self.started\r\n        speed = self.completed / elapsed if elapsed > 0 else 0.0\r\n        remaining = (self.total - self.completed) / speed if speed > 0 else 0.0\r\n        if self.bar is not None:\r\n            finish = datetime.now().astimezone() + timedelta(seconds=remaining)\r\n            self.bar.set_postfix_str(\r\n                f\"ETA {_format_duration(remaining)}; finish {finish.strftime('%I:%M:%S %p').lstrip('0')}\",\r\n                refresh=False,\r\n            )\r\n            self.bar.update(1)\r\n            return\r\n        now = perf_counter()\r\n        if self.completed != self.total and now - self.last_fallback_refresh < 1.0:\r\n            return\r\n        self.last_fallback_refresh = now\r\n        percent = self.completed / self.total * 100 if self.total else 100.0\r\n        print(\r\n            f\"SENSEX Backtest: {percent:5.1f}% | \"\r\n            f\"Completed: {self.completed}/{self.total} trading days | \"\r\n            f\"Elapsed: {_format_duration(elapsed)} | \"\r\n            f\"Speed: {speed:.2f} days/sec | \"\r\n            f\"ETA Remaining: {_format_duration(remaining)}\",\r\n            end=\"\\n\" if self.completed == self.total else \"\\r\",\r\n            flush=True,\r\n        )\r\n\r\n    def close(self) -> None:\r\n        if self.bar is not None:\r\n            self.bar.close()\r\n\r\n\r\ndef _parse_datetime(value: Any) -> pd.Timestamp:\r\n    parsed = pd.to_datetime(value, dayfirst=True)\r\n    if pd.isna(parsed):\r\n        raise ValueError(f\"Invalid market-data timestamp: {value!r}\")\r\n    timestamp = pd.Timestamp(parsed)\r\n    if timestamp.tzinfo is None:\r\n        return timestamp.tz_localize(TIMEZONE)\r\n    return timestamp.tz_convert(TIMEZONE)\r\n\r\n\r\ndef _parse_datetimes(values: pd.Series) -> pd.Series:\r\n    if values.empty or not (\r\n        pd.api.types.is_object_dtype(values.dtype)\r\n        or isinstance(values.dtype, pd.StringDtype)\r\n    ):\r\n        return values.map(_parse_datetime)\r\n    try:\r\n        standard = values.str.fullmatch(\r\n            r\"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}\",\r\n            na=False,\r\n        ).fillna(False).astype(bool)\r\n    except AttributeError:\r\n        return values.map(_parse_datetime)\r\n    if not standard.all():\r\n        return values.map(_parse_datetime)\r\n    parsed = pd.to_datetime(\r\n        values, format=\"%d/%m/%Y %H:%M:%S\", errors=\"coerce\"\r\n    ).dt.tz_localize(TIMEZONE)\r\n    if parsed.isna().any():\r\n        return values.map(_parse_datetime)\r\n    return parsed\r\n\r\n\r\ndef _instrument_symbol(config: dict[str, Any]) -> str:\r\n    instrument = config.get(\"instrument\") or {}\r\n    if isinstance(instrument, dict):\r\n        symbol = instrument.get(\"symbol\") or instrument.get(\"underlying\")\r\n        if symbol:\r\n            return str(symbol).upper()\r\n    return \"SENSEX\"\r\n\r\n\r\ndef _lot_size(config: dict[str, Any], symbol: str) -> int:\r\n    parameters = dict(config.get(\"parameters\") or {})\r\n    execution = dict(config.get(\"execution\") or {})\r\n    value = parameters.get(\"lot_size\", execution.get(\"lot_size\"))\r\n    if value is not None:\r\n        lot_size = int(value)\r\n        if lot_size <= 0:\r\n            raise ValueError(\"lot_size must be positive\")\r\n        return lot_size\r\n    return DEFAULT_LOT_SIZE_BY_SYMBOL.get(symbol.upper(), 1)\r\n\r\n\r\ndef _period(config: dict[str, Any]) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:\r\n    period = dict(config.get(\"period\") or {})\r\n    start = period.get(\"start_date\") or period.get(\"start\")\r\n    end = period.get(\"end_date\") or period.get(\"end\")\r\n    start_ts = pd.Timestamp(start).date() if start else None\r\n    end_ts = pd.Timestamp(end).date() if end else None\r\n    return start_ts, end_ts\r\n\r\n\r\ndef _resolve_data_paths(selected: Path, symbol: str) -> tuple[Path, Path]:\r\n    selected = Path(selected)\r\n    prefix = symbol.lower()\r\n    summary_names = (\r\n        f\"{prefix}_summary.parquet\",\r\n        \"nifty_summary.parquet\",\r\n        \"sensex_summary.parquet\",\r\n        \"summary.parquet\",\r\n    )\r\n    chain_names = (f\"{prefix}_chain\", \"nifty_chain\", \"sensex_chain\", \"chain\")\r\n\r\n    if selected.is_file():\r\n        summary = selected if \"summary\" in selected.stem.lower() else None\r\n        if summary is None:\r\n            summary = next((selected.parent / name for name in summary_names if (selected.parent / name).exists()), None)\r\n        chain = selected if selected.suffix.lower() == \".parquet\" and \"summary\" not in selected.stem.lower() else None\r\n        if chain is None:\r\n            chain = next((selected.parent / name for name in chain_names if (selected.parent / name).exists()), None)\r\n    elif selected.is_dir():\r\n        if selected.name.lower() in set(chain_names):\r\n            chain = selected\r\n            summary = next((selected.parent / name for name in summary_names if (selected.parent / name).exists()), None)\r\n        else:\r\n            summary = next((selected / name for name in summary_names if (selected / name).exists()), None)\r\n            chain = next((selected / name for name in chain_names if (selected / name).exists()), None)\r\n    else:\r\n        raise FileNotFoundError(f\"Selected market data does not exist: {selected}\")\r\n\r\n    if summary is None or chain is None:\r\n        raise ValueError(\r\n            f\"Selected market data must provide summary and chain data; \"\r\n            f\"resolved summary={summary!r}, chain={chain!r}\"\r\n        )\r\n    return Path(summary), Path(chain)\r\n\r\n\r\ndef _add_time_columns(frame: pd.DataFrame) -> pd.DataFrame:\r\n    out = frame.copy()\r\n    out[\"dt\"] = _parse_datetimes(out[\"datetime\"])\r\n    out[\"date\"] = out[\"dt\"].dt.date\r\n    out[\"time\"] = out[\"dt\"].dt.strftime(\"%H:%M:%S\")\r\n    return out\r\n\r\n\r\ndef _parquet_sources(path: Path) -> list[Path]:\r\n    return [path] if path.is_file() else sorted(path.rglob(\"*.parquet\"))\r\n\r\n\r\ndef _read_frame(loader, sources: list[Path], key: tuple[Any, ...], build):\r\n    if callable(getattr(loader, \"read_frame\", None)):\r\n        return loader.read_frame(sources, key, build)\r\n    return build()\r\n\r\n\r\ndef _market_sources(selected: Path, symbol: str) -> list[Path]:\r\n    summary_path, chain_path = _resolve_data_paths(selected, symbol)\r\n    return _parquet_sources(summary_path) + _parquet_sources(chain_path)\r\n\r\n\r\ndef _load_market_data(\r\n    selected: Path,\r\n    symbol: str,\r\n    loader=None,\r\n    start_date=None,\r\n    end_date=None,\r\n) -> tuple[pd.DataFrame, pd.DataFrame]:\r\n    summary_path, chain_path = _resolve_data_paths(selected, symbol)\r\n    summary_columns = [\r\n        \"datetime\", \"DTE\", \"future_close\", \"future_atm\", \"straddle_future\",\r\n        \"synth_atm\", \"straddle_synth\",\r\n    ]\r\n    chain_columns = [\"datetime\", \"strike\", \"ce_close\", \"pe_close\", \"DTE\"]\r\n    summary = _read_frame(\r\n        loader,\r\n        _parquet_sources(summary_path),\r\n        (\"fixed-094559-summary-v1\", str(summary_path), tuple(summary_columns)),\r\n        lambda: pd.read_parquet(summary_path, columns=summary_columns),\r\n    )\r\n    chain = _read_frame(\r\n        loader,\r\n        _parquet_sources(chain_path),\r\n        (\"fixed-094559-chain-v1\", str(chain_path), tuple(chain_columns)),\r\n        lambda: pd.read_parquet(chain_path, columns=chain_columns),\r\n    )\r\n\r\n    summary = _add_time_columns(summary)\r\n    summary = summary[summary[\"DTE\"].eq(0)].copy()\r\n    if start_date is not None:\r\n        summary = summary[summary[\"date\"] >= start_date].copy()\r\n    if end_date is not None:\r\n        summary = summary[summary[\"date\"] <= end_date].copy()\r\n    summary[\"entry_atm\"] = summary[\"synth_atm\"].where(\r\n        summary[\"synth_atm\"].notna(), summary[\"future_atm\"]\r\n    )\r\n    summary[\"entry_straddle\"] = summary[\"straddle_synth\"].where(\r\n        summary[\"straddle_synth\"].notna(), summary[\"straddle_future\"]\r\n    )\r\n    summary = summary[\r\n        summary[\"future_close\"].notna()\r\n        & summary[\"entry_atm\"].notna()\r\n        & summary[\"entry_straddle\"].notna()\r\n    ].copy()\r\n    if summary.empty:\r\n        raise ValueError(\"The selected dataset has no valid DTE=0 summary rows\")\r\n    if summary.duplicated(\"dt\").any():\r\n        raise ValueError(\"Summary data contains duplicate timestamps\")\r\n    summary[\"entry_atm\"] = summary[\"entry_atm\"].astype(int)\r\n    summary = summary.sort_values(\"dt\").reset_index(drop=True)\r\n\r\n    chain = _add_time_columns(chain)\r\n    chain = chain[chain[\"DTE\"].eq(0)].copy()\r\n    wanted = set(summary[\"dt\"])\r\n    chain = chain[chain[\"dt\"].isin(wanted)].copy()\r\n    chain[\"strike\"] = chain[\"strike\"].astype(int)\r\n    if chain.duplicated([\"dt\", \"strike\"]).any():\r\n        chain = chain.groupby([\"dt\", \"date\", \"time\", \"strike\"], as_index=False)[\r\n            [\"ce_close\", \"pe_close\"]\r\n        ].max()\r\n    if chain.empty:\r\n        raise ValueError(\"The selected dataset has no matching DTE=0 chain rows\")\r\n    return summary, chain\r\n\r\n\r\ndef _load_days(\r\n    selected: Path,\r\n    symbol: str,\r\n    loader=None,\r\n    start_date=None,\r\n    end_date=None,\r\n) -> list[dict[str, Any]]:\r\n    sources = _market_sources(selected, symbol)\r\n\r\n    def build() -> list[dict[str, Any]]:\r\n        summary, chain = _load_market_data(\r\n            selected,\r\n            symbol,\r\n            loader,\r\n            start_date=start_date,\r\n            end_date=end_date,\r\n        )\r\n        return _build_days(summary, chain, tuple(float(value) for value in PREMIUM_PRICES))\r\n\r\n    shared_object = getattr(loader, \"shared_object\", None)\r\n    if callable(shared_object):\r\n        return shared_object(\r\n            sources,\r\n            (\r\n                \"sensex-absolute-premium-days-v3\",\r\n                symbol,\r\n                str(start_date),\r\n                str(end_date),\r\n                ENTRY_START,\r\n                EXIT_TIME,\r\n                PREMIUM_PRICES,\r\n            ),\r\n            build,\r\n        )\r\n    return build()\r\n\r\n\r\ndef _clock_seconds(value: str) -> int:\r\n    hour, minute, second = (int(part) for part in str(value).split(\":\")[:3])\r\n    return hour * 3600 + minute * 60 + second\r\n\r\n\r\ndef _precompute_option_selections(\r\n    strikes: np.ndarray,\r\n    ce: np.ndarray,\r\n    pe: np.ndarray,\r\n    atm: np.ndarray,\r\n    premium_prices: tuple[float, ...],\r\n) -> dict[float, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:\r\n    \"\"\"Precompute the reference selection rule for every grid premium and bar.\"\"\"\r\n    selections = {}\r\n    for premium_price in premium_prices:\r\n        sides = {}\r\n        for option_type, quotes in ((\"CE\", ce), (\"PE\", pe)):\r\n            columns = np.full(len(atm), -1, dtype=np.int32)\r\n            selected_strikes = np.full(len(atm), -1, dtype=np.int32)\r\n            selected_quotes = np.full(len(atm), np.nan, dtype=np.float64)\r\n            for index in range(len(atm)):\r\n                if option_type == \"CE\":\r\n                    valid = (\r\n                        (strikes >= atm[index])\r\n                        & np.isfinite(quotes[index])\r\n                        & (quotes[index] > 0)\r\n                    )\r\n                else:\r\n                    valid = (\r\n                        (strikes <= atm[index])\r\n                        & np.isfinite(quotes[index])\r\n                        & (quotes[index] > 0)\r\n                    )\r\n                candidates = np.flatnonzero(valid)\r\n                if not len(candidates):\r\n                    continue\r\n                score = (\r\n                    np.abs(quotes[index, candidates] - premium_price)\r\n                    + np.abs(strikes[candidates] - atm[index]) * 1e-9\r\n                )\r\n                column = int(candidates[int(np.argmin(score))])\r\n                columns[index] = column\r\n                selected_strikes[index] = int(strikes[column])\r\n                selected_quotes[index] = float(quotes[index, column])\r\n            columns.flags.writeable = False\r\n            selected_strikes.flags.writeable = False\r\n            selected_quotes.flags.writeable = False\r\n            sides[option_type] = (columns, selected_strikes, selected_quotes)\r\n        selections[float(premium_price)] = sides\r\n    return selections\r\n\r\n\r\ndef _build_days(\r\n    summary: pd.DataFrame,\r\n    chain: pd.DataFrame,\r\n    premium_prices: tuple[float, ...],\r\n) -> list[dict[str, Any]]:\r\n    days: list[dict[str, Any]] = []\r\n    start_seconds = _clock_seconds(ENTRY_START)\r\n    exit_seconds = _clock_seconds(EXIT_TIME)\r\n    for date_value, summary_day in summary.groupby(\"date\", sort=True):\r\n        summary_day = summary_day.sort_values(\"dt\").reset_index(drop=True)\r\n        chain_day = chain[chain[\"date\"].eq(date_value)]\r\n        times = summary_day[\"time\"].tolist()\r\n        timestamps = summary_day[\"dt\"].tolist()\r\n        seconds = np.array([_clock_seconds(t) for t in times], dtype=int)\r\n        start_i = next((i for i, value in enumerate(seconds) if value >= start_seconds), None)\r\n        if start_i is None:\r\n            continue\r\n        exit_i = next((i for i, value in enumerate(seconds) if value >= exit_seconds), len(seconds) - 1)\r\n        if exit_i < start_i:\r\n            continue\r\n        strikes = np.sort(chain_day[\"strike\"].unique().astype(int))\r\n        if not len(strikes):\r\n            continue\r\n        ce = chain_day.pivot_table(\r\n            index=\"time\", columns=\"strike\", values=\"ce_close\", aggfunc=\"max\"\r\n        ).reindex(index=times, columns=strikes).to_numpy(float)\r\n        pe = chain_day.pivot_table(\r\n            index=\"time\", columns=\"strike\", values=\"pe_close\", aggfunc=\"max\"\r\n        ).reindex(index=times, columns=strikes).to_numpy(float)\r\n        ce_ff = pd.DataFrame(ce).ffill().to_numpy()\r\n        pe_ff = pd.DataFrame(pe).ffill().to_numpy()\r\n        atm = summary_day[\"entry_atm\"].to_numpy(int)\r\n        straddle = summary_day[\"entry_straddle\"].to_numpy(float)\r\n        selections = _precompute_option_selections(\r\n            strikes, ce, pe, atm, premium_prices\r\n        )\r\n        days.append({\r\n            \"date\": date_value,\r\n            \"timestamps\": timestamps,\r\n            \"times\": times,\r\n            \"seconds\": seconds,\r\n            \"start_i\": start_i,\r\n            \"exit_i\": exit_i,\r\n            \"atm\": atm,\r\n            \"straddle\": straddle,\r\n            \"strikes\": strikes,\r\n            \"ce\": ce,\r\n            \"pe\": pe,\r\n            \"ce_ff\": ce_ff,\r\n            \"pe_ff\": pe_ff,\r\n            \"selections\": selections,\r\n        })\r\n    if not days:\r\n        raise ValueError(\"No tradable DTE=0 expiry days were found\")\r\n    return days\r\n\r\n\r\ndef _leg_quote(day: dict[str, Any], option_type: str, column: int, index: int) -> float:\r\n    values = day[\"ce\"] if option_type == \"CE\" else day[\"pe\"]\r\n    value = values[index, column]\r\n    return float(value) if np.isfinite(value) and value > 0 else float(\"nan\")\r\n\r\n\r\ndef _leg_mark(day: dict[str, Any], option_type: str, column: int, index: int) -> float:\r\n    values = day[\"ce_ff\"] if option_type == \"CE\" else day[\"pe_ff\"]\r\n    value = values[index, column]\r\n    return float(value) if np.isfinite(value) and value > 0 else float(\"nan\")\r\n\r\n\r\ndef _select_leg(\r\n    day: dict[str, Any],\r\n    option_type: str,\r\n    index: int,\r\n    premium_price: float,\r\n) -> dict[str, Any] | None:\r\n    prepared = day[\"selections\"].get(float(premium_price), {}).get(option_type)\r\n    if prepared is not None:\r\n        columns, strikes, quotes = prepared\r\n        column = int(columns[index])\r\n        if column < 0:\r\n            return None\r\n        return {\r\n            \"option_type\": option_type,\r\n            \"strike\": int(strikes[index]),\r\n            \"column\": column,\r\n            \"entry_quote\": float(quotes[index]),\r\n        }\r\n\r\n    # Preserve exact behavior for an explicitly supplied non-grid value.\r\n    target = float(premium_price)\r\n    atm = int(day[\"atm\"][index])\r\n    strikes = day[\"strikes\"]\r\n    quotes = day[\"ce\"][index] if option_type == \"CE\" else day[\"pe\"][index]\r\n    if option_type == \"CE\":\r\n        mask = (strikes >= atm) & np.isfinite(quotes) & (quotes > 0)\r\n    else:\r\n        mask = (strikes <= atm) & np.isfinite(quotes) & (quotes > 0)\r\n    indexes = np.where(mask)[0]\r\n    if not len(indexes):\r\n        return None\r\n    score = np.abs(quotes[indexes] - target) + np.abs(strikes[indexes] - atm) * 1e-9\r\n    column = int(indexes[int(np.argmin(score))])\r\n    return {\r\n        \"option_type\": option_type,\r\n        \"strike\": int(strikes[column]),\r\n        \"column\": column,\r\n        \"entry_quote\": float(quotes[column]),\r\n    }\r\n\r\n\r\ndef _entry_fill(quote: float) -> float:\r\n    return quote * (1.0 - SLIPPAGE)\r\n\r\n\r\ndef _exit_fill(quote: float) -> float:\r\n    return quote * (1.0 + SLIPPAGE)\r\n\r\n\r\ndef _leg_unrealized(leg: dict[str, Any], day: dict[str, Any], index: int, lot_size: int) -> float:\r\n    if leg[\"status\"] != \"active\":\r\n        return 0.0\r\n    mark = _leg_mark(day, leg[\"option_type\"], leg[\"column\"], index)\r\n    if not np.isfinite(mark):\r\n        return 0.0\r\n    return (_entry_fill(float(leg[\"entry_quote\"])) - _exit_fill(mark)) * lot_size\r\n\r\n\r\ndef _trade_row(\r\n    day: dict[str, Any],\r\n    leg: dict[str, Any],\r\n    cycle_no: int,\r\n    exit_i: int,\r\n    reason: str,\r\n    run_id: str,\r\n    lot_size: int,\r\n    symbol: str,\r\n) -> dict[str, Any]:\r\n    mark = _leg_mark(day, leg[\"option_type\"], leg[\"column\"], exit_i)\r\n    if not np.isfinite(mark):\r\n        raise ValueError(\"Open leg has no usable exit quote at the closing timestamp\")\r\n    batch = f\"{day['date'].isoformat()}-cycle{cycle_no}\"\r\n    entry_seq = int(leg[\"entry_seq\"])\r\n    option_type = str(leg[\"option_type\"])\r\n    return {\r\n        \"run_id\": run_id,\r\n        \"trade_id\": f\"{batch}-{option_type}-{entry_seq}\",\r\n        \"batch_id\": batch,\r\n        \"leg_id\": f\"{option_type}-{entry_seq}\",\r\n        \"strategy\": STRATEGY_NAME,\r\n        \"symbol\": f\"{symbol}_DTE0_{option_type}_{int(leg['strike'])}\",\r\n        \"side\": \"SHORT\",\r\n        \"entry_time\": day[\"timestamps\"][int(leg[\"entry_i\"])].isoformat(),\r\n        \"exit_time\": day[\"timestamps\"][exit_i].isoformat(),\r\n        \"quantity\": lot_size,\r\n        \"entry_price\": _entry_fill(float(leg[\"entry_quote\"])),\r\n        \"exit_price\": _exit_fill(mark),\r\n        \"multiplier\": 1,\r\n        \"fees\": 0.0,\r\n    }\r\n\r\n\r\ndef _run_day(\r\n    day: dict[str, Any],\r\n    run_id: str,\r\n    lot_size: int,\r\n    symbol: str,\r\n    starting_realized: float,\r\n    settings: dict[str, Any],\r\n) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:\r\n    completed: list[dict[str, Any]] = []\r\n    snapshots: list[dict[str, Any]] = []\r\n    realized = float(starting_realized)\r\n    entry_seq = 0\r\n    cycle_no = 0\r\n    active_cycle: dict[str, Any] | None = None\r\n    pending_new_cycle = True\r\n\r\n    baseline_time = day[\"timestamps\"][0] - timedelta(seconds=1)\r\n    snapshots.append({\r\n        \"timestamp\": baseline_time.isoformat(),\r\n        \"realized_pnl\": realized,\r\n        \"unrealized_pnl\": 0.0,\r\n    })\r\n\r\n    def active_legs() -> list[dict[str, Any]]:\r\n        if active_cycle is None:\r\n            return []\r\n        return [leg for leg in active_cycle[\"legs\"].values() if leg[\"status\"] == \"active\"]\r\n\r\n    def cycle_realized() -> float:\r\n        if active_cycle is None:\r\n            return 0.0\r\n        return realized - float(active_cycle[\"start_realized\"])\r\n\r\n    def unrealized(index: int) -> float:\r\n        return float(sum(_leg_unrealized(leg, day, index, lot_size) for leg in active_legs()))\r\n\r\n    premium_price = float(settings[\"premium_price\"])\r\n    entry_start = str(settings[\"entry_start\"])\r\n    entry_start_seconds = _clock_seconds(entry_start)\r\n    run_start_i = next((i for i, value in enumerate(day[\"seconds\"]) if value >= entry_start_seconds), None)\r\n    if run_start_i is None or run_start_i >= int(day[\"exit_i\"]):\r\n        return completed, snapshots, realized\r\n    leg_sl_pct = float(settings[\"leg_sl_pct\"])\n    leg_tp_pct = float(settings[\"leg_tp_pct\"])\n    overall_reentries = int(settings[\"overall_reentries\"])\r\n    exit_i = int(day[\"exit_i\"])\r\n\r\n    def open_leg(option_type: str, index: int, reentry_count: int = 0) -> dict[str, Any] | None:\r\n        nonlocal entry_seq\r\n        selected = _select_leg(day, option_type, index, premium_price)\r\n        if selected is None:\r\n            return None\r\n        entry_seq += 1\r\n        selected.update({\r\n            \"status\": \"active\",\r\n            \"entry_i\": index,\r\n            \"entry_seq\": entry_seq,\r\n            \"reentry_count\": reentry_count,\r\n        })\r\n        return selected\r\n\r\n    def open_cycle(index: int) -> bool:\r\n        nonlocal active_cycle, cycle_no, pending_new_cycle\r\n        ce = open_leg(\"CE\", index)\r\n        pe = open_leg(\"PE\", index)\r\n        if ce is None or pe is None:\r\n            return False\r\n        cycle_no += 1\r\n        active_cycle = {\r\n            \"cycle_no\": cycle_no,\r\n            \"start_realized\": realized,\r\n            \"legs\": {\"CE\": ce, \"PE\": pe},\r\n        }\r\n        pending_new_cycle = False\r\n        return True\r\n\r\n    def close_leg(leg: dict[str, Any], index: int, reason: str) -> None:\r\n        nonlocal realized\r\n        pnl = _leg_unrealized(leg, day, index, lot_size)\r\n        realized += pnl\r\n        leg[\"status\"] = \"closed\"\r\n        completed.append(\r\n            _trade_row(\r\n                day,\r\n                leg,\r\n                int(active_cycle[\"cycle_no\"]),\r\n                index,\r\n                reason,\r\n                run_id,\r\n                lot_size,\r\n                symbol,\r\n            )\r\n        )\r\n\r\n    def close_cycle(index: int, reason: str) -> None:\r\n        nonlocal active_cycle, pending_new_cycle\r\n        for leg in list(active_legs()):\r\n            close_leg(leg, index, reason)\r\n        active_cycle = None\r\n        pending_new_cycle = cycle_no <= overall_reentries\r\n\r\n    for index in range(len(day[\"times\"])):\r\n        if index > exit_i:\r\n            break\r\n        if index < int(run_start_i):\r\n            continue\r\n\r\n        if active_cycle is None and pending_new_cycle and index < exit_i:\r\n            open_cycle(index)\r\n\r\n        if active_cycle is not None:\r\n            for option_type, leg in list(active_cycle[\"legs\"].items()):\r\n                if leg[\"status\"] != \"active\":\r\n                    continue\r\n                mark = _leg_mark(day, option_type, int(leg[\"column\"]), index)\r\n                if not np.isfinite(mark):\r\n                    continue\r\n                entry_quote = float(leg[\"entry_quote\"])\n                if mark >= entry_quote * (1.0 + leg_sl_pct / 100.0):\n                    close_leg(leg, index, \"LEG_SL\")\n                elif mark <= entry_quote * (1.0 - leg_tp_pct / 100.0):\n                    close_leg(leg, index, \"LEG_TP\")\n\n            if active_cycle is not None:\n                # A cycle remains open while either leg remains open. Once\n                # both independent legs have exited, the shared cycle-level\n                # re-entry budget permits the next cycle on the next bar.\n                if not active_legs():\n                    close_cycle(index, \"LEG_EXITS_COMPLETE\")\n\r\n        if index == exit_i and active_cycle is not None:\r\n            close_cycle(index, \"EOD\")\r\n\r\n        snapshots.append({\r\n            \"timestamp\": day[\"timestamps\"][index].isoformat(),\r\n            \"realized_pnl\": float(realized),\r\n            \"unrealized_pnl\": unrealized(index),\r\n        })\r\n\r\n    return completed, snapshots, realized\r\n\r\n\r\ndef _dedupe_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:\r\n    ordered = sorted(snapshots, key=lambda item: item[\"timestamp\"])\r\n    deduped: list[dict[str, Any]] = []\r\n    seen: set[str] = set()\r\n    for snapshot in ordered:\r\n        if snapshot[\"timestamp\"] in seen:\r\n            continue\r\n        deduped.append(snapshot)\r\n        seen.add(snapshot[\"timestamp\"])\r\n    if any(a[\"timestamp\"] >= b[\"timestamp\"] for a, b in zip(deduped, deduped[1:])):\r\n        raise RuntimeError(\"Equity snapshot timestamps are not strictly increasing\")\r\n    return deduped\r\n\r\n\r\ndef run_strategy(context):\r\n    \"\"\"Run one exhaustive-grid mapping using the dashboard-selected dataset.\"\"\"\r\n    config = dict(context.config or {})\r\n    parameters = dict(config.get(\"parameters\") or {})\r\n    unknown = set(parameters) - ALLOWED_PARAMETERS\r\n    if unknown:\r\n        raise ValueError(f\"Unsupported strategy parameters: {sorted(unknown)}\")\r\n    missing = SWEEP_PARAMETER_NAMES - set(parameters)\r\n    if missing:\r\n        raise ValueError(f\"Missing sweep parameters: {sorted(missing)}\")\r\n    settings = {\r\n        \"premium_price\": float(parameters[\"premium_price\"]),\r\n        \"entry_start\": str(parameters[\"entry_start\"]),\r\n        \"leg_sl_pct\": float(parameters[\"leg_sl_pct\"]),\n        \"leg_tp_pct\": float(parameters[\"leg_tp_pct\"]),\n        \"overall_reentries\": int(parameters[\"overall_reentries\"]),\r\n    }\r\n    if settings[\"premium_price\"] <= 0:\r\n        raise ValueError(\"premium_price must be positive\")\r\n    if settings[\"leg_sl_pct\"] <= 0:\n        raise ValueError(\"leg_sl_pct must be positive\")\n    if settings[\"leg_tp_pct\"] <= 0 or settings[\"leg_tp_pct\"] > 100:\n        raise ValueError(\"leg_tp_pct must be between 0 and 100\")\n    if settings[\"overall_reentries\"] < 0:\r\n        raise ValueError(\"overall_reentries cannot be negative\")\r\n    if settings[\"entry_start\"] not in ENTRY_TIMES:\r\n        raise ValueError(\"entry_start must be a five-minute grid time from 09:30:00 to 11:00:00\")\r\n\r\n    symbol = _instrument_symbol(config)\r\n    lot_size = _lot_size(config, symbol)\r\n    start_date, end_date = _period(config)\r\n    market_data_loader = getattr(context, \"market_data_loader\", None)\r\n    days = _load_days(\r\n        Path(context.market_data),\r\n        symbol,\r\n        market_data_loader,\r\n        start_date=start_date,\r\n        end_date=end_date,\r\n    )\r\n\r\n    completed: list[dict[str, Any]] = []\r\n    snapshots: list[dict[str, Any]] = []\r\n    realized = 0.0\r\n    progress = _DayProgress(len(days), getattr(context, \"report_progress\", None))\r\n    try:\r\n        for day in days:\r\n            day_trades, day_snapshots, realized = _run_day(\r\n                day,\r\n                str(context.run_id),\r\n                lot_size,\r\n                symbol,\r\n                realized,\r\n                settings,\r\n            )\r\n            completed.extend(day_trades)\r\n            snapshots.extend(day_snapshots)\r\n            progress.update()\r\n    finally:\r\n        progress.close()\r\n\r\n    equity_snapshots = _dedupe_snapshots(snapshots)\r\n    if equity_snapshots and abs(float(equity_snapshots[-1][\"unrealized_pnl\"])) > 0.01:\r\n        raise RuntimeError(\"Final unrealized P&L is not zero; open legs remain\")\r\n\r\n    return {\r\n        \"completed_trades\": completed,\r\n        \"completed_trade_count\": len(completed),\r\n        \"equity_snapshots\": equity_snapshots or None,\r\n        \"metadata\": {\r\n            \"strategy_name\": STRATEGY_NAME,\r\n            \"engine\": \"dashboard-compatible deterministic bar engine\",\r\n            \"data_rule\": \"context.market_data, current-expiry DTE=0\",\r\n            \"sweep_combinations\": SWEEP_COMBINATION_COUNT,\r\n            \"entry_time\": settings[\"entry_start\"],\r\n            \"exit_time\": EXIT_TIME,\r\n            \"strike_rule\": (\r\n                f\"short CE/PE closest to Rs {settings['premium_price']:g} option premium\"\r\n            ),\r\n            \"leg_stop_loss\": f\"{settings['leg_sl_pct']:g}% per leg\",\n            \"leg_take_profit\": f\"{settings['leg_tp_pct']:g}% per leg\",\n            \"leg_reentries\": 0,\n            \"overall_reentries\": settings[\"overall_reentries\"],\r\n            \"slippage\": \"1.0% at entry and exit\",\n            \"lot_size\": lot_size,\r\n            \"margin_basis_rs\": MARGIN_BASIS_RS,\r\n            \"drawdown_note\": \"equity snapshots are quote-marked observations; dashboard calculates analytics\",\r\n        },\r\n    }\r\n\r\n\r\ndef run_databricks_sweep(\n    spark,\r\n    summary_table=\"workspace.default.sensex_summary\",\r\n    chain_table=\"workspace.default.sensex_chain\",\r\n    start_date=None,\r\n    end_date=None,\r\n    lot_size=20,\r\n    max_combinations=None,\r\n):\r\n    \"\"\"Run the exhaustive sweep directly against Databricks Unity Catalog tables.\r\n\r\n    The table schemas must contain the same columns as the dashboard parquet\r\n    inputs.  The option-chain table is required because strike selection and\r\n    stop/target evaluation use intraday CE/PE quotes.\r\n    \"\"\"\r\n    summary_columns = [\r\n        \"datetime\", \"DTE\", \"future_close\", \"future_atm\", \"straddle_future\",\r\n        \"synth_atm\", \"straddle_synth\",\r\n    ]\r\n    chain_columns = [\"datetime\", \"strike\", \"ce_close\", \"pe_close\", \"DTE\"]\r\n\r\n    summary = _spark_to_pandas(spark.table(summary_table).select(*summary_columns))\n    chain = _spark_to_pandas(spark.table(chain_table).select(*chain_columns))\n    if summary.empty or chain.empty:\r\n        raise ValueError(\"The Databricks summary or chain table is empty\")\r\n\r\n    summary = _add_time_columns(summary)\r\n    summary = summary[summary[\"DTE\"].eq(0)].copy()\r\n    chain = _add_time_columns(chain)\r\n    chain = chain[chain[\"DTE\"].eq(0)].copy()\r\n\r\n    if start_date is not None:\r\n        start_date = pd.Timestamp(start_date).date()\r\n        summary = summary[summary[\"date\"] >= start_date].copy()\r\n    if end_date is not None:\r\n        end_date = pd.Timestamp(end_date).date()\r\n        summary = summary[summary[\"date\"] <= end_date].copy()\r\n\r\n    summary[\"entry_atm\"] = summary[\"synth_atm\"].where(\r\n        summary[\"synth_atm\"].notna(), summary[\"future_atm\"]\r\n    )\r\n    summary[\"entry_straddle\"] = summary[\"straddle_synth\"].where(\r\n        summary[\"straddle_synth\"].notna(), summary[\"straddle_future\"]\r\n    )\r\n    summary = summary[\r\n        summary[\"future_close\"].notna()\r\n        & summary[\"entry_atm\"].notna()\r\n        & summary[\"entry_straddle\"].notna()\r\n    ].copy()\r\n    if summary.empty:\r\n        raise ValueError(\"No valid DTE=0 summary rows remain after filtering\")\r\n    wanted = set(summary[\"dt\"])\r\n    chain = chain[chain[\"dt\"].isin(wanted)].copy()\r\n    if chain.empty:\r\n        raise ValueError(\"No matching DTE=0 chain rows remain after filtering\")\r\n\r\n    days = _build_days(summary, chain, tuple(float(x) for x in PREMIUM_PRICES))\r\n    parameter_sets = SWEEP_PARAMETER_SETS\r\n    if max_combinations is not None:\r\n        parameter_sets = parameter_sets[: int(max_combinations)]\r\n    results = []\r\n    total = len(parameter_sets)\r\n    for run_number, parameters in enumerate(parameter_sets, start=1):\r\n        settings = {\r\n            \"premium_price\": float(parameters[\"premium_price\"]),\r\n            \"entry_start\": str(parameters[\"entry_start\"]),\n            \"leg_sl_pct\": float(parameters[\"leg_sl_pct\"]),\n            \"leg_tp_pct\": float(parameters[\"leg_tp_pct\"]),\n            \"overall_reentries\": int(parameters[\"overall_reentries\"]),\r\n        }\r\n        completed = []\r\n        realized = 0.0\r\n        for day in days:\r\n            day_trades, _, realized = _run_day(\r\n                day, f\"databricks-{run_number}\", int(lot_size), \"SENSEX\",\r\n                realized, settings,\r\n            )\r\n            completed.extend(day_trades)\r\n\r\n        trade_pnl = [\r\n            (float(row[\"entry_price\"]) - float(row[\"exit_price\"]))\r\n            * float(row[\"quantity\"]) * float(row.get(\"multiplier\", 1.0))\r\n            - float(row.get(\"fees\", 0.0))\r\n            for row in completed\r\n        ]\r\n        results.append({\r\n            **parameters,\r\n            \"status\": \"succeeded\" if completed else \"no_trades\",\r\n            \"completed_trade_count\": len(completed),\r\n            \"net_pnl\": float(sum(trade_pnl)),\r\n            \"max_loss\": float(min(trade_pnl)) if trade_pnl else None,\r\n            \"win_rate\": float(sum(p > 0 for p in trade_pnl) / len(trade_pnl)) if trade_pnl else None,\r\n        })\r\n        if run_number == 1 or run_number % 100 == 0 or run_number == total:\r\n            print(f\"Completed {run_number}/{total} combinations\")\r\n\r\n    return pd.DataFrame(results)\n\n\ndef _fast_parameter_matrix(parameter_sets) -> np.ndarray:\n    matrix = np.empty((len(parameter_sets), 5), dtype=np.float64)\n    premium_index = {float(value): index for index, value in enumerate(PREMIUM_PRICES)}\n    for row_index, parameters in enumerate(parameter_sets):\n        premium = float(parameters[\"premium_price\"])\n        if premium not in premium_index:\n            raise ValueError(f\"Unsupported premium_price: {premium}\")\n        matrix[row_index] = (\n            premium_index[premium],\n            float(parameters[\"leg_sl_pct\"]),\n            float(parameters[\"leg_tp_pct\"]),\n            float(parameters[\"overall_reentries\"]),\n            float(_clock_seconds(parameters[\"entry_start\"])),\n        )\n    return np.ascontiguousarray(matrix)\n\n\ndef _fast_numeric_data(days: list[dict[str, Any]]) -> dict[str, Any]:\n    \"\"\"Flatten prepared days into compact arrays shared by every sweep row.\"\"\"\n    premium_count = len(PREMIUM_PRICES)\n    event_count = sum(len(day[\"times\"]) for day in days)\n    max_strikes = max(len(day[\"strikes\"]) for day in days)\n    day_offsets = [0]\n    exit_indices = []\n    seconds = np.empty(event_count, dtype=np.int32)\n    selected_columns = np.full((premium_count, 2, event_count), -1, dtype=np.int32)\n    quotes = np.full((2, event_count, max_strikes), np.nan, dtype=np.float64)\n    quotes_ff = np.full((2, event_count, max_strikes), np.nan, dtype=np.float64)\n\n    offset = 0\n    for day in days:\n        count = len(day[\"times\"])\n        seconds[offset:offset + count] = day[\"seconds\"]\n        width = len(day[\"strikes\"])\n        quotes[0, offset:offset + count, :width] = day[\"ce\"]\n        quotes[1, offset:offset + count, :width] = day[\"pe\"]\n        quotes_ff[0, offset:offset + count, :width] = day[\"ce_ff\"]\n        quotes_ff[1, offset:offset + count, :width] = day[\"pe_ff\"]\n        for premium_index, premium in enumerate(PREMIUM_PRICES):\n            for side_index, side in enumerate((\"CE\", \"PE\")):\n                columns, _, _ = day[\"selections\"][float(premium)][side]\n                selected_columns[premium_index, side_index, offset:offset + count] = columns\n        offset += count\n        day_offsets.append(offset)\n        exit_indices.append(int(day[\"exit_i\"]))\n\n    return {\n        \"day_offsets\": np.asarray(day_offsets, dtype=np.int64),\n        \"exit_indices\": np.asarray(exit_indices, dtype=np.int64),\n        \"seconds\": np.ascontiguousarray(seconds),\n        \"selected_columns\": np.ascontiguousarray(selected_columns),\n        \"quotes\": np.ascontiguousarray(quotes),\n        \"quotes_ff\": np.ascontiguousarray(quotes_ff),\n    }\n\n\nif njit is not None:\n    @njit(cache=True, parallel=True)\n    def _fast_kernel(parameters, day_offsets, exit_indices, seconds, selected_columns, quotes, quotes_ff, lot_size, slippage):\n        results = np.full((parameters.shape[0], 7), np.nan, dtype=np.float64)\n        for parameter_index in prange(parameters.shape[0]):\n            premium_index = int(parameters[parameter_index, 0])\n            leg_sl = parameters[parameter_index, 1] / 100.0\n            leg_tp = parameters[parameter_index, 2] / 100.0\n            reentries = int(parameters[parameter_index, 3])\n            entry_seconds = int(parameters[parameter_index, 4])\n            realized = 0.0\n            minimum_trade = 0.0\n            trade_count = 0\n            win_count = 0\n            loss_streak = 0\n            maximum_consecutive_losses = 0\n            peak_equity = 0.0\n            maximum_drawdown = 0.0\n\n            for day_index in range(day_offsets.shape[0] - 1):\n                day_start = int(day_offsets[day_index])\n                day_end = int(day_offsets[day_index + 1])\n                exit_index = day_start + int(exit_indices[day_index])\n                cycle_active = False\n                ce_active = False\n                pe_active = False\n                pending = True\n                cycle_number = 0\n                cycle_start_realized = 0.0\n                ce_entry = np.nan\n                pe_entry = np.nan\n                ce_column = -1\n                pe_column = -1\n\n                for event_index in range(day_start, exit_index + 1):\n                    # Do not open or evaluate a cycle before the requested\n                    # entry time.  This must remain inside the numeric kernel\n                    # because entry_start is one of the sweep dimensions.\n                    if seconds[event_index] < entry_seconds:\n                        continue\n\n                    if not cycle_active and pending and event_index < exit_index:\n                        ce_column = selected_columns[premium_index, 0, event_index]\n                        pe_column = selected_columns[premium_index, 1, event_index]\n                        ce_entry = np.nan\n                        pe_entry = np.nan\n                        if ce_column >= 0:\n                            ce_entry = quotes[0, event_index, ce_column]\n                        if pe_column >= 0:\n                            pe_entry = quotes[1, event_index, pe_column]\n                        if np.isfinite(ce_entry) and ce_entry > 0.0 and np.isfinite(pe_entry) and pe_entry > 0.0:\n                            ce_active = True\n                            pe_active = True\n                            cycle_active = True\n                            pending = False\n                            cycle_number += 1\n                            cycle_start_realized = realized\n\n                    if ce_active:\n                        ce_mark = quotes_ff[0, event_index, ce_column]\n                        if np.isfinite(ce_mark) and ce_mark >= ce_entry * (1.0 + leg_sl):\n                            pnl = (ce_entry * (1.0 - slippage) - ce_mark * (1.0 + slippage)) * lot_size\n                            realized += pnl\n                            ce_active = False\n                            trade_count += 1\n                            if pnl > 0.0:\n                                win_count += 1\n                                loss_streak = 0\n                            else:\n                                loss_streak += 1\n                                if loss_streak > maximum_consecutive_losses:\n                                    maximum_consecutive_losses = loss_streak\n                            if trade_count == 1 or pnl < minimum_trade:\n                                minimum_trade = pnl\n                        elif np.isfinite(ce_mark) and ce_mark <= ce_entry * (1.0 - leg_tp):\n                            pnl = (ce_entry * (1.0 - slippage) - ce_mark * (1.0 + slippage)) * lot_size\n                            realized += pnl\n                            ce_active = False\n                            trade_count += 1\n                            if pnl > 0.0:\n                                win_count += 1\n                                loss_streak = 0\n                            else:\n                                loss_streak += 1\n                                if loss_streak > maximum_consecutive_losses:\n                                    maximum_consecutive_losses = loss_streak\n                            if trade_count == 1 or pnl < minimum_trade:\n                                minimum_trade = pnl\n\n                    if pe_active:\n                        pe_mark = quotes_ff[1, event_index, pe_column]\n                        if np.isfinite(pe_mark) and pe_mark >= pe_entry * (1.0 + leg_sl):\n                            pnl = (pe_entry * (1.0 - slippage) - pe_mark * (1.0 + slippage)) * lot_size\n                            realized += pnl\n                            pe_active = False\n                            trade_count += 1\n                            if pnl > 0.0:\n                                win_count += 1\n                                loss_streak = 0\n                            else:\n                                loss_streak += 1\n                                if loss_streak > maximum_consecutive_losses:\n                                    maximum_consecutive_losses = loss_streak\n                            if trade_count == 1 or pnl < minimum_trade:\n                                minimum_trade = pnl\n                        elif np.isfinite(pe_mark) and pe_mark <= pe_entry * (1.0 - leg_tp):\n                            pnl = (pe_entry * (1.0 - slippage) - pe_mark * (1.0 + slippage)) * lot_size\n                            realized += pnl\n                            pe_active = False\n                            trade_count += 1\n                            if pnl > 0.0:\n                                win_count += 1\n                                loss_streak = 0\n                            else:\n                                loss_streak += 1\n                                if loss_streak > maximum_consecutive_losses:\n                                    maximum_consecutive_losses = loss_streak\n                            if trade_count == 1 or pnl < minimum_trade:\n                                minimum_trade = pnl\n\n                    open_pnl = 0.0\n                    if ce_active and np.isfinite(quotes_ff[0, event_index, ce_column]):\n                        open_pnl += (ce_entry * (1.0 - slippage) - quotes_ff[0, event_index, ce_column] * (1.0 + slippage)) * lot_size\n                    if pe_active and np.isfinite(quotes_ff[1, event_index, pe_column]):\n                        open_pnl += (pe_entry * (1.0 - slippage) - quotes_ff[1, event_index, pe_column] * (1.0 + slippage)) * lot_size\n                    equity = realized + open_pnl\n                    if equity > peak_equity:\n                        peak_equity = equity\n                    if equity - peak_equity < maximum_drawdown:\n                        maximum_drawdown = equity - peak_equity\n                    close_cycle = (not ce_active and not pe_active) or event_index == exit_index\n\n                    # A cycle ends once both independent legs have exited, or\n                    # at EOD. Re-entry remains shared across the whole cycle.\n                    if close_cycle and cycle_active:\n                        if ce_active:\n                            ce_mark = quotes_ff[0, event_index, ce_column]\n                            if np.isfinite(ce_mark):\n                                pnl = (ce_entry * (1.0 - slippage) - ce_mark * (1.0 + slippage)) * lot_size\n                                realized += pnl\n                                trade_count += 1\n                                if pnl > 0.0:\n                                    win_count += 1\n                                    loss_streak = 0\n                                else:\n                                    loss_streak += 1\n                                    if loss_streak > maximum_consecutive_losses:\n                                        maximum_consecutive_losses = loss_streak\n                                if trade_count == 1 or pnl < minimum_trade:\n                                    minimum_trade = pnl\n                            ce_active = False\n                        if pe_active:\n                            pe_mark = quotes_ff[1, event_index, pe_column]\n                            if np.isfinite(pe_mark):\n                                pnl = (pe_entry * (1.0 - slippage) - pe_mark * (1.0 + slippage)) * lot_size\n                                realized += pnl\n                                trade_count += 1\n                                if pnl > 0.0:\n                                    win_count += 1\n                                    loss_streak = 0\n                                else:\n                                    loss_streak += 1\n                                    if loss_streak > maximum_consecutive_losses:\n                                        maximum_consecutive_losses = loss_streak\n                                if trade_count == 1 or pnl < minimum_trade:\n                                    minimum_trade = pnl\n                            pe_active = False\n                        cycle_active = False\n                        pending = event_index < exit_index and cycle_number <= reentries\n\n            results[parameter_index, 0] = 1.0 if trade_count else 0.0\n            results[parameter_index, 1] = trade_count\n            results[parameter_index, 2] = realized\n            results[parameter_index, 3] = minimum_trade if trade_count else np.nan\n            results[parameter_index, 4] = win_count / trade_count if trade_count else np.nan\n            results[parameter_index, 5] = maximum_drawdown\n            results[parameter_index, 6] = maximum_consecutive_losses\n        return results\n\n\ndef run_databricks_fast_sweep(\n    spark,\n    summary_table=\"workspace.default.sensex_summary\",\n    chain_table=\"workspace.default.sensex_chain\",\n    start_date=None,\n    end_date=None,\n    lot_size=20,\n    batch_size=25_000,\n    workers=None,\n    max_combinations=None,\n):\n    \"\"\"Run the same sweep rules with one prepared market pass and Numba batches.\n\n    This returns compact screening metrics. Selected winners should be rerun\n    through ``run_databricks_sweep`` for authoritative trade rows.\n    \"\"\"\n    if numba is None:\n        raise RuntimeError(\"Numba is required for the fast Databricks sweep\")\n    summary_columns = [\n        \"datetime\", \"DTE\", \"future_close\", \"future_atm\", \"straddle_future\",\n        \"synth_atm\", \"straddle_synth\",\n    ]\n    chain_columns = [\"datetime\", \"strike\", \"ce_close\", \"pe_close\", \"DTE\"]\n    summary_query = spark.table(summary_table).select(*summary_columns).where(\"DTE = 0\")\n    chain_query = spark.table(chain_table).select(*chain_columns).where(\"DTE = 0\")\n    if start_date is not None:\n        start = pd.Timestamp(start_date).date().isoformat()\n        summary_query = summary_query.where(\n            f\"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start}'\"\n        )\n        chain_query = chain_query.where(\n            f\"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start}'\"\n        )\n    if end_date is not None:\n        end = pd.Timestamp(end_date).date().isoformat()\n        summary_query = summary_query.where(\n            f\"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end}'\"\n        )\n        chain_query = chain_query.where(\n            f\"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end}'\"\n        )\n    summary = _spark_to_pandas(summary_query)\n    chain = _spark_to_pandas(chain_query)\n    summary = _add_time_columns(summary)\n    chain = _add_time_columns(chain)\n    summary[\"entry_atm\"] = summary[\"synth_atm\"].where(summary[\"synth_atm\"].notna(), summary[\"future_atm\"])\n    summary[\"entry_straddle\"] = summary[\"straddle_synth\"].where(summary[\"straddle_synth\"].notna(), summary[\"straddle_future\"])\n    summary = summary[summary[\"future_close\"].notna() & summary[\"entry_atm\"].notna() & summary[\"entry_straddle\"].notna()].copy()\n    if summary.empty or chain.empty:\n        raise ValueError(\"The selected period contains no usable DTE=0 summary and chain rows\")\n    chain = chain[chain[\"dt\"].isin(set(summary[\"dt\"]))].copy()\n    days = _build_days(summary, chain, tuple(float(value) for value in PREMIUM_PRICES))\n    numeric = _fast_numeric_data(days)\n    parameter_sets = list(SWEEP_PARAMETER_SETS)\n    if max_combinations is not None:\n        parameter_sets = parameter_sets[: int(max_combinations)]\n    matrix = _fast_parameter_matrix(parameter_sets)\n    previous_threads = numba.get_num_threads()\n    selected_threads = previous_threads if workers is None else max(1, int(workers))\n    numba.set_num_threads(selected_threads)\n    try:\n        rows = []\n        for start_index in range(0, len(matrix), int(batch_size)):\n            end_index = min(len(matrix), start_index + int(batch_size))\n            metrics = _fast_kernel(\n                matrix[start_index:end_index],\n                numeric[\"day_offsets\"], numeric[\"exit_indices\"], numeric[\"seconds\"],\n                numeric[\"selected_columns\"], numeric[\"quotes\"], numeric[\"quotes_ff\"],\n                int(lot_size), float(SLIPPAGE),\n            )\n            for parameter, metric in zip(parameter_sets[start_index:end_index], metrics):\n                rows.append({\n                    **parameter,\n                    \"status\": \"succeeded\" if metric[0] else \"no_trades\",\n                    \"completed_trade_count\": int(metric[1]),\n                    \"net_pnl\": float(metric[2]),\n                    \"max_loss\": float(metric[3]) if np.isfinite(metric[3]) else None,\n                    \"win_rate\": float(metric[4]) if np.isfinite(metric[4]) else None,\n                    \"max_drawdown\": float(metric[5]),\n                    \"max_consecutive_losses\": int(metric[6]),\n                })\n            print(f\"Completed {end_index}/{len(matrix)} combinations\")\n    finally:\n        numba.set_num_threads(previous_threads)\n    return pd.DataFrame(rows)\n\r\n", "embedded_sensex_base.py", "exec"), _base.__dict__)


numba = _base.numba
njit = _base.njit
prange = _base.prange

STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"

PREMIUM_PRICES = _base.PREMIUM_PRICES
LEG_SL_PCTS = tuple(range(12, 41, 2))
COMBINED_MAX_LOSSES_RS = tuple(range(500, 2001, 250))
COMBINED_TARGETS_RS = tuple(range(1000, 5001, 500))
REENTRIES = _base.REENTRIES
ENTRY_TIMES = _base.ENTRY_TIMES

SWEEP_PARAMETER_NAMES = {
    "premium_price", "leg_sl_pct", "combined_max_loss_rs",
    "combined_target_rs", "leg_sl_reentries",
    "combined_max_loss_reentries", "combined_target_reentries",
    "entry_start",
}


class _SweepGrid:
    _dimensions = (
        PREMIUM_PRICES, LEG_SL_PCTS,
        COMBINED_MAX_LOSSES_RS, COMBINED_TARGETS_RS,
        REENTRIES, REENTRIES, REENTRIES, ENTRY_TIMES,
    )

    def __len__(self):
        result = 1
        for values in self._dimensions:
            result *= len(values)
        return result

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[position] for position in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        selected = []
        for values in reversed(self._dimensions):
            index, offset = divmod(index, len(values))
            selected.append(values[offset])
        (
            premium, leg_sl, max_loss, target,
            leg_sl_reentries, max_loss_reentries, target_reentries,
            entry_start,
        ) = reversed(selected)
        return {
            "premium_price": float(premium),
            "leg_sl_pct": float(leg_sl),
            "combined_max_loss_rs": float(max_loss),
            "combined_target_rs": float(target),
            "leg_sl_reentries": int(leg_sl_reentries),
            "combined_max_loss_reentries": int(max_loss_reentries),
            "combined_target_reentries": int(target_reentries),
            "entry_start": str(entry_start),
        }


SWEEP_PARAMETER_SETS = _SweepGrid()
SWEEP_COMBINATION_COUNT = len(SWEEP_PARAMETER_SETS)
ALLOWED_PARAMETERS = {*SWEEP_PARAMETER_NAMES, "lot_size"}
STRATEGY_NAME = "sensex-dte0-absolute-premium-short-straddle-combined-and-leg-exits"


def _settings(parameters):
    settings = {
        "premium_price": float(parameters["premium_price"]),
        "entry_start": str(parameters["entry_start"]),
        "leg_sl_pct": float(parameters["leg_sl_pct"]),
        "combined_max_loss_rs": float(parameters["combined_max_loss_rs"]),
        "combined_target_rs": float(parameters["combined_target_rs"]),
        "leg_sl_reentries": int(parameters["leg_sl_reentries"]),
        "combined_max_loss_reentries": int(parameters["combined_max_loss_reentries"]),
        "combined_target_reentries": int(parameters["combined_target_reentries"]),
    }
    if settings["premium_price"] <= 0:
        raise ValueError("premium_price must be positive")
    if not 0 < settings["leg_sl_pct"] <= 100:
        raise ValueError("leg_sl_pct must be between 0 and 100")
    if settings["combined_max_loss_rs"] <= 0 or settings["combined_target_rs"] <= 0:
        raise ValueError("combined loss and target must be positive")
    if settings["leg_sl_reentries"] < 0:
        raise ValueError("leg_sl_reentries cannot be negative")
    if settings["combined_max_loss_reentries"] < 0:
        raise ValueError("combined_max_loss_reentries cannot be negative")
    if settings["combined_target_reentries"] < 0:
        raise ValueError("combined_target_reentries cannot be negative")
    if settings["entry_start"] not in ENTRY_TIMES:
        raise ValueError("entry_start must be on the five-minute 09:30-11:00 grid")
    return settings


def _run_day(day, run_id, lot_size, symbol, starting_realized, settings):
    completed = []
    snapshots = []
    realized = float(starting_realized)
    entry_seq = 0
    cycle_no = 0
    leg_sl_reentries_used = 0
    combined_max_loss_reentries_used = 0
    combined_target_reentries_used = 0
    active_cycle = None
    pending_new_cycle = True
    snapshots.append({
        "timestamp": (day["timestamps"][0] - _base.timedelta(seconds=1)).isoformat(),
        "realized_pnl": realized, "unrealized_pnl": 0.0,
    })

    def active_legs():
        if active_cycle is None:
            return []
        return [leg for leg in active_cycle["legs"].values() if leg["status"] == "active"]

    def cycle_realized():
        return 0.0 if active_cycle is None else realized - active_cycle["start_realized"]

    def unrealized(index):
        return float(sum(_base._leg_unrealized(leg, day, index, lot_size) for leg in active_legs()))

    entry_start_i = next((i for i, value in enumerate(day["seconds"])
                          if value >= _base._clock_seconds(settings["entry_start"])), None)
    exit_i = int(day["exit_i"])
    if entry_start_i is None or entry_start_i >= exit_i:
        return completed, snapshots, realized

    def open_leg(option_type, index):
        nonlocal entry_seq
        selected = _base._select_leg(day, option_type, index, settings["premium_price"])
        if selected is None:
            return None
        entry_seq += 1
        selected.update({"status": "active", "entry_i": index, "entry_seq": entry_seq})
        return selected

    def close_leg(leg, index, reason):
        nonlocal realized
        realized += _base._leg_unrealized(leg, day, index, lot_size)
        leg["status"] = "closed"
        completed.append(_base._trade_row(
            day, leg, int(active_cycle["cycle_no"]), index, reason,
            run_id, lot_size, symbol,
        ))

    def close_cycle(index, reason):
        nonlocal active_cycle, pending_new_cycle
        nonlocal leg_sl_reentries_used
        nonlocal combined_max_loss_reentries_used
        nonlocal combined_target_reentries_used
        for leg in list(active_legs()):
            close_leg(leg, index, reason)
        active_cycle = None
        pending_new_cycle = False
        if reason == "LEG_SL" and leg_sl_reentries_used < settings["leg_sl_reentries"]:
            leg_sl_reentries_used += 1
            pending_new_cycle = True
        elif reason == "COMBINED_MAX_LOSS" and combined_max_loss_reentries_used < settings["combined_max_loss_reentries"]:
            combined_max_loss_reentries_used += 1
            pending_new_cycle = True
        elif reason == "COMBINED_TARGET" and combined_target_reentries_used < settings["combined_target_reentries"]:
            combined_target_reentries_used += 1
            pending_new_cycle = True

    for index in range(entry_start_i, exit_i + 1):
        if active_cycle is None and pending_new_cycle and index < exit_i:
            ce = open_leg("CE", index)
            pe = open_leg("PE", index)
            if ce is not None and pe is not None:
                cycle_no += 1
                active_cycle = {"cycle_no": cycle_no, "start_realized": realized,
                                "legs": {"CE": ce, "PE": pe}}
                pending_new_cycle = False

        if active_cycle is not None:
            for option_type, leg in list(active_cycle["legs"].items()):
                if leg["status"] != "active":
                    continue
                mark = _base._leg_mark(day, option_type, int(leg["column"]), index)
                if not np.isfinite(mark):
                    continue
                entry = float(leg["entry_quote"])
                if mark >= entry * (1 + settings["leg_sl_pct"] / 100):
                    close_leg(leg, index, "LEG_SL")

            if active_cycle is not None:
                if not active_legs():
                    close_cycle(index, "LEG_SL")
                else:
                    cycle_total = cycle_realized() + unrealized(index)
                    if cycle_total <= -settings["combined_max_loss_rs"]:
                        close_cycle(index, "COMBINED_MAX_LOSS")
                    elif cycle_total >= settings["combined_target_rs"]:
                        close_cycle(index, "COMBINED_TARGET")

        if index == exit_i and active_cycle is not None:
            close_cycle(index, "EOD")
        snapshots.append({
            "timestamp": day["timestamps"][index].isoformat(),
            "realized_pnl": float(realized),
            "unrealized_pnl": unrealized(index),
        })
    return completed, snapshots, realized


def run_strategy(context):
    config = dict(context.config or {})
    parameters = dict(config.get("parameters") or {})
    unknown = set(parameters) - ALLOWED_PARAMETERS
    if unknown:
        raise ValueError(f"Unsupported strategy parameters: {sorted(unknown)}")
    missing = SWEEP_PARAMETER_NAMES - set(parameters)
    if missing:
        raise ValueError(f"Missing sweep parameters: {sorted(missing)}")
    settings = _settings(parameters)
    symbol = _base._instrument_symbol(config)
    lot_size = _base._lot_size(config, symbol)
    start_date, end_date = _base._period(config)
    days = _base._load_days(Path(context.market_data), symbol,
                            getattr(context, "market_data_loader", None),
                            start_date=start_date, end_date=end_date)
    completed, snapshots, realized = [], [], 0.0
    progress = _base._DayProgress(len(days), getattr(context, "report_progress", None))
    try:
        for day in days:
            trades, marks, realized = _run_day(
                day, str(context.run_id), lot_size, symbol, realized, settings)
            completed.extend(trades)
            snapshots.extend(marks)
            progress.update()
    finally:
        progress.close()
    equity = _base._dedupe_snapshots(snapshots)
    if equity and abs(float(equity[-1]["unrealized_pnl"])) > 0.01:
        raise RuntimeError("Final unrealized P&L is not zero; open legs remain")
    return {
        "completed_trades": completed,
        "completed_trade_count": len(completed),
        "equity_snapshots": equity or None,
        "metadata": {
            "strategy_name": STRATEGY_NAME,
            "engine": "dashboard-compatible deterministic bar engine",
            "combined_max_loss_rs": settings["combined_max_loss_rs"],
            "combined_target_rs": settings["combined_target_rs"],
            "leg_stop_loss": f"{settings['leg_sl_pct']:g}% per leg",
            "leg_take_profit": "disabled",
            "leg_sl_reentries": settings["leg_sl_reentries"],
            "combined_max_loss_reentries": settings["combined_max_loss_reentries"],
            "combined_target_reentries": settings["combined_target_reentries"],
            "slippage": "1.0% at entry and exit",
        },
    }


def _fast_parameter_matrix(parameter_sets):
    matrix = np.empty((len(parameter_sets), 8), dtype=np.float64)
    premium_index = {float(value): index for index, value in enumerate(PREMIUM_PRICES)}
    for row, parameters in enumerate(parameter_sets):
        matrix[row] = (
            premium_index[float(parameters["premium_price"])],
            float(parameters["leg_sl_pct"]),
            float(parameters["combined_max_loss_rs"]),
            float(parameters["combined_target_rs"]),
            float(parameters["leg_sl_reentries"]),
            float(parameters["combined_max_loss_reentries"]),
            float(parameters["combined_target_reentries"]),
            float(_base._clock_seconds(parameters["entry_start"])),
        )
    return np.ascontiguousarray(matrix)


if njit is not None:
    @njit(cache=False, parallel=True)
    def _fast_kernel(parameters, day_offsets, exit_indices, seconds,
                     selected_columns, quotes, quotes_ff, lot_size, slippage):
        results = np.full((parameters.shape[0], 7), np.nan, dtype=np.float64)
        for pi in prange(parameters.shape[0]):
            premium = int(parameters[pi, 0])
            leg_sl = parameters[pi, 1] / 100.0
            max_loss, target = parameters[pi, 2], parameters[pi, 3]
            leg_sl_reentries = int(parameters[pi, 4])
            combined_max_loss_reentries = int(parameters[pi, 5])
            combined_target_reentries = int(parameters[pi, 6])
            entry_seconds = int(parameters[pi, 7])
            realized = 0.0
            trade_count = 0
            win_count = 0
            minimum_trade = 0.0
            loss_streak = 0
            maximum_consecutive_losses = 0
            peak_equity = 0.0
            maximum_drawdown = 0.0
            for di in range(day_offsets.shape[0] - 1):
                start, end = int(day_offsets[di]), int(day_offsets[di + 1])
                exit_i = start + int(exit_indices[di])
                active = False
                ce_active = False
                pe_active = False
                pending = True
                leg_sl_reentries_used = 0
                combined_max_loss_reentries_used = 0
                combined_target_reentries_used = 0
                cycle_start = 0.0
                ce_entry, pe_entry = np.nan, np.nan
                ce_column, pe_column = -1, -1
                for ei in range(start, exit_i + 1):
                    if seconds[ei] < entry_seconds:
                        continue
                    if not active and pending and ei < exit_i:
                        ce_column = selected_columns[premium, 0, ei]
                        pe_column = selected_columns[premium, 1, ei]
                        ce_entry = quotes[0, ei, ce_column] if ce_column >= 0 else np.nan
                        pe_entry = quotes[1, ei, pe_column] if pe_column >= 0 else np.nan
                        if np.isfinite(ce_entry) and ce_entry > 0 and np.isfinite(pe_entry) and pe_entry > 0:
                            active = True; ce_active = True; pe_active = True
                            pending = False; cycle_start = realized
                    if ce_active:
                        mark = quotes_ff[0, ei, ce_column]
                        if np.isfinite(mark) and mark >= ce_entry * (1 + leg_sl):
                            pnl = (ce_entry * (1 - slippage) - mark * (1 + slippage)) * lot_size
                            realized += pnl; ce_active = False; trade_count += 1
                            if pnl > 0: win_count += 1; loss_streak = 0
                            else: loss_streak += 1; maximum_consecutive_losses = max(maximum_consecutive_losses, loss_streak)
                            minimum_trade = pnl if trade_count == 1 else min(minimum_trade, pnl)
                    if pe_active:
                        mark = quotes_ff[1, ei, pe_column]
                        if np.isfinite(mark) and mark >= pe_entry * (1 + leg_sl):
                            pnl = (pe_entry * (1 - slippage) - mark * (1 + slippage)) * lot_size
                            realized += pnl; pe_active = False; trade_count += 1
                            if pnl > 0: win_count += 1; loss_streak = 0
                            else: loss_streak += 1; maximum_consecutive_losses = max(maximum_consecutive_losses, loss_streak)
                            minimum_trade = pnl if trade_count == 1 else min(minimum_trade, pnl)
                    open_pnl = 0.0
                    if ce_active and np.isfinite(quotes_ff[0, ei, ce_column]):
                        open_pnl += (ce_entry * (1 - slippage) - quotes_ff[0, ei, ce_column] * (1 + slippage)) * lot_size
                    if pe_active and np.isfinite(quotes_ff[1, ei, pe_column]):
                        open_pnl += (pe_entry * (1 - slippage) - quotes_ff[1, ei, pe_column] * (1 + slippage)) * lot_size
                    cycle_total = realized - cycle_start + open_pnl
                    equity = realized + open_pnl
                    peak_equity = max(peak_equity, equity)
                    maximum_drawdown = min(maximum_drawdown, equity - peak_equity)
                    close_reason = 0
                    if active and not ce_active and not pe_active:
                        close_reason = 1
                    elif cycle_total <= -max_loss:
                        close_reason = 2
                    elif cycle_total >= target:
                        close_reason = 3
                    elif ei == exit_i:
                        close_reason = 4
                    close_cycle = close_reason != 0
                    if close_cycle and active:
                        if ce_active:
                            mark = quotes_ff[0, ei, ce_column]
                            if np.isfinite(mark):
                                pnl = (ce_entry * (1 - slippage) - mark * (1 + slippage)) * lot_size
                                realized += pnl; trade_count += 1
                                if pnl > 0: win_count += 1; loss_streak = 0
                                else: loss_streak += 1; maximum_consecutive_losses = max(maximum_consecutive_losses, loss_streak)
                                minimum_trade = pnl if trade_count == 1 else min(minimum_trade, pnl)
                            ce_active = False
                        if pe_active:
                            mark = quotes_ff[1, ei, pe_column]
                            if np.isfinite(mark):
                                pnl = (pe_entry * (1 - slippage) - mark * (1 + slippage)) * lot_size
                                realized += pnl; trade_count += 1
                                if pnl > 0: win_count += 1; loss_streak = 0
                                else: loss_streak += 1; maximum_consecutive_losses = max(maximum_consecutive_losses, loss_streak)
                                minimum_trade = pnl if trade_count == 1 else min(minimum_trade, pnl)
                            pe_active = False
                        active = False
                        pending = False
                        if ei < exit_i:
                            if close_reason == 1 and leg_sl_reentries_used < leg_sl_reentries:
                                leg_sl_reentries_used += 1
                                pending = True
                            elif close_reason == 2 and combined_max_loss_reentries_used < combined_max_loss_reentries:
                                combined_max_loss_reentries_used += 1
                                pending = True
                            elif close_reason == 3 and combined_target_reentries_used < combined_target_reentries:
                                combined_target_reentries_used += 1
                                pending = True
            results[pi, 0] = 1.0 if trade_count else 0.0
            results[pi, 1] = trade_count
            results[pi, 2] = realized
            results[pi, 3] = min(0.0, minimum_trade) if trade_count else np.nan
            results[pi, 4] = win_count / trade_count if trade_count else np.nan
            results[pi, 5] = maximum_drawdown
            results[pi, 6] = maximum_consecutive_losses
        return results


def run_databricks_fast_sweep(spark, summary_table="workspace.default.sensex_summary",
                              chain_table="workspace.default.sensex_chain", start_date=None,
                              end_date=None, lot_size=20, batch_size=25_000,
                              workers=None, max_combinations=None):
    """Run every expanded combination with one prepared market-data pass."""
    if numba is None:
        raise RuntimeError("Numba is required for the fast Databricks sweep")
    summary_columns = ["datetime", "DTE", "future_close", "future_atm", "straddle_future", "synth_atm", "straddle_synth"]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]
    summary_query = spark.table(summary_table).select(*summary_columns).where("DTE = 0")
    chain_query = spark.table(chain_table).select(*chain_columns).where("DTE = 0")
    if start_date is not None:
        summary_query = summary_query.where(f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{pd.Timestamp(start_date).date().isoformat()}'")
        chain_query = chain_query.where(f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{pd.Timestamp(start_date).date().isoformat()}'")
    if end_date is not None:
        summary_query = summary_query.where(f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{pd.Timestamp(end_date).date().isoformat()}'")
        chain_query = chain_query.where(f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{pd.Timestamp(end_date).date().isoformat()}'")
    summary = _base._add_time_columns(_base._spark_to_pandas(summary_query))
    chain = _base._add_time_columns(_base._spark_to_pandas(chain_query))
    summary["entry_atm"] = summary["synth_atm"].where(summary["synth_atm"].notna(), summary["future_atm"])
    summary["entry_straddle"] = summary["straddle_synth"].where(summary["straddle_synth"].notna(), summary["straddle_future"])
    summary = summary[summary["future_close"].notna() & summary["entry_atm"].notna() & summary["entry_straddle"].notna()].copy()
    chain = chain[chain["dt"].isin(set(summary["dt"]))].copy()
    days = _base._build_days(summary, chain, tuple(float(v) for v in PREMIUM_PRICES))
    parameter_sets = SWEEP_PARAMETER_SETS
    total = len(parameter_sets) if max_combinations is None else min(
        len(parameter_sets), int(max_combinations)
    )
    previous = numba.get_num_threads()
    maximum_threads = int(getattr(numba.config, "NUMBA_NUM_THREADS", previous))
    selected_threads = previous if workers is None else max(1, int(workers))
    numba.set_num_threads(min(selected_threads, maximum_threads))
    try:
        numeric = _base._fast_numeric_data(days)
        rows = []
        for start in range(0, total, int(batch_size)):
            stop = min(total, start + int(batch_size))
            batch_parameters = parameter_sets[start:stop]
            matrix = _fast_parameter_matrix(batch_parameters)
            metrics = _fast_kernel(matrix, numeric["day_offsets"], numeric["exit_indices"], numeric["seconds"], numeric["selected_columns"], numeric["quotes"], numeric["quotes_ff"], int(lot_size), float(_base.SLIPPAGE))
            for combination_index, (parameters, metric) in enumerate(
                zip(batch_parameters, metrics), start=start
            ):
                rows.append({"combination_index": combination_index, **parameters, "status": "succeeded" if metric[0] else "no_trades", "completed_trade_count": int(metric[1]), "net_pnl": float(metric[2]), "max_loss": float(metric[3]) if np.isfinite(metric[3]) else None, "win_rate": float(metric[4]) if np.isfinite(metric[4]) else None, "max_drawdown": float(metric[5]), "max_consecutive_losses": int(metric[6])})
            print(f"Completed {stop}/{total} combinations")
    finally:
        numba.set_num_threads(previous)
    return pd.DataFrame(rows)


def _screening_rows(batch_parameters, metrics, start_index):
    rows = []
    for combination_index, (parameters, metric) in enumerate(
        zip(batch_parameters, metrics), start=start_index
    ):
        rows.append({
            "combination_index": int(combination_index),
            **parameters,
            "status": "succeeded" if metric[0] else "no_trades",
            "completed_trade_count": int(metric[1]),
            "net_pnl": float(metric[2]),
            "max_loss": float(metric[3]) if np.isfinite(metric[3]) else None,
            "win_rate": float(metric[4]) if np.isfinite(metric[4]) else None,
            "max_drawdown": float(metric[5]),
            "max_consecutive_losses": int(metric[6]),
        })
    return rows


def run_databricks_fast_sweep_to_table(
    spark,
    output_table="workspace.default.sensex_screening_results_all",
    summary_table="workspace.default.sensex_summary",
    chain_table="workspace.default.sensex_chain",
    start_date=None,
    end_date=None,
    lot_size=20,
    batch_size=25_000,
    workers=None,
    max_combinations=None,
):
    """Run the grid and stream each completed batch to a Delta table.

    Use ``max_combinations`` only for smoke tests. Leave it as ``None`` for
    the complete exhaustive grid. The output table is replaced on each run.
    """
    if numba is None:
        raise RuntimeError("Numba is required for the fast Databricks sweep")
    if int(batch_size) <= 0:
        raise ValueError("batch_size must be positive")

    from pyspark.sql.types import (
        DoubleType, LongType, StringType, StructField, StructType,
    )

    summary_columns = [
        "datetime", "DTE", "future_close", "future_atm", "straddle_future",
        "synth_atm", "straddle_synth",
    ]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]
    summary_query = spark.table(summary_table).select(*summary_columns).where("DTE = 0")
    chain_query = spark.table(chain_table).select(*chain_columns).where("DTE = 0")
    if start_date is not None:
        start_value = pd.Timestamp(start_date).date().isoformat()
        summary_query = summary_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start_value}'"
        )
        chain_query = chain_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start_value}'"
        )
    if end_date is not None:
        end_value = pd.Timestamp(end_date).date().isoformat()
        summary_query = summary_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end_value}'"
        )
        chain_query = chain_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end_value}'"
        )

    print("Loading and preparing all available DTE=0 market data...", flush=True)
    summary = _base._add_time_columns(_base._spark_to_pandas(summary_query))
    chain = _base._add_time_columns(_base._spark_to_pandas(chain_query))
    summary["entry_atm"] = summary["synth_atm"].where(
        summary["synth_atm"].notna(), summary["future_atm"]
    )
    summary["entry_straddle"] = summary["straddle_synth"].where(
        summary["straddle_synth"].notna(), summary["straddle_future"]
    )
    summary = summary[
        summary["future_close"].notna()
        & summary["entry_atm"].notna()
        & summary["entry_straddle"].notna()
    ].copy()
    chain = chain[chain["dt"].isin(set(summary["dt"]))].copy()
    if summary.empty or chain.empty:
        raise ValueError("No matching usable DTE=0 summary and chain rows were found")
    days = _base._build_days(summary, chain, tuple(float(v) for v in PREMIUM_PRICES))
    numeric = _base._fast_numeric_data(days)

    parameter_sets = SWEEP_PARAMETER_SETS
    total = len(parameter_sets) if max_combinations is None else min(
        len(parameter_sets), int(max_combinations)
    )
    if total <= 0:
        raise ValueError("No parameter combinations were selected")

    schema = StructType([
        StructField("combination_index", LongType(), False),
        StructField("premium_price", DoubleType(), False),
        StructField("leg_sl_pct", DoubleType(), False),
        StructField("combined_max_loss_rs", DoubleType(), False),
        StructField("combined_target_rs", DoubleType(), False),
        StructField("leg_sl_reentries", LongType(), False),
        StructField("combined_max_loss_reentries", LongType(), False),
        StructField("combined_target_reentries", LongType(), False),
        StructField("entry_start", StringType(), False),
        StructField("status", StringType(), False),
        StructField("completed_trade_count", LongType(), False),
        StructField("net_pnl", DoubleType(), False),
        StructField("max_loss", DoubleType(), True),
        StructField("win_rate", DoubleType(), True),
        StructField("max_drawdown", DoubleType(), False),
        StructField("max_consecutive_losses", LongType(), False),
    ])

    previous = numba.get_num_threads()
    maximum_threads = int(getattr(numba.config, "NUMBA_NUM_THREADS", previous))
    selected_threads = previous if workers is None else max(1, int(workers))
    numba.set_num_threads(min(selected_threads, maximum_threads))
    started = perf_counter()
    try:
        for start in range(0, total, int(batch_size)):
            stop = min(total, start + int(batch_size))
            batch_parameters = parameter_sets[start:stop]
            matrix = _fast_parameter_matrix(batch_parameters)
            metrics = _fast_kernel(
                matrix,
                numeric["day_offsets"], numeric["exit_indices"],
                numeric["seconds"], numeric["selected_columns"],
                numeric["quotes"], numeric["quotes_ff"],
                int(lot_size), float(_base.SLIPPAGE),
            )
            batch_rows = _screening_rows(batch_parameters, metrics, start)
            batch_df = spark.createDataFrame(batch_rows, schema=schema)
            writer = batch_df.write.format("delta")
            if start == 0:
                writer.mode("overwrite").option(
                    "overwriteSchema", "true"
                ).partitionBy("premium_price").saveAsTable(output_table)
            else:
                writer.mode("append").saveAsTable(output_table)

            elapsed = perf_counter() - started
            speed = stop / elapsed if elapsed else 0.0
            remaining = (total - stop) / speed if speed else 0.0
            print(
                f"Completed and saved {stop:,}/{total:,} combinations "
                f"({stop / total:.2%}); elapsed={elapsed / 60:.1f} min; "
                f"ETA={remaining / 60:.1f} min",
                flush=True,
            )
            del batch_df, batch_rows, metrics, matrix, batch_parameters
    finally:
        numba.set_num_threads(previous)

    return spark.table(output_table)
