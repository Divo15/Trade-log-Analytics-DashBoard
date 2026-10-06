"""Create a clean top-N SENSEX review CSV from ranked sorter output."""
from pathlib import Path
import pandas as pd

INPUT = Path("/kaggle/working/sensex_8lac_top100.csv")
RANKED_PARQUET = Path("/kaggle/working/sensex_8lac_ranked.parquet")
OUTPUT = Path("/kaggle/working/sensex_8lac_structured_top20.csv")
TOP_N = 20

def main():
    data = pd.read_csv(INPUT) if INPUT.exists() else pd.DataFrame()

    # Fall back to the ranked Parquet when the CSV is missing or empty.
    if data.empty:
        if not RANKED_PARQUET.exists():
            raise FileNotFoundError(
                "No ranked rows found. Run the ranking script first."
            )
        data = pd.read_parquet(RANKED_PARQUET)

    if data.empty:
        raise ValueError(
            "The ranked output contains zero rows. "
            "Check the ranking filters and source dataset."
        )
    if "net_pnl" not in data.columns:
        raise ValueError("Ranked CSV must contain net_pnl")
    if "rank" in data.columns:
        data = data.sort_values("rank")
    elif "final_score" in data.columns:
        data = data.sort_values("final_score", ascending=False)
    # Preserve every source column, including exit time, status, audit fields,
    # yearly metrics, strategy parameters, and ranking fields.
    result = data.head(TOP_N).copy()
    if "rank" not in result.columns:
        result.insert(0, "rank", range(1, len(result) + 1))
    result = result.round(2)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT, index=False)
    print(f"Structured output: {OUTPUT}")
    print(f"Rows written: {len(result)}")
    try:
        display(result)
    except NameError:
        print(result.to_string(index=False))

if __name__ == "__main__":
    main()
