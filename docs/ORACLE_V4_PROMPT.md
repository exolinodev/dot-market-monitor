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

## Production rollout still required

The existing hourly ChatGPT task was inspected in its Scheduled editor on
2026-09-21 local date. It still holds copied prompt 3.3.2 and only accepts a v3
repository prompt. Merely merging this file does not migrate that task. The
existing daily task is also still v3. No external task changes were saved.

After code deployment, explicit paper initialization, and a verified current
full snapshot with execution_context, replace the existing hourly task's prompt
with the v4 contract and set its hourly minute to :05; read back both fields.
Preserve its existing destination and notification settings. Separately replace
the daily task prompt while preserving its schedule. Private context in the
existing task must never be copied into public repository artifacts. Do not
create a replacement local automation or enable exchange execution.

Check the first real run for boundary matching, complete management, valid
schema, timely writer acceptance, published forecast/plan, and subsequent
ledger processing. Re-read the job and its first results; a saved setting or green
unit test alone is not operational acceptance. Timing/storage and paper/demo/live
observation gates in the plan remain required.

Prompt design reference: [OpenAI prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
(code-managed prompts, explicit structure, representative tests and staged rollout).
Task management reference: [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
(existing task prompt/schedule controls and first-run verification).
