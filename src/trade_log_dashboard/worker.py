"""Local subprocess entry point. Uploaded strategies execute with user privileges."""
import importlib.util
import copy
import json
from pathlib import Path
import shutil
import sys
import traceback
import time
from numbers import Integral
from types import SimpleNamespace
from trade_log_dashboard.market_data import MarketDataLoader
from collections.abc import Mapping, Sequence
from uuid import uuid4

from trade_log_exporter import export_trade_log, export_equity_snapshots
from trade_log_dashboard.analytics import analyze_trade_log
from trade_log_dashboard.equity import analyze_equity
from trade_log_dashboard.legacy import supports_protected_straddle, run_protected_straddle
from trade_log_dashboard.sweep import QUARTILE_POINTS, SELECTION_METRICS, rank_sweep, sweep_payload


def _write_progress(folder, *, completed, total, current=None, mode="sweep",
                    unit="combination", elapsed_seconds=None, **details):
    payload = {
        "mode": mode,
        "completed": completed,
        "total": total,
        "current": current,
        "unit": unit,
        "updated_at": time.time(),
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = max(0.0, float(elapsed_seconds))
    payload.update(details)
    temporary = folder / f"progress-{uuid4().hex}.tmp"
    temporary.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                temporary.replace(folder / "progress.json")
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _validate_result(result):
    if not isinstance(result, Mapping):
        raise ValueError("run_strategy must return completed_trades and completed_trade_count in a mapping.")
    if "completed_trades" not in result:
        raise ValueError("run_strategy result must contain completed_trades.")
    count = result.get("completed_trade_count")
    if isinstance(count, bool) or not isinstance(count, Integral) or count < 0:
        raise ValueError("completed_trade_count must be a nonnegative integer.")
    return int(count)


def _execute_result(result, context, output_folder, dataset):
    output_folder.mkdir(parents=True, exist_ok=True)
    count = _validate_result(result)
    receipt = export_trade_log(
        result["completed_trades"], output_folder / "trades.csv",
        mapper=result.get("trade_mapper"), expected_count=count,
    )
    if count == 0:
        return {
            "status": "empty",
            "message": "Backtest completed with no closed trades. Check the selected market data and entry conditions.",
            "analysis": None,
            "has_equity": False,
        }
    print(f"Validating and analysing {receipt.row_count} completed legs…", flush=True)
    analysis = analyze_trade_log(receipt.output_path, validated_receipt=receipt)
    analysis["dataset"] = dataset or {"id": None, "label": "Custom data"}
    parameters = context.config.get("parameters", {})
    analysis["parameters"] = dict(parameters) if isinstance(parameters, Mapping) else {}
    has_equity = result.get("equity_snapshots") is not None
    if has_equity:
        equity_path = export_equity_snapshots(
            result["equity_snapshots"], output_folder / "equity.csv", run_id=context.run_id
        )
        analysis["intraday"] = analyze_equity(equity_path, receipt.output_path)
    (output_folder / "analysis.json").write_text(
        json.dumps({"status": "succeeded", "analysis": analysis, "has_equity": has_equity}, allow_nan=False),
        encoding="utf-8",
    )
    return {"status": "succeeded", "analysis": analysis, "has_equity": has_equity}


def _summary(index, parameters, detail):
    if detail["status"] == "empty":
        return {"index": index, "parameters": parameters, "status": "no_trades", "metrics": None}
    analysis = detail["analysis"]
    overview, stats = analysis["overview"], analysis["statistics"]
    yearly_net_pnl = {}
    for row in analysis["daily"]:
        year = str(row["day"])[:4]
        yearly_net_pnl[year] = yearly_net_pnl.get(year, 0.0) + float(row["net_pnl"])
    return {
        "index": index,
        "parameters": parameters,
        "status": "succeeded",
        "has_equity": detail["has_equity"],
        "metrics": {
            "net_pnl": overview["net_pnl"],
            "yearly_net_pnl": {
                year: yearly_net_pnl[year] for year in sorted(yearly_net_pnl)
            },
            "max_drawdown": stats["max_drawdown"],
            "intraday_drawdown": analysis.get("intraday", {}).get("max_drawdown"),
            "win_rate": stats["win_rate"],
            "profit_factor": stats["profit_factor"],
            "max_consecutive_losses": stats["longest_loss_streak"],
            "average_profit": stats["average_win_batch"],
            "average_loss": stats["average_loss_batch"],
            "sharpe_traded_days": stats["sharpe_traded_days"],
            "batch_count": overview["batch_count"],
            "traded_days": overview["traded_days"],
        },
    }


def _rank_sweep(summaries):
    """Compatibility wrapper for existing strategy dashboard integrations."""
    return rank_sweep(summaries)


def _sweep_payload(summaries, iteration_count, *, partial=False):
    return sweep_payload(summaries, iteration_count, partial=partial)


def _compact_iteration(iteration_folder, summary):
    """Keep only the comparison row; full artifacts are created after selection."""
    shutil.rmtree(iteration_folder, ignore_errors=True)
    iteration_folder.mkdir(parents=True, exist_ok=True)
    (iteration_folder / "summary.json").write_text(
        json.dumps(summary, allow_nan=False), encoding="utf-8"
    )


def _existing_iteration(iteration_folder, index, parameters):
    """Return a prior completed iteration only when it matches this sweep input."""
    summary_path = iteration_folder / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (
        summary.get("index") != index
        or summary.get("parameters") != parameters
        or summary.get("status") not in {"succeeded", "no_trades", "failed"}
    ):
        return None
    return summary


def _run_sweep(module, base_context, folder, dataset, execute=None):
    values = getattr(module, "SWEEP_PARAMETER_SETS", None)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ValueError("Sweep strategies must declare a non-empty SWEEP_PARAMETER_SETS sequence of parameter mappings.")
    parameters = []
    seen = set()
    for index, value in enumerate(values, 1):
        if not isinstance(value, Mapping):
            raise ValueError(f"Sweep variation {index} must be a parameter mapping.")
        normalized = dict(value)
        try:
            fingerprint = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sweep variation {index} contains values that cannot be stored as JSON.") from exc
        if fingerprint in seen:
            raise ValueError(f"Sweep variation {index} duplicates an earlier parameter set.")
        seen.add(fingerprint)
        parameters.append(normalized)

    existing = {
        index: _existing_iteration(folder / "iterations" / str(index), index, parameter_set)
        for index, parameter_set in enumerate(parameters)
    }
    summaries = [summary for summary in existing.values() if summary is not None]
    summaries.sort(key=lambda summary: summary["index"])
    sweep_started = time.perf_counter()
    duration_count = 0
    duration_total_seconds = 0.0
    next_index = next((index for index in range(len(parameters)) if existing[index] is None), None)
    _write_progress(
        folder, completed=len(summaries), total=len(parameters),
        current=next_index + 1 if next_index is not None else None,
        combination_elapsed_seconds=0.0,
        completed_combination_count=duration_count,
        completed_combination_total_seconds=duration_total_seconds,
    )
    for index, parameter_set in enumerate(parameters):
        if existing[index] is not None:
            continue
        combination_started = time.perf_counter()
        print(f"Sweep {index + 1}/{len(parameters)} · parameters {json.dumps(parameter_set, sort_keys=True)}", flush=True)
        config = copy.deepcopy(base_context.config)
        config["parameters"] = parameter_set
        context = SimpleNamespace(
            run_id=f"{base_context.run_id}-s{index + 1}",
            market_data=base_context.market_data,
            market_data_loader=getattr(base_context, "market_data_loader", None),
            config=config,
        )
        progress_state = {"last_write": 0.0, "phase": object()}

        def report_current(completed, total, unit="item", phase=None):
            now = time.perf_counter()
            if phase == progress_state["phase"] and now - progress_state["last_write"] < 1.0:
                return
            progress_state.update(last_write=now, phase=phase)
            _write_progress(
                folder,
                completed=index,
                total=len(parameters),
                current=index + 1,
                combination_completed=completed,
                combination_total=total,
                combination_unit=unit,
                combination_phase=phase,
                combination_elapsed_seconds=now - combination_started,
                completed_combination_count=duration_count,
                completed_combination_total_seconds=duration_total_seconds,
                elapsed_seconds=now - sweep_started,
            )
        context.report_progress = report_current
        iteration_folder = folder / "iterations" / str(index)
        try:
            result = execute(context) if execute else module.run_strategy(context)
            detail = _execute_result(result, context, iteration_folder, dataset)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"Variation {index + 1} failed · {error}", flush=True)
            summary = {
                "index": index,
                "parameters": parameter_set,
                "status": "failed",
                "metrics": None,
                "error": error,
            }
        else:
            summary = _summary(index, parameter_set, detail)
        _compact_iteration(iteration_folder, summary)
        summaries.append(summary)
        duration_count += 1
        duration_total_seconds += time.perf_counter() - combination_started
        _write_progress(
            folder,
            completed=index + 1,
            total=len(parameters),
            current=index + 2 if index + 1 < len(parameters) else None,
            combination_elapsed_seconds=0.0,
            completed_combination_count=duration_count,
            completed_combination_total_seconds=duration_total_seconds,
            elapsed_seconds=time.perf_counter() - sweep_started,
        )
    return {
        "status": "succeeded",
        "mode": "sweep",
        "sweep": _sweep_payload(summaries, len(parameters)),
    }


def run(folder):
    with MarketDataLoader(directory=folder) as loader:
        started = time.perf_counter()
        try:
            _run(folder, loader)
        finally:
            print(f"Worker elapsed: {time.perf_counter() - started:.3f}s", flush=True)
            print(f"Market-data cache: {loader.stats}", flush=True)
            print(f"Cache retained: {loader.memory_bytes} memory bytes, {loader.disk_bytes} disk bytes", flush=True)


def _run(folder, loader):
    folder = Path(folder).resolve()
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(folder / "code"))
    spec = importlib.util.spec_from_file_location("uploaded_strategy", folder / "code" / request["entrypoint"])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses inspect their defining module
    spec.loader.exec_module(module)
    print("Running strategy against supplied market data…", flush=True)
    print(f"Dataset: {request.get('dataset_label') or 'Custom data'}\nMarket data: {request['market_data']}", flush=True)
    context = SimpleNamespace(run_id=request["run_id"], market_data=Path(request["market_data"]), config=request["config"], market_data_loader=loader)
    selected_rerun = request.get("run_mode_override") == "single"
    if callable(getattr(module, "run_strategy", None)):
        if str(getattr(module, "STRATEGY_CONTRACT_VERSION", "")) not in {"1", "2"}:
            raise ValueError("A run_strategy(context) strategy must declare STRATEGY_CONTRACT_VERSION='1' or '2'.")
        mode = "single" if selected_rerun else getattr(module, "RUN_MODE", "single")
        if mode == "sweep":
            output = _run_sweep(module, context, folder, request.get("dataset"))
            (folder / "result.json").write_text(json.dumps(output, allow_nan=False), encoding="utf-8")
            return
        if mode != "single":
            raise ValueError("RUN_MODE must be 'single' or 'sweep'.")
        progress_state = {"phase": None, "started": time.perf_counter()}
        def report_single_progress(completed, total, unit="item", phase=None):
            if phase != progress_state["phase"]:
                progress_state.update(phase=phase, started=time.perf_counter())
            _write_progress(
                folder,
                completed=completed,
                total=total,
                mode="single",
                unit=unit,
                elapsed_seconds=time.perf_counter() - progress_state["started"],
                phase=phase,
            )
        context.report_progress = report_single_progress
        result = module.run_strategy(context)
    elif supports_protected_straddle(module):
        print("Recognized standalone ProtectedStraddleBacktester; applying the project-data adapter…", flush=True)
        mode = "single" if selected_rerun else getattr(module, "RUN_MODE", "single")
        if mode == "sweep":
            output = _run_sweep(
                module, context, folder, request.get("dataset"),
                execute=lambda iteration_context: run_protected_straddle(module, iteration_context),
            )
            (folder / "result.json").write_text(json.dumps(output, allow_nan=False), encoding="utf-8")
            return
        if mode != "single":
            raise ValueError("RUN_MODE must be 'single' or 'sweep'.")
        result = run_protected_straddle(module, context)
    else:
        raise ValueError(
            "Unsupported Python strategy. Provide run_strategy(context), or a standalone file "
            "containing StrategyConfig, DataLoader and ProtectedStraddleBacktester."
        )
    detail = _execute_result(result, context, folder, request.get("dataset"))
    expected = request.get("optimizer_expected_metrics")
    if expected and detail["status"] != "succeeded":
        raise ValueError(
            "Selected combination rerun produced no completed trades. "
            "Make the strategy deterministic before selecting a winner."
        )
    if expected:
        actual = _summary(0, context.config.get("parameters") or {}, detail)["metrics"]
        mismatches = []
        for name in ("net_pnl", "max_drawdown", "win_rate", "batch_count"):
            expected_value, actual_value = expected.get(name), actual.get(name)
            if isinstance(expected_value, (int, float)) and isinstance(actual_value, (int, float)):
                if abs(float(expected_value) - float(actual_value)) > 1e-6:
                    mismatches.append(name)
            elif expected_value != actual_value:
                mismatches.append(name)
        if mismatches:
            raise ValueError(
                "Selected combination rerun did not match its sweep row for: "
                + ", ".join(mismatches)
                + ". Make the strategy deterministic before selecting a winner."
            )
    output = {"status": detail["status"]}
    if detail["status"] == "empty":
        output["message"] = detail["message"]
    else:
        output["analysis"] = detail["analysis"]
    (folder / "result.json").write_text(json.dumps(output, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except Exception as exc:
        traceback.print_exc()
        message = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, ModuleNotFoundError):
            message += " — Install this strategy dependency in the Python environment used to launch trade-dashboard, then retry."
        Path(sys.argv[1], "result.json").write_text(json.dumps({"status": "failed", "error": message}), encoding="utf-8")
        sys.exit(1)
