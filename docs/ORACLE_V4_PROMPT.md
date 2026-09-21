# Oracle v4 prompt rollout

`CHATGPT_MONITOR_PROMPT.md` is prompt 4.0.0, matched to
`schema/oracle_forecast_v4.schema.json`. `docs/CHATGPT_DAILY_CHECK_PROMPT.md` is
read-only daily operations prompt 1.1.0. Runtime execution context now supplies
the five most recently closed trades in deterministic close-time/ID order,
including net R, status, costs and forecast identity. Tick size and quote-age
policy are exported alongside the existing risk controls.

The forecast remains schema version 2 inside submission envelope version 1.
FLAT means no new entry, not closing an existing position. Management must cover
every bound object. No model quantity, unbound account data or v3 trade_setup is
accepted. Directional spot evaluator feedback stays separate from ledger returns.

The unchanged historical v3 replay harness defaults to the archived v3 prompt
`docs/ORACLE_V3_PROMPT_ARCHIVE.md`. It is not a v4 account/prompt backtest.
No actual model-run evaluation or profitability result is claimed by unit tests.

## Task copy and production rollout

`docs/CHATGPT_HOURLY_TASK_PROMPT.md` is the compact task copy of prompt 4.0.0.
It preserves the v4 order/management, binding, sizing, cost, REFLOOP and immutable
writer rules, while condensing explanatory feature examples and repeated writer
instructions from the canonical file. It has 18,714 characters. The existing
ChatGPT editor rejected a 20,822-character v3-plus-guard prompt and accepted an
equivalent integrated version of 19,939 characters; no published product-wide
limit is inferred from that observation. Keep private legacy context separate
and out of this repository.

Phase 1 and the code stack are deployed (#228/#265). Production paper genesis
merged in #283 at 06:50:19 UTC, with identical replay. The existing hourly task's
schedule was changed to minute :05; the task chat confirmed the next occurrence
as 2026-09-21T07:05:00Z after correcting its initial timezone description.
A first real run is still needed to verify effective scheduling.

After verifying the first current full snapshot with execution_context, replace
the existing hourly task prompt with the compact v4 copy and read it back.
Preserve destination and notifications. Replace the daily task prompt separately
while preserving its schedule. No replacement local automation or exchange
execution is introduced. Until this step is verified the saved task remains v3.

Check the first real run for boundary matching, complete management, valid
schema, timely writer acceptance, published forecast/plan, and subsequent
ledger processing. Re-read the job and its first results; a saved setting or green
unit test alone is not operational acceptance. Timing/storage and paper/demo/live
observation gates in the plan remain required.

Prompt design reference: [OpenAI prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
(code-managed prompts, explicit structure, representative tests and staged rollout).
Task management reference: [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
(existing task prompt/schedule controls and first-run verification).
