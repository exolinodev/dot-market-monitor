# Oracle v4 rollout status — 2026-09-21

This is a deployment/acceptance audit, not a claim that v4 is operational.
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
no first successful v4 forecast/writeback or exchange operation is claimed.

## Phase requirements and remaining proof

| Scope | Prepared implementation/evidence | Still required |
|---|---|---|
| Phase 0 | Writer fix, draft cleanup, runner fixtures and interpretation merged (#221/#222/#224/#225) | No new phase-0 code gate identified in this audit |
| WP1–WP3 / Phase 1 | #228: boundary scheduling, light capture, perp candles, incremental funding and lossless history storage; isolated local/runner smokes | 48 h with >=95% lag <=30 s; light publication <60 s; writer queue <=2 min; four real quarters and actual Git growth |
| WP4–WP5 / Phase 2 | #230/#232/#235: deterministic ledger, v4 writer contract and runtime; source/replay and synthetic writer-to-runtime tests | Completed via #283/#285 and run 35571581958; continuing runtime evidence required |
| WP6 / Phase 2 | #236: versioned hourly/daily prompts; #238 explicit initializer; #241 timing report; #242 runtime end-to-end tests | Hourly/daily migration saved and read back; still required: real v4 model run, accepted forecast/plan and runtime processing; two weeks paper and >=30 closed trades before performance conclusions |
| WP7 / Phase 3 | #243 through #263: demo transport, durable journal, recovery layers, raw account/market evidence, quantity reconciliation, preflight and account logs; 603 offline Python tests green | Integrated sending/protective-management runner; remaining edit/trigger recovery; margin and net-accounting proof; dedicated demo setup; real fixtures; two-week demo/no-orphan/idempotency/readback acceptance |
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
