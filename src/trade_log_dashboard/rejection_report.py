"""Bounded, sequential rejection evidence using the ranking predicates themselves."""
import json
import re


def build_rejection_report(connection, path, clauses, values, entry_expression,
                           drawdown_expression, query, require_robustness):
    stages, flags, bindings = [], [], []
    offset = 1  # Ranking binds the input path first.
    observed_columns = []
    for index, clause in enumerate(clauses):
        arguments = values[offset:offset + clause.count("?")]
        offset += len(arguments)
        bindings.extend(arguments)
        names = re.findall(r'"((?:[^"]|"")+)"', clause)
        names = list(dict.fromkeys(name.replace('""', '"') for name in names))
        for name in names:
            if name not in observed_columns:
                observed_columns.append(name)
        field = ", ".join(names) or ("Entry time (minutes)" if "entry_minutes" in clause else "Drawdown")
        if "IS NOT NULL" in clause:
            reason = f"{field}: a non-missing metric is required."
        elif arguments:
            operator = next((op for op in (">=", "<=", ">", "<", "=") if op in clause), "matches")
            reason = f"{field}: must satisfy {operator} " + ", ".join(str(value) for value in arguments) + "."
            if " OR " in clause or " AND " in clause:
                reason = f"{field}: must match the selected values or ranges ({', '.join(str(v) for v in arguments)})."
        elif "'succeeded'" in clause:
            reason = f"{field}: status must be succeeded."
        else:
            reason = f"{field}: must be greater than zero."
        stages.append({"filter": field, "requirement": reason, "examples": []})
        flags.append(f"coalesce(({clause}), false) AS audit_pass_{index}")
    def quote(name):
        return '"' + name.replace('"', '""') + '"'
    observed = list(dict.fromkeys(observed_columns + ["entry_minutes", "ranking_drawdown"]))
    sample = "to_json(struct_pack(" + ", ".join(f"{quote(name)} := {quote(name)}" for name in observed) + "))"
    failure = "CASE " + " ".join(f"WHEN NOT audit_pass_{i} THEN {i}" for i in range(len(stages))) + f" ELSE {len(stages)} END"
    results = connection.execute(f"""
        WITH source AS (
          SELECT *, {entry_expression} AS entry_minutes,
                 {drawdown_expression} AS ranking_drawdown FROM read_parquet(?)
        ), checks AS (SELECT *, {', '.join(flags)} FROM source),
        failures AS (SELECT *, {failure} AS failed_stage FROM checks)
        SELECT failed_stage, count(*), min(CASE WHEN failed_stage < {len(stages)} THEN {sample} END)
          FROM failures GROUP BY failed_stage
    """, [str(path), *bindings]).fetchall()
    counts = {int(index): (int(count), example) for index, count, example in results}
    total = sum(count for count, _ in counts.values())
    remaining = total
    for index, stage in enumerate(stages):
        removed, example = counts.get(index, (0, None))
        stage.update(input_count=remaining, removed_count=removed, remaining_count=remaining - removed)
        if example and removed:
            stage["examples"] = [{"reason": stage["requirement"], "values": json.loads(example)}]
        remaining -= removed

    # Reuse the actual base and robustness CTEs, without percentile sorts or Top-N.
    prefix = re.split(r"\beligible AS(?: MATERIALIZED)?\s*\(", query, maxsplit=1)[0].rstrip().rstrip(",")
    robust_examples = []
    if require_robustness:
        tail = re.split(r"\beligible AS(?: MATERIALIZED)?\s*\(", query, maxsplit=1)[1]
        condition = re.search(r"FROM robustness\s+WHERE\s+([^\n]+)", tail).group(1)
        base_count, passed, example = connection.execute(prefix + f"""
          SELECT count(*), count(*) FILTER (WHERE {condition}),
                 min(to_json(struct_pack(entry := entry_start, pnl := net_pnl,
                   before_variants := before_variant_count, after_variants := after_variant_count,
                   before_pass := before_pass, after_pass := after_pass)))
                   FILTER (WHERE NOT coalesce(({condition}), false)) FROM robustness
        """, values).fetchone()
        if example:
            evidence = json.loads(example)
            reason = ("No same-parameter entry variants exist in the configured ranges."
                      if not evidence["before_variants"] and not evidence["after_variants"]
                      else "Entry variants did not meet the selected side and pass requirement (each passing variant retains at least 70% of base P&L).")
            robust_examples = [{"reason": reason, "values": evidence}]
    else:
        base_count = connection.execute(prefix + " SELECT count(*) FROM base", values).fetchone()[0]
        passed = base_count
    base_count, passed = int(base_count), int(passed)
    return {"source_count": total, "base_rows": remaining, "base_combinations": base_count,
            "grouped_rows": remaining - base_count, "stages": stages,
            "robustness": {"filter": "Entry-time robustness", "input_count": base_count,
                "removed_count": base_count - passed, "remaining_count": passed,
                "status": "enforced" if require_robustness else "not_required",
                "examples": robust_examples}, "eligible_count": passed}
