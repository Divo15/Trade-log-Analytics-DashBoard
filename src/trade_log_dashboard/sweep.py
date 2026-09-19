"""Ranking and presentation helpers shared by the sweep worker and runner."""

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


def _quartile_scores(values, *, higher_is_better=True):
    """Give every distinct value one of four transparent, rank-based bands."""
    if len(set(values)) <= 1:
        return {value: 0 for value in values}
    ordered = sorted(set(values), reverse=higher_is_better)
    positions = {value: index for index, value in enumerate(ordered)}
    return {
        value: QUARTILE_POINTS[min(3, positions[value] * 4 // len(ordered))]
        for value in ordered
    }


def rank_sweep(summaries):
    """Score profitable combinations using four equal, transparent point bands."""
    eligible = [
        item for item in summaries
        if item.get("metrics") and float(item["metrics"]["net_pnl"]) > 0
    ]
    if not eligible:
        return None

    recent_years = sorted({str(year) for item in eligible
                           for year in item["metrics"].get("yearly_net_pnl", {})}, reverse=True)
    year_weights = {year: RECENT_YEAR_DECAY ** offset for offset, year in enumerate(recent_years)}
    metric_values = {name: [] for name in SELECTION_METRICS}
    for item in eligible:
        metrics = item["metrics"]
        drawdown = metrics["intraday_drawdown"]
        if drawdown is None:
            drawdown = metrics["max_drawdown"]
        magnitude = abs(min(0.0, float(drawdown)))
        average_profit, average_loss = metrics["average_profit"], metrics["average_loss"]
        ratio = (float(average_profit) / abs(float(average_loss))
                 if average_profit is not None and average_loss not in {None, 0}
                 else float("inf") if average_profit is not None else 0.0)
        metrics["average_win_loss_ratio"] = ratio if ratio != float("inf") else None
        recent_year_pnl = (sum(float(metrics.get("yearly_net_pnl", {}).get(year, 0.0)) * year_weights[year]
                               for year in recent_years) / sum(year_weights.values())
                           if recent_years else 0.0)
        metric_values["net_pnl"].append(float(metrics["net_pnl"]))
        metric_values["recent_year_pnl"].append(recent_year_pnl)
        metric_values["drawdown"].append(magnitude)
        metric_values["average_win_loss_ratio"].append(ratio)
        metric_values["win_rate"].append(max(0.0, min(100.0, float(metrics["win_rate"]))))
        metric_values["max_consecutive_losses"].append(int(metrics["max_consecutive_losses"]))

    point_bands = {name: _quartile_scores(values, higher_is_better=name not in {"drawdown", "max_consecutive_losses"})
                   for name, values in metric_values.items()}
    for position, item in enumerate(eligible):
        components = {name: point_bands[name][metric_values[name][position]] for name in SELECTION_METRICS}
        item["metrics"]["selection_components"] = {name: int(value) for name, value in components.items()}
        item["metrics"]["selection_score"] = sum(components.values())

    ranked = sorted(eligible, key=lambda item: (
        -item["metrics"]["selection_score"],
        -float(item["metrics"]["net_pnl"]),
        abs(min(0.0, float(item["metrics"]["intraday_drawdown"]
                           if item["metrics"]["intraday_drawdown"] is not None
                           else item["metrics"]["max_drawdown"]))),
        item["index"],
    ))
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    return ranked[0]["index"]


def sweep_payload(summaries, iteration_count, *, partial=False):
    summaries.sort(key=lambda item: item["index"])
    recommended_index = rank_sweep(summaries)
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
