"""Create a clean structured table from the SENSEX sorter output.

Example:
    %run /kaggle/working/structure_sensex_output.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INPUT = "/kaggle/working/sensex_8lac_top100.csv"
DEFAULT_OUTPUT = "/kaggle/working/sensex_8lac_structured_top20.csv"


def running_inside_notebook() -> bool:
    executable = Path(sys.argv[0]).name.lower() if sys.argv else ""
    return (
        "ipykernel_launcher" in executable
        or "colab_kernel_launcher" in executable
        or any(arg.endswith(".json") and "/jupyter/" in arg for arg in sys.argv[1:])
    )


def detect_yearly_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    column_set = set(columns)
    years = sorted(
        column.removeprefix("pnl_")
        for column in columns
        if column.startswith("pnl_") and column.removeprefix("pnl_").isdigit()
    )
    pnl_columns = [f"pnl_{year}" for year in years]
    dd_columns = [f"mtm_dd_{year}" for year in years if f"mtm_dd_{year}" in column_set]
    return pnl_columns, dd_columns


def select_columns(columns: list[str]) -> list[str]:
    yearly_pnl_cols, yearly_dd_cols = detect_yearly_columns(columns)
    preferred_cols = [
        "entry_start",
        "net_pnl",
        "overall_roi_pct",
        *yearly_pnl_cols,
        "recent_year_pnl",
        "worst_year_pnl",
        *yearly_dd_cols,
        "worst_year_drawdown",
        "max_drawdown",
        "max_loss",
        "max_consecutive_losses",
        "win_rate",
        "bracket_pnl_ratio",
        "prior_robust_ratio",
        "after_robust_ratio",
        "entry_robust_side",
        "reentry_burden",
        "passes_strict_screen",
    ]
    known_result_cols = set(preferred_cols + [
        "rank",
        "status",
        "completed_trade_count",
        "generated_combination_index",
        "drawdown_risk",
        "max_loss_risk",
        "bracket_min_pnl",
        "bracket_max_pnl",
        "bracket_count",
        "expected_bracket_count",
        "prior_bracket_count",
        "after_bracket_count",
        "drawdown_score",
        "max_loss_score",
        "streak_score",
    ])
    param_cols = [
        column for column in columns
        if column not in known_result_cols
        and not column.endswith("_score")
        and not column.startswith("pnl_")
        and not column.startswith("roi_")
        and not column.startswith("mtm_dd_")
    ]
    selected = []
    for column in param_cols + preferred_cols:
        if column in columns and column not in selected:
            selected.append(column)
    return selected


def find_sorter_csv(input_csv: str | Path) -> Path:
    source = Path(input_csv)
    if source.exists():
        return source

    candidates = [
        Path("/kaggle/working/sensex_8lac_top100.csv"),
        Path("/kaggle/working/sensex_yearly_top100.csv"),
        Path("/kaggle/working/top100.csv"),
    ]
    for candidate in candidates:
        if candidate.exists():
            print(f"Using sorter output CSV: {candidate}", flush=True)
            return candidate

    working = Path("/kaggle/working")
    if working.exists():
        matches = sorted(working.glob("*top100*.csv"))
        if matches:
            print(f"Using sorter output CSV: {matches[0]}", flush=True)
            return matches[0]

    raise FileNotFoundError(
        "Sorter output CSV not found. Expected one of: "
        "/kaggle/working/sensex_yearly_top100.csv, "
        "/kaggle/working/sensex_8lac_top100.csv, or any *top100*.csv file."
    )


def structure_output(input_csv: str | Path, output_csv: str | Path, top_n: int = 20) -> Path:
    import pandas as pd
    source = find_sorter_csv(input_csv)
    output = Path(output_csv)

    data = pd.read_csv(source)
    yearly_pnl_cols, yearly_dd_cols = detect_yearly_columns(list(data.columns))
    if not yearly_pnl_cols:
        raise ValueError(
            "Year-on-year P&L values are missing from the sorter CSV. "
            "sorting script on the yearly Parquet file before structuring."
        )
    if "net_pnl" not in data.columns:
        pnl_alias = next(
            (
                column for column in (
                    "pnl", "total_pnl", "net_profit",
                    "total_net_pnl", "gross_pnl",
                )
                if column in data.columns
            ),
            None,
        )
        if pnl_alias:
            data["net_pnl"] = data[pnl_alias]
            print(f"Using {pnl_alias} as net_pnl.", flush=True)
        else:
            data["net_pnl"] = data[yearly_pnl_cols].sum(axis=1)
            print(
                f"Derived net_pnl from yearly P&L columns: {yearly_pnl_cols}",
                flush=True,
            )
    if "recent_year_pnl" not in data.columns:
        data["recent_year_pnl"] = data[yearly_pnl_cols[-2:]].mean(axis=1)
    if "worst_year_pnl" not in data.columns:
        data["worst_year_pnl"] = data[yearly_pnl_cols].min(axis=1)

    if not yearly_dd_cols:
        raise ValueError(
            "Year-on-year DD values are missing from the sorter CSV. "
            "sorting script on the yearly Parquet file before structuring."
        )
    elif "worst_year_drawdown" not in data.columns:
        data["worst_year_drawdown"] = data[yearly_dd_cols].abs().max(axis=1)

    if "rank" in data.columns:
        data = data.sort_values("rank", ascending=True)
    elif "final_score" in data.columns:
        sort_columns = [
            column for column in [
                "final_score",
                "worst_year_pnl",
                "bracket_pnl_ratio",
                "net_pnl",
            ] if column in data.columns
        ]
        data = data.sort_values(
            by=sort_columns,
            ascending=[False] * len(sort_columns),
        )
    else:
        data = data.sort_values("net_pnl", ascending=False)

    selected_cols = select_columns(list(data.columns))
    structured = data[selected_cols].head(top_n).copy()
    if "rank" not in structured.columns:
        structured.insert(0, "rank", range(1, len(structured) + 1))

    for column in structured.select_dtypes(include="number").columns:
        structured[column] = structured[column].round(2)

    output.parent.mkdir(parents=True, exist_ok=True)
    structured.to_csv(output, index=False)

    print(f"Saved structured output: {output}", flush=True)
    try:
        display(structured)
    except NameError:
        print(structured.to_string(index=False), flush=True)
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=20)

    if argv is None and running_inside_notebook():
        argv = []

    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args:
        print(f"Ignoring notebook-injected arguments: {unknown_args}", flush=True)

    structure_output(args.input, args.output, args.top_n)


if __name__ == "__main__":
    main()
