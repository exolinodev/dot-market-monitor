# Oracle v4 rollout status — 2026-09-21

This is a deployment/acceptance audit. Production paper processing is active;
exchange execution and full operational acceptance remain outstanding.
Implementation and operational acceptance are tracked separately. The original
scope remains WP1–WP7, including paper, demo and separately approved live stages.

## Production rollout on 2026-09-21

The user authorized production rollout after the saved ChatGPT boundary guard
was verified by reopening its editor. Its equivalent checks were integrated into
the existing recovery paragraph (19,939 characters total); the longer appended
version failed to save. No private prompt/account context is copied here.

Phase 1 merged via #228 at `2026-09-21T06:34:47Z`, commit
`8cddd9b7e5a3738db13b10a6adf4ce702dce2fca`. Cloudflare Worker version
`c8f8f21a-17f3-4c64-87b2-6872e7ef8d4f` was deployed from that main revision.
Public health confirms enabled/configured and crons `59,14,29,44` plus the
five-minute verifier. Existing secrets were retained.

The first production collector
[35569095951](https://github.com/exolinodev/dot-market-monitor/actions/runs/35569095951)
succeeded and published the full 06:00 cycle. The manual deployment-time capture
started at 06:35:15 (lag 2115.025 seconds), correctly marked late. This is rollout
proof, not timing acceptance.

The complete prepared implementation stack merged through release #265 at
`2026-09-21T06:39:37Z`, commit `172c855eb71659f445f1ab5c6a8ead931eab4a3b`.
Its [current-main CI](https://github.com/exolinodev/dot-market-monitor/actions/runs/35569311755)
passed. Original stacked PRs #230–#264 were closed after verifying their commits
were included (the extra #230 merge carried only main data). No original branches
or audit artifacts were deleted. The post-release collector
[35569432428](https://github.com/exolinodev/dot-market-monitor/actions/runs/35569432428)
also succeeded. Production paper genesis, current account context and the
subsequent task migration are separate activation steps; exchange sending remains
disabled.

## Production paper genesis and first advance

The regular 06:45 light run
[35569808440](https://github.com/exolinodev/dot-market-monitor/actions/runs/35569808440)
published on main `751e41d`. It made 12 requests in 0.334 seconds and began
25.392 seconds after the boundary; status ok, fresh, late false. This is one
production sample, not the 48-hour acceptance result.

Explicit initialization from those already-published sources selected epoch
`2026-09-21T06:45:25.677258Z`, USD 5000 and the authorized configurable paper
baseline. Reviewed plan SHA:
`0942b52076f3a2b425e2b92598aaa4ba0f75096ad98d679e4d14142648d2e99f`.
The two funding/spread seed events replay identically to state
`d0d0c6ad47026243f5a7ac743c7e9ff8468368b5eaf82a8a46ae94619cd93d9c`.
The genesis is enabled; the baseline config file remains an inactive template.
Genesis merged through #283 at 06:50:19 UTC. The first 07:00 collector failed
when cached null Perp VWAP/trade counts were converted with float(None). Hotfix
#285 preserves those unknown optional measurements; 29 targeted tests and the
full PR CI passed before its merge.

The retry [35571581958](https://github.com/exolinodev/dot-market-monitor/actions/runs/35571581958)
succeeded and published main `d1f0596`. It is the 07:00 full cycle, generated at
07:10:03 UTC and correctly marked late (575.817 seconds capture lag). Overall
snapshot status is partial; DOT execution_context is ok. The account reached
06:59 UTC with 16 events, USD 5000 and zero trades. Replay from merged main is
identical, state SHA `6573411698e4dda38f074d81249d11a618719f391c4c43626b8ea3dd73a70835`.

After this evidence, the existing hourly task was saved as DOT/BTC Oracle v4
with the compact public v4 prompt and preserved separate private context
(19,672 characters total). Reopening confirmed exact text equality. Its :05
custom schedule was preserved. The daily v4 system check was also saved and
read back exactly, with its existing 8:20 displayed schedule unchanged.
The preceding hourly v3 result correctly refused the stale 06:00 snapshot;
the first successful v4 forecast/writeback is documented below. No exchange
operation is claimed.

The subsequent regular 07:15 light run
[35572069813](https://github.com/exolinodev/dot-market-monitor/actions/runs/35572069813)
published on main `ff13560`: 12 requests, 0.394-second capture, 24.578-second
boundary lag, fresh and not late. The workflow completed at 07:15:42 UTC.
It advanced the account through 07:14 UTC with 33 events and identical replay,
state SHA `a0a2713fbbf4971dd3e093b4784f02eefb31b823a64eacb87688b29ca2712e34`.
The five changed files are intraday and ledger data; no gzip archives changed.

A live Worker log shows the correct `59,14,29,44 * * * *` trigger invoked at
07:14:58.505 UTC and dispatched the 07:15 light cycle. Thus this observed
minute-boundary dispatch is late Cron delivery within :14, not a rejected or
normalized cron string. It leaves only two seconds of prewarming. This sample
meets the 30-second capture target but does not establish the 48-hour percentile.

## First real model order accepted into the paper ledger

The saved :05 task ran on 2026-09-21 and produced forecast
`20260921T081105Z-5a4bb8b3ee06-oracle-v4` at 08:11:05 UTC, bound to the
08:00 snapshot on `459fc23f7efb122b395bd81b5676101c65f5ccbb`.
Submission draft #297 opened at 08:11:29 UTC. Writer
[35576605314](https://github.com/exolinodev/dot-market-monitor/actions/runs/35576605314)
validated and published forecast, receipt and plan through #298 at 08:13:59 UTC,
main `7df8df45e7f0d7b08d644670d0fd163b3b912842`, then closed the draft.
Opening-to-publication took 150 seconds: this does **not** prove the two-minute
writer queue/publication target. No submission draft was manually merged.

The regular 08:15 collector
[35576912142](https://github.com/exolinodev/dot-market-monitor/actions/runs/35576912142)
published `a3d52722c8db54c16097c73750cad515a5849641` and accepted the order.
Runtime uses 08:14:00 UTC, after actual publication, rather than retrospectively
executing the plan's nominal 08:12 effective time. The LONG LIMIT is 1.1545 USD,
stop 1.1475, targets 1.1648/1.1717/1.1827 at 50%/30%/20%, HALF risk and expiry
09:41:05 UTC. Python sized 3030 contracts, 3498.135 USD notional and
24.99556075694 USD estimated stop loss including costs against a 25 USD budget.

Replay at that immutable head is identical: 99 events through the closed 08:14
candle, state hash
`d9c3d159309b3c1e1302208b38d1b55716fd337d47309670c708113f0d4d00c0`.
The order is resting, position is null, closed trade count is zero and equity
is 5000 USD. This proves real model → writer → paper processing, not a fill,
profitability, subsequent model management or exchange execution.

The 08:00 snapshot contains four actual quarters (07:15, 07:30, 07:45, 08:00),
status ok and no missing boundaries. Across the five boundaries 07:15–08:15,
only 2/5 captures meet the 30-second target (lags 24.578, 27.748, 33.635,
30.540, 34.949 seconds). All four light publications took less than 60 seconds
(38/42/47/51 seconds), with no gzip changes. These early samples identify a
prewarming shortfall; the 48-hour timing/storage acceptance remains unproven.

## Production observation through 2026-09-21 18:15 UTC

Read at main `49004bdc0495a251979e32248154907996a3406a` (merge of collector
#357, 18:03:39 UTC) with a local replay of the committed ledger and a fresh
`scripts/rollout_timing.py` window 09:00–18:00 UTC. Numbers below are measured,
not targets. One day of paper operation proves the mechanics, not the strategy.

### First closed paper trade

Forecast `20260921T151021Z-8efa08b7e805-oracle-v4` (created 15:10:21, draft #343
opened 15:11:16, published 15:16:09) placed a QUARTER-risk LONG LIMIT at
1.1725 USD, stop 1.1628 (82.7 bps), targets 1.1880/1.1950/1.2008 at 50%/30%/20%,
stop-after-T1 at entry, expiry 16:30. Against the bound 1.1860/1.1869 quote
Python sized 1140 contracts (12.5 USD risk budget, 1336.65 USD notional, net
T1 reward/risk 1.3715). The runtime accepted the order at 15:17:00 (publication
adjusted), filled it at 15:28 at the limit price (maker fee 0.26733 USD) and
recorded `EXECUTION_RISK_VARIANCE` (estimated stop loss 12.7172 USD against the
12.5 USD budget) because the spread recorded at fill time exceeded the plan-time
spread; the quantity was not resized. T1 filled 570 contracts at 15:52, T2 342 at
15:55 and T3 228 at 16:10. Fees total 0.53925648 USD, funding −0.001236 USD over
42 one-minute accruals, net **+22.441907 USD = 2.0295 R** in 2520 seconds,
status `complete`. Quantity, reward/risk, fees and net result were recomputed
independently from the plan and candle evidence and matched exactly.

Ledger state after the closed 17:59 candle: 736 journal records (674 candles,
45 spreads, 12 funding rates, 5 instructions), equity 5022.441907 USD, no open
order or position, kill switch off, minute-close maximum drawdown 0.06%.
The passive perpetual benchmark stood at 5087.22 USD (excess −64.78 USD); with
one complete trade `sample_sufficient` is false and no performance claim follows.

### Hourly task acceptance

Nine v4 drafts were opened between 08:00 and 18:15 UTC; five were accepted
(#297, #315, #321, #332, #343: 08:11 LONG order, 10:11/11:09/13:12 FLAT,
15:10 LONG order) and four rejected by the writer with recorded reasons:
#307 (09:10, requested CANCEL of the resting order) opened 131 s after its
declared creation, #353 (17:12) 122 s, both beyond the 120-second limit;
#328 (created 12:15:55) exceeded the 900-second `execution_quote_max_age_seconds`
measured from the 12:00:09 quote; #338 (14:09) failed schema validation. The
16:00 round produced no draft (its full publication took 443 s), and the 18:00
round created branch `oracle-submission/20260921T180825Z` without a file or PR.
Five of eleven hourly rounds therefore reached the ledger; the requested CANCEL
never did, and the fail-safe expiry at 09:42 closed that order instead. Accepted
drafts took 150–318 s from opening to main publication, above the two-minute
writer target, largely because the writer's producer step ran the full test
suite (twice, with the staged pre-pass) and shares the collector concurrency group.
The publication fast path below removes that cost.

### Timing and publication

All 36 expected quarters between 09:00 and 18:00 were published. Recorded
acquisition lag was 8.4–9.0 s in 34 quarters; 10:15 (31.1 s) and 15:15
(103.3 s, late runner start) exceeded the 30-second target, giving 94.4%
against the 95% criterion. Light quarters reached main 21–44 s after the
boundary except 15:15 (119 s), with no gzip changes. Full hours reached main
179–443 s after the boundary (median 229 s). The 18:00 run shows the split:
runner ready 55 s before the boundary, collection 50 s, then 170 s of
publication of which `pytest` took 124 s and the two archive-guard passes 16 s
each; PR creation, check and merge took 12 s. At least 48 hours are still
required before either criterion can be evaluated.

### Publication fast path and prompt 4.0.1 (evening of 2026-09-21)

Three changes target the time chain measured above; none touches the ledger,
the contract or the data formats. `scripts/promote_data.py` now verifies each
data commit once (the intraday, ledger-replay and Oracle-archive guards, the
snapshot schema, the three focused runtime test files and the scheduler tests)
instead of a staged pre-pass plus the whole Python suite; locally the full
verification dropped from about 115 s to about 10 s, and `encode_candles` was
rewritten without per-row pandas Series so the archive guard's recomputation of
all 834 archived horizon hashes takes 3 s instead of 9 s (every archived hash
still matches). The writer accepts 180 seconds between declared creation and
the draft's GitHub opening time (`MAX_ACCEPTANCE_DELAY_SECONDS`). Prompt 4.0.1
moves the hourly job to :03 UTC and allows four status polls over 150 seconds
while a collector is still publishing. Expected on the runner: full snapshot on
main about two minutes after the boundary, writer publication under two minutes,
and roughly twelve minutes between the job start and the 900-second quote
deadline. These are expectations from measured components; the next production
day has to confirm them, and the ChatGPT task schedule must be moved to :03 by
hand.

### Repository growth

Fully deltified incremental packs for the three 24-hour windows ending 18:15
UTC on 19, 20 and 21 September are 33.0, 33.4 and 41.6 MB. In the latest window
`data/raw/ohlc_cache.json.gz` (15.1 MB across 27 versions) and
`data/raw/latest.json.gz` (14.8 MB) dominate because gzip prevents Git deltas
on rolling caches; `oracle_feature_history.json.gz` added 4.2 MB (the file holds
118 hourly records of about 88 KB each uncompressed, 65 KB of which are archived
inputs), the plain-JSON snapshot and history files 4.1 MB, and the append-only
ledger journal plus intraday archive 0.6 MB together. A local experiment storing
six hourly versions of each cache uncompressed instead of gzipped shrank the
packed size from 3.42 to 0.61 MB (`ohlc_cache`) and 3.38 to 0.99 MB (`latest`).
The local pack is 272 MB after 762 commits. The Phase 1 storage rule ("no more
replaced bytes than before") is not met yet; the WP3.4 relocation of rolling
caches out of Git, or delta-friendly storage, is the identified remedy.

## Phase requirements and remaining proof

| Scope | Prepared implementation/evidence | Still required |
|---|---|---|
| Phase 0 | Writer fix, draft cleanup, runner fixtures and interpretation merged (#221/#222/#224/#225) | No new phase-0 code gate identified in this audit |
| WP1–WP3 / Phase 1 | #228 deployed; two-minute prewarm (#70864e6); 36/36 quarters published 09:00–18:00 with 34 at <=30 s lag | 48 h with >=95% lag <=30 s (9 h sample: 94.4%); light publication <60 s (one 119 s outlier); writer queue <=2 min (150–318 s before the publication fast path; remeasure); storage rule not met (see growth section) |
| WP4–WP5 / Phase 2 | #230/#232/#235: deterministic ledger, v4 writer contract and runtime; source/replay and synthetic writer-to-runtime tests | Completed via #283/#285 and run 35571581958; continuing runtime evidence required |
| WP6 / Phase 2 | #236: versioned hourly/daily prompts; #238 explicit initializer; #241 timing report; #242 runtime end-to-end tests; first fill and first closed trade observed 15:28–16:10 UTC | Model management path (CANCEL/CLOSE/MODIFY) not yet applied in production; hourly acceptance 5 of 11 rounds; two weeks paper and >=30 closed trades remain required |
| WP7 / Phase 3 | #243 through #312: demo transport, durable journal, evidence-backed recovery including ordinary and stop edits, raw account/market/preferences evidence, reconciliation, preflight, account logs, integrated protective preview and gated single-attempt dispatch; 725 offline Python tests green, all synthetic | Deferred by user decision on 2026-09-21 until paper acceptance: demo account/host access, dedicated private runner, real fixtures, margin and net-accounting proof, two-week demo/no-orphan/idempotency/readback acceptance |
| WP7 / Phase 4 | Live remains disabled | Complete prior gates, implement/test live-specific operation, and obtain separate approvals for 500/2000/5000 USD notional |

The unavailable demo host does **not** prevent Phase-1/2 paper rollout. Its public
endpoint returned a documented HTTP 301 to a marketing page from this environment
(see `ORACLE_V4_EXECUTOR.md` and the frozen redirect fixture); no production-host
substitution is authorized. That remains a separate Phase-3 dependency.

The implementation stack is now deployed via #265. Deployment does not establish
account activation, model execution or the required observation-window acceptance.

## Isolated real-market paper rehearsal

A fresh rehearsal directory outside the repository was created under the parent
workspace (`oracle-v4-paper-rehearsal-20260921`). Public full and current-quarter
collectors ran there using implementation commit
`6a3f3c0` (the account-log branch head before this documentation change).
No repository data, scheduled task, production account or exchange order changed.

The actual initialization preview selected epoch
`2026-09-21T01:28:31.585947Z`, equity USD 5000, 1% risk, 2x notional cap and configured
maker/taker fees 0.02%/0.05%. The reviewed plan hash was
`0af61d7fd1fbb5ad7128945467050101ed54204fb32d335091a958975396f11a`.
The local explicit initialization produced two source-bound funding/spread events
and byte-identical replay, with no trades. This demonstrates the real-source
initialization path; it is not production genesis or model/strategy acceptance.

The real 01:30 quarter started 8.418 seconds after the boundary, made 12 public
requests in 0.136 seconds, and wrote a 7,745-byte latest quarter record with status
ok. The ledger reached the closed 01:29 candle with three total input events,
USD 5000 equity and zero trades. All three input source bindings were verified.
Replaying the same quarter changed none of the ledger files. Quarter-level
execution context was status ok; no current full-hour model context or model
forecast was exercised.

The compact machine-readable result is
[evidence/oracle-v4-paper-rehearsal-20260921.json](evidence/oracle-v4-paper-rehearsal-20260921.json).
Full raw source data and replay artifacts remain in the local rehearsal directory
rather than adding duplicate market archives to Git. The measured duration is
local collection, **not** GitHub queue/publication time. One local quarter cannot
satisfy the 48-hour timing, production publication or paper trading gates.
