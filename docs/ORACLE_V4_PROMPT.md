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
instructions from the canonical file. It has 18,829 characters. The existing
ChatGPT editor rejected a 20,822-character v3-plus-guard prompt and accepted an
equivalent integrated version of 19,939 characters; no published product-wide
limit is inferred from that observation. Keep private legacy context separate
and out of this repository.

Phase 1 and the code stack are deployed (#228/#265). Production paper genesis
merged in #283 at 06:50:19 UTC, with identical replay. The existing hourly task's
schedule was changed to minute :05; the task chat confirmed the next occurrence
as 2026-09-21T07:05:00Z after correcting its initial timezone description.
The first real v4 run was observed active at :05 and produced the 08:11 forecast
documented below.

The first current full snapshot was published by run 35571581958 on main
d1f0596, with DOT execution_context status ok and identical account replay.
The hourly task is now saved as DOT/BTC Oracle v4 with the compact v4 copy;
reopening confirmed exact equality (19,672 characters including the preserved,
separate private context). The existing :05 schedule was unchanged. The daily
v4 system check was separately saved and read back with its existing 8:20
schedule. No replacement local automation or exchange execution was introduced.
The preceding real v3 result refused the stale 06:00 snapshot. The first real v4
forecast `20260921T081105Z-5a4bb8b3ee06-oracle-v4` was subsequently accepted by
writer run 35576605314 and published through #298 at 08:13:59 UTC. Collector
35576912142 processed it at the publication-adjusted 08:14 effective boundary.
Replay at main `a3d52722c8db54c16097c73750cad515a5849641` is identical, with a
resting LONG LIMIT, no position or closed trades, and 5000 USD equity. Details
and measured timing shortfalls are in `ORACLE_V4_ROLLOUT_STATUS.md`.

Continue checking subsequent runs for boundary matching, complete management,
valid schema, timely writer acceptance, published forecast/plan, and subsequent
ledger processing. A saved setting or green
unit test alone is not operational acceptance. Timing/storage and paper/demo/live
observation gates in the plan remain required.

Prompt design reference: [OpenAI prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
(code-managed prompts, explicit structure, representative tests and staged rollout).
Task management reference: [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
(existing task prompt/schedule controls and first-run verification).

## Follow-up after rejected management submission

The next real forecast `20260921T091040Z-a314999c44e3-oracle-v4` requested
CANCEL of the resting order. Draft #307 opened at 09:12:51 UTC, 131 seconds
after its declared creation time. Writer 35582001700 correctly rejected it;
the cancellation did not enter the ledger. This historical submission must
not be edited, reopened or resubmitted.

The prompt now requires compact transport prose, complete preparation before
the last real clock read, and direct Create-File → Create-PR submission. The
120-second acceptance limit remains unchanged. The evidence does not separate
model composition time from tool latency; the next real scheduled run must
show whether this mitigation improves acceptance. Runtime executability must
be read from submitted_at_utc/ORDER_ACCEPTED, rather than the nominal plan time.
The revised public copy adds 115 characters; with the existing separate private
context, the expected saved task length is 19,787 characters. Browser save and
readback remain a separate deployment step.
