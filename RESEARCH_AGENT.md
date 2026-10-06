# Research inside Trade-log Analytics

Open **Research** in the dashboard's top bar. It uses the existing dataset catalog,
storage locations, Python environment and backtest form. The research controller
does not replace tuning or the dashboard's independently calculated analytics.

## Connection and models

Install the Codex CLI and sign in with `codex login` using ChatGPT. Research checks
`codex login status` before starting or resuming. API-key login is rejected; API-key
environment variables are removed from child processes. No credentials are copied
into the dashboard, no model fallback occurs, and no new OAuth application is needed
for this local Codex CLI integration.

The lead is `gpt-6-astra`. Data, experiment, strategy and validation roles use
`gpt-5.6-sol`. Availability is verified by actual execution, not by the sign-in
check. An unavailable model, exhausted allowance or missing sandbox setup causes
an explicit failed task. The dashboard does not automatically switch providers.

## Workflow

1. Select a configured dataset, objective, constraints and call budget.
2. A GPT-5.6 data analyst inspects the feed and reports capabilities and problems.
3. Astra chooses experiments or specifies a complete selling strategy journey.
4. GPT-5.6 runs assigned analyses or writes a self-contained Contract v2 candidate.
5. A separate GPT-5.6 reviewer inspects code and execution evidence. Astra chooses
   further investigation, revision, rejection, missing-input status or handoff.
6. **Load candidate into backtest** fills the existing strategy and dataset inputs.
   It does not execute code. Review the assumptions and then use the normal runner.

Tasks communicate through a controller-owned journal and recorded artifact paths.
They run sequentially to keep dependent evidence and model usage auditable; this
release does not parallelise research. Each call is a fresh Codex session, with a
separate writable task directory. Astra has a read-only tool sandbox. Workers have
workspace-write; approval escalation is disabled for unattended tasks. User-level
Codex configuration and exec rules are ignored for these child sessions, while
the existing account authentication is retained. The configured local sandbox
must be available; the app never bypasses it to recover a failed task.

## Controls and limits

- 4–40 model calls per research run (default 12), including lead calls and retries.
- 20-minute per-task timeout and two-hour active-session timeout.
- Stop terminates the child process tree; resume retries an interrupted task and
  preserves previous completed tasks. Failed/retried calls still consume budget.
- No automatic restart after dashboard shutdown. Research is marked interrupted.
- User messages are included in the next lead call; they do not interrupt a running
  worker. Stop first when a change must take effect immediately.
- Research and dashboard backtests cannot start concurrently through the UI.
- Saved state and task artifacts live under the per-user storage root's `research/`.
- Logs and artifact downloads are bounded. A call budget is not a token/spend cap.

## Evidence boundary

Only short positions are requested unless protective long hedges are explicitly
authorised in constraints. Buying back a short position is always permitted.
Parameter sweeps are rejected at candidate handoff; baseline parameters remain
visible for the existing fine-tuning workflow. SENSEX execution studies require
the existing reconciliation reference documented by the project's AGENTS.md.

The controller validates structured replies, artifact containment, candidate
declarations, the independent-review gate and candidate hashes. These are not
proofs of profitability, correct financial logic or safe code. The review is
another model's assessment. Use the existing trusted worker/validator to obtain
authoritative trade analytics; inspect Python before running it.

This release directs agents to separate discovery and validation but does not
physically partition or deny read access to a final holdout. Therefore it must not
be described as a leakage-proof final evaluation system. Final untouched-data
validation belongs in a separately controlled step after tuning.

Prompts and tool output are sent to the model service; the raw dataset is not
automatically uploaded, but agents may include selected rows in their tool output.
Codex's workspace sandbox is used, not a containerised market-data enclave.
Existing global provider restrictions or account policy can still prevent calls.

## Verification

Run `python -m unittest discover -s tests -p test_research.py` with this project's
`src` on `PYTHONPATH`. Tests use a fake agent executor, never paid inference or
fabricated market results. They exercise routing, handoff gates, budgets,
interruption/resume, persistent messages, artifact access and explicit failures.

A live end-to-end research run requires the selected dataset, usable local sandbox,
model access and essential trading assumptions. Authentication alone does not
verify those prerequisites.
