"""Validate an uploaded strategy through the real local worker on supplied sample data."""
import argparse
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

from .runner import LocalRunner


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy", type=Path)
    parser.add_argument("--market-data", required=True, type=Path,
                        help="Representative sample dataset in the strategy's expected schema")
    parser.add_argument("--config", type=Path, help="Optional dashboard configuration JSON")
    parser.add_argument("--output", required=True, type=Path,
                        help="New folder for validation report and worker artifacts")
    parser.add_argument("--timeout", type=float, default=120,
                        help="Maximum total seconds for this check (default: 120)")
    args = parser.parse_args(argv)
    strategy, market, output = (p.resolve() for p in
                                (args.strategy, args.market_data, args.output))
    if not strategy.is_file() or strategy.suffix != ".py":
        parser.error("strategy must be an existing .py file")
    if not market.exists():
        parser.error("--market-data must exist")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
        if not isinstance(config, dict):
            raise ValueError("configuration must be a JSON object")
        output.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print("Validating the strategy on the supplied dataset using the local worker.", flush=True)
    print("Use representative sample data; this command does not select dates or change strategy rules.", flush=True)
    with tempfile.TemporaryDirectory(prefix="strategy-check-") as temporary:
        runner = LocalRunner(history_root=Path(temporary) / "history")
        try:
            identifier = runner.start(
                {"entrypoint": strategy.name, "market_path": str(market),
                 "config": json.dumps(config, allow_nan=False)},
                [("strategy", strategy.name, strategy.read_bytes())],
            )
            deadline = time.monotonic() + args.timeout
            timed_out = False
            while True:
                job = runner.status(identifier)
                if job["status"] != "running":
                    break
                if time.monotonic() >= deadline:
                    runner.cancel(identifier)
                    job = runner.status(identifier)
                    timed_out = True
                    break
                time.sleep(0.2)
            sweep = (job.get("result") or {}).get("sweep")
            if timed_out:
                outcome = "timeout"
            elif sweep:
                outcomes = [row["status"] for row in sweep["iterations"]]
                outcome = ("failed" if "failed" in outcomes else
                           "passed" if all(value == "succeeded" for value in outcomes) else
                           "no_trades")
            else:
                outcome = {"succeeded": "passed", "empty": "no_trades"}.get(job["status"], "failed")
            report = {"validation": outcome, "strategy": str(strategy),
                      "market_data": str(market), "job": job}
            (output / "validation.json").write_text(
                json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
            )
            folder = runner.jobs[identifier]["folder"]
            for name in ("request.json", "result.json", "progress.json", "run.log",
                         "trades.csv", "trades.csv.manifest.json", "equity.csv", "analysis.json"):
                source = folder / name
                if source.is_file():
                    shutil.copy2(source, output / name)
            if (folder / "iterations").is_dir():
                shutil.copytree(folder / "iterations", output / "iterations")
            print(f"Validation: {outcome}. Report: {output / 'validation.json'}", flush=True)
            if job.get("error"):
                print(job["error"], flush=True)
            if sweep:
                for row in sweep["iterations"]:
                    print(f"Combination {row['index'] + 1}: {row['status']} {row.get('error', '')}", flush=True)
            return 0 if outcome == "passed" else 1
        finally:
            runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
