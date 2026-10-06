"""Run the R6 Numba accelerator in resumable numeric chunks.

Checkpoint files are provisional screening artifacts.  They are not canonical
trade logs and must not be used as final dashboard analytics.  Rerun selected
parameter mappings through the authoritative ``run_strategy(context)`` path.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies import nifty_current_week_0dte_trend_following_r6_numba_batch as batch
from strategies import nifty_current_week_0dte_trend_following_r6_sweep_dashboard as strategy


def _atomic_checkpoint(path: Path, parameters: np.ndarray, metrics: np.ndarray) -> None:
    fingerprint = sha256(parameters.tobytes(order="C")).hexdigest()
    temporary = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(
        temporary,
        parameters=parameters,
        metrics=metrics,
        parameter_sha256=np.asarray(fingerprint),
    )
    temporary.replace(path)


def _checkpoint_matches(path: Path, parameters: np.ndarray) -> bool:
    if not path.is_file():
        return False
    expected = sha256(parameters.tobytes(order="C")).hexdigest()
    try:
        with np.load(path, allow_pickle=False) as saved:
            return (
                saved["parameters"].shape == parameters.shape
                and str(saved["parameter_sha256"].item()) == expected
            )
    except (OSError, ValueError, KeyError):
        return False


def run_checkpoints(
    context,
    parameter_sets,
    output: Path,
    *,
    chunk_size: int = batch.DEFAULT_BATCH_SIZE,
    workers: int | None = None,
) -> dict:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    data, parameter_matrix = batch.prepare_batch_data(context, parameter_sets)
    completed = []
    for start in range(0, len(parameter_matrix), chunk_size):
        end = min(len(parameter_matrix), start + chunk_size)
        chunk = np.ascontiguousarray(parameter_matrix[start:end])
        checkpoint = output / f"chunk-{start:09d}-{end:09d}.npz"
        if not _checkpoint_matches(checkpoint, chunk):
            metrics = batch.run_batch_kernel(data, chunk, workers=workers, parallel=True)
            _atomic_checkpoint(checkpoint, chunk, metrics)
        completed.append(checkpoint.name)

    manifest = {
        "strategy": strategy.STRATEGY_NAME,
        "provisional": True,
        "combination_count": len(parameter_matrix),
        "chunk_size": chunk_size,
        "parameter_columns": list(batch.PARAMETER_COLUMNS),
        "metric_columns": list(batch.METRIC_COLUMNS),
        "premium_values": data.premium_values.tolist(),
        "checkpoints": completed,
        "warning": (
            "These numeric rows are provisional. Rerun selected mappings with "
            "run_strategy(context) before using or saving dashboard analytics."
        ),
    }
    manifest_path = output / "manifest.json"
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest


def _load_json(path: Path, expected_type):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise TypeError(f"{path} must contain {expected_type.__name__}")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parameters-json", type=Path)
    parser.add_argument("--config-json", type=Path)
    parser.add_argument("--chunk-size", type=int, default=batch.DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args(argv)

    parameter_sets = (
        _load_json(args.parameters_json, list)
        if args.parameters_json
        else list(strategy.SWEEP_PARAMETER_SETS)
    )
    config = _load_json(args.config_json, dict) if args.config_json else {}
    context = SimpleNamespace(
        run_id="r6-numba-screen",
        market_data=args.market_data,
        config=config,
    )
    manifest = run_checkpoints(
        context,
        parameter_sets,
        args.output,
        chunk_size=args.chunk_size,
        workers=args.workers,
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

