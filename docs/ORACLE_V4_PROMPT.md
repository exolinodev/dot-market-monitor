# Oracle v4 prompt rollout

`CHATGPT_MONITOR_PROMPT.md` is prompt 4.0.2, matched to
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

`docs/CHATGPT_HOURLY_TASK_PROMPT.md` is the compact task copy of prompt 4.0.2.
It preserves the v4 order/management, binding, sizing, cost, REFLOOP and immutable
writer rules, while condensing explanatory feature examples and repeated writer
instructions from the canonical file. It has 19,915 characters. The existing
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
120-second acceptance limit was kept at that point. The evidence does not separate
model composition time from tool latency; the next real scheduled run must
show whether this mitigation improves acceptance. Runtime executability must
be read from submitted_at_utc/ORDER_ACCEPTED, rather than the nominal plan time.
The revised public copy adds 115 characters; with the existing separate private
context, the expected saved task length is 19,787 characters. Browser save and
readback remain a separate deployment step.

## Prompt 4.0.1: earlier start, longer recovery wait, 180-second limit

After the first production day (5 of 11 hourly rounds reached the ledger, see
`ORACLE_V4_ROLLOUT_STATUS.md`) three changes address the time chain rather than
the analysis: the producer publishes the full snapshot about two minutes after
the boundary instead of three to seven (guards only, no full test suite; see
`ORACLE_INTAKE.md`), the writer accepts 180 seconds between `created_at_utc` and
the draft opening (`MAX_ACCEPTANCE_DELAY_SECONDS`), and prompt 4.0.1 describes the
hourly job at **:03 UTC** with up to four status polls over 150 seconds while a
collector is still publishing. The ORACLE CALL, order/management contract,
bindings, REFLOOP and immutable writer rules are unchanged. Both prompt files
change only those three passages, so the compact copy remains a text replacement
for the saved task; the task's schedule must be moved from :05 to :03 in the
ChatGPT editor separately and read back. Until the schedule is moved, the :05
task keeps working with the new prompt; it merely wastes two minutes of the
900-second quote window.

## Writeback recovery, 24 September 2026

The 16:00 UTC collector succeeded, but the hourly model created only its analysis
branch and then reported an execution-environment block. On direct diagnosis in
the same task chat, it corrected that statement: no create-file or create-PR call
had been made, and there was no actual GitHub error. The branch remained at its
analysis SHA and no submission or PR existed. It had also paused its own schedule.

Prompt 4.0.2 makes the existing public paper-simulation writeback authorization
explicit, requires actual calls for a valid round, distinguishes not attempted,
tool error, submitted and persisted, and requires an actual tool error before
claiming a permissions or environment block. Real denials remain binding. The
hourly analysis is forbidden from changing its own schedule/status without an
explicit user request. Forecast schema, strategy, writer checks and trading mode
are unchanged.

A transport test in the original task chat exercised create-branch, create-file,
create-draft-PR and readback successfully. Diagnostic PR #678 contained exactly
one harmless docs JSON file, no production inbox or ledger changes; it was closed
without merge. Independent GitHub readback confirmed commit
674bd9f44cf7c0db7d0c3befba7b1567fd5f2957 and blob
e43c2e2d04d0a79cf0009a4ef674f734302261f5. This proves connector transport only;
a fresh scheduled forecast, writer success, receipt/input verification and ledger
replay are still required to claim end-to-end recovery.
