"""Full-history runtime acceptance check; saves evidence outside Best history."""
import contextlib
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from time import perf_counter

import pandas as pd
from trade_log_dashboard.datasets import resolve_dataset
from trade_log_dashboard.worker import run


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs" / ("full-weekly-test-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    (output / "code").mkdir()
    script = "nifty_6DTE_weekly_hard_sl_tp_two_reentries.py"
    source = root / "strategies" / script
    shutil.copy2(source, output / "code" / script)
    market, dataset = resolve_dataset("weekly")
    request = dict(entrypoint=script, run_id="acceptance-weekly", market_data=str(market),
                   dataset=dataset, dataset_label=dataset["label"], config={
        "instrument": {"symbol": "NIFTY", "timeframe": "1m"},
        "period": {"start_date": dataset["start_date"], "end_date": dataset["end_date"]},
        "execution": {"slippage": .005, "fees_per_leg": 0, "timezone": "Asia/Kolkata"},
        "parameters": {},
    })
    (output / "request.json").write_text(json.dumps(request), encoding="utf-8")
    print(f"Evidence folder: {output}", flush=True)
    started = perf_counter()
    with (output / "run.log").open("w", encoding="utf-8", buffering=1) as log, contextlib.redirect_stdout(log):
        run(output)
    sweep_seconds = perf_counter() - started
    sweep = json.loads((output / "result.json").read_text())["sweep"]
    report = dict(dataset=dataset, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  sweep_seconds=sweep_seconds, combinations=sweep["iteration_count"],
                  failed=sweep["failed_count"], completed=sweep["completed_count"],
                  no_trades=sweep["no_trade_count"], recommended_index=sweep["recommended_index"])
    assert report["combinations"] == 60 and report["failed"] == 0, report
    index = sweep["recommended_index"]
    # If nothing is profitable, still verify reproduction of one completed row.
    row = next((r for r in sweep["iterations"] if r["index"] == index), None)
    if row is None:
        row = next(r for r in sweep["iterations"] if r.get("metrics"))
    winner = output / "verified-result"
    winner.mkdir()
    shutil.copytree(output / "code", winner / "code")
    request["config"]["parameters"] = row["parameters"]
    request.update(run_mode_override="single", optimizer_expected_metrics=row["metrics"])
    (winner / "request.json").write_text(json.dumps(request), encoding="utf-8")
    started = perf_counter()
    with (winner / "run.log").open("w", encoding="utf-8", buffering=1) as log, contextlib.redirect_stdout(log):
        run(winner)
    report["rerun_seconds"] = perf_counter() - started
    report["verified_index"] = row["index"]
    report["verified_parameters"] = row["parameters"]
    report["verified_metrics"] = row["metrics"]
    trades = pd.read_csv(winner / "trades.csv")
    report["completed_legs"] = len(trades)
    report["first_entry"] = str(trades.entry_time.min())
    report["last_exit"] = str(trades.exit_time.max())
    report["reproduction_passed"] = True
    (output / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
