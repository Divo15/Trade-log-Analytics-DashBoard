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


SELECTION_METRICS = (
    "net_pnl",
    "recent_year_pnl",
    "drawdown",
    "average_win_loss_ratio",
    "win_rate",
    "max_consecutive_losses",
)
QUARTILE_POINTS = (3, 2, -2, -3)
RECENT_YEAR_DECAY = 0.60


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


def _quartile_scores(values, *, higher_is_better=True):
    """Give every value one of four transparent, rank-based point bands.

    Identical values receive identical points. If every value is identical, the
    metric is neutral because it cannot distinguish between combinations.
    """
    if len(set(values)) <= 1:
        return {value: 0 for value in values}
    ordered = sorted(set(values), reverse=higher_is_better)
    positions = {value: index for index, value in enumerate(ordered)}
    return {
        value: QUARTILE_POINTS[min(3, positions[value] * 4 // len(ordered))]
        for value in ordered
    }


def _rank_sweep(summaries):
    """Score profitable combinations using four equal, transparent point bands."""
    eligible = [
        item for item in summaries
        if item.get("metrics") and float(item["metrics"]["net_pnl"]) > 0
    ]
    if not eligible:
        return None

    recent_years = sorted(
        {
            str(year)
            for item in eligible
            for year in item["metrics"].get("yearly_net_pnl", {})
        },
        reverse=True,
    )
    year_weights = {
        year: RECENT_YEAR_DECAY ** offset
        for offset, year in enumerate(recent_years)
    }
    metric_values = {name: [] for name in SELECTION_METRICS}
    for item in eligible:
        metrics = item["metrics"]
        drawdown = metrics["intraday_drawdown"]
        if drawdown is None:
            drawdown = metrics["max_drawdown"]
        magnitude = abs(min(0.0, float(drawdown)))
        average_profit, average_loss = metrics["average_profit"], metrics["average_loss"]
        ratio = (
            float(average_profit) / abs(float(average_loss))
            if average_profit is not None and average_loss not in {None, 0}
            else float("inf") if average_profit is not None
            else 0.0
        )
        metrics["average_win_loss_ratio"] = ratio if ratio != float("inf") else None
        recent_year_pnl = (
            sum(
                float(metrics.get("yearly_net_pnl", {}).get(year, 0.0)) * year_weights[year]
                for year in recent_years
            ) / sum(year_weights.values())
            if recent_years else 0.0
        )
        metric_values["net_pnl"].append(float(metrics["net_pnl"]))
        metric_values["recent_year_pnl"].append(recent_year_pnl)
        metric_values["drawdown"].append(magnitude)
        metric_values["average_win_loss_ratio"].append(ratio)
        metric_values["win_rate"].append(max(0.0, min(100.0, float(metrics["win_rate"]))))
        metric_values["max_consecutive_losses"].append(int(metrics["max_consecutive_losses"]))

    point_bands = {
        name: _quartile_scores(
            values,
            higher_is_better=name not in {"drawdown", "max_consecutive_losses"},
        )
        for name, values in metric_values.items()
    }
    for position, item in enumerate(eligible):
        metrics = item["metrics"]
        components = {
            name: point_bands[name][metric_values[name][position]]
            for name in SELECTION_METRICS
        }
        metrics["selection_components"] = {
            name: int(value) for name, value in components.items()
        }
        metrics["selection_score"] = sum(components.values())

    ranked = sorted(
        eligible,
        key=lambda item: (
            -item["metrics"]["selection_score"],
            -float(item["metrics"]["net_pnl"]),
            abs(min(0.0, float(
                item["metrics"]["intraday_drawdown"]
                if item["metrics"]["intraday_drawdown"] is not None
                else item["metrics"]["max_drawdown"]
            ))),
            item["index"],
        ),
    )
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    return ranked[0]["index"]


def _compact_iteration(iteration_folder, summary):
    """Keep only the comparison row; full artifacts are created after selection."""
    shutil.rmtree(iteration_folder, ignore_errors=True)
    iteration_folder.mkdir(parents=True, exist_ok=True)
    (iteration_folder / "summary.json").write_text(
        json.dumps(summary, allow_nan=False), encoding="utf-8"
    )


def _sweep_payload(summaries, iteration_count, *, partial=False):
    recommended_index = _rank_sweep(summaries)
    return {
        "iteration_count": iteration_count,
        "processed_count": len(summaries),
        "completed_count": sum(item["status"] == "succeeded" for item in summaries),
        "ranked_count": sum("rank" in item for item in summaries),
        "no_trade_count": sum(item["status"] == "no_trades" for item in summaries),
        "failed_count": sum(item["status"] == "failed" for item in summaries),
        "recommended_index": recommended_index,
        "selection_metrics": SELECTION_METRICS,
        "quartile_points": QUARTILE_POINTS,
        "recent_year_decay": RECENT_YEAR_DECAY,
        "partial": partial,
        "iterations": summaries,
    }


def _write_partial_sweep(folder, sweep):
    """Atomically save completed sweep rows so they remain reviewable after cancellation."""
    temporary = folder / f"partial-sweep-{uuid4().hex}.tmp"
    temporary.write_text(json.dumps(sweep, allow_nan=False), encoding="utf-8")
    try:
        temporary.replace(folder / "partial-sweep.json")
    finally:
        temporary.unlink(missing_ok=True)


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

    summaries = []
    sweep_started = time.perf_counter()
    combination_durations = []
    _write_progress(
        folder, completed=0, total=len(parameters), current=1,
        combination_elapsed_seconds=0.0,
        completed_combination_seconds=combination_durations,
    )
    for index, parameter_set in enumerate(parameters):
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
        def report_current(completed, total, unit="item", phase=None):
            _write_progress(
                folder,
                completed=index,
                total=len(parameters),
                current=index + 1,
                combination_completed=completed,
                combination_total=total,
                combination_unit=unit,
                combination_phase=phase,
                combination_elapsed_seconds=time.perf_counter() - combination_started,
                completed_combination_seconds=combination_durations,
                elapsed_seconds=time.perf_counter() - sweep_started,
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
        combination_durations.append(time.perf_counter() - combination_started)
        _write_partial_sweep(folder, _sweep_payload(summaries, len(parameters), partial=True))
        _write_progress(
            folder,
            completed=index + 1,
            total=len(parameters),
            current=index + 2 if index + 1 < len(parameters) else None,
            combination_elapsed_seconds=0.0,
            completed_combination_seconds=combination_durations,
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
