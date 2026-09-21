# Oracle v4 rollout status — 2026-09-21

This is a deployment/acceptance audit, not a claim that v4 is operational.
Implementation and operational acceptance are tracked separately. The original
scope remains WP1–WP7, including paper, demo and separately approved live stages.

## Production state inspected

At main `5373da84ba44ff7039a839887623adc4e00653da`, the committed full snapshot was
generated at `2026-09-21T00:50:50.073904Z`. It has neither v4 cycle metadata nor a
committed `data/ledger` or `config/ledger.json`. Phase-1 PR #228 remains open and
behind main (head `83071b0e7f7e7b103e2f690b8e5352009f8a608b`). Its last check was
green; a fresh current-branch check will be needed before merge.

A fresh read of the saved hourly ChatGPT task confirms prompt 3.3.2, without
`cycle_boundary_utc` or prompt 4.0.0. The separate, previously prepared editor
change has not been saved. Automatic approval review rejected that persistent
change; the specific user approval request remains unanswered. No private task
prompt or account context is copied here.

## Phase requirements and remaining proof

| Scope | Prepared implementation/evidence | Still required |
|---|---|---|
| Phase 0 | Writer fix, draft cleanup, runner fixtures and interpretation merged (#221/#222/#224/#225) | No new phase-0 code gate identified in this audit |
| WP1–WP3 / Phase 1 | #228: boundary scheduling, light capture, perp candles, incremental funding and lossless history storage; isolated local/runner smokes | Consumer boundary readiness; current-branch merge; Worker deployment; 48 h with >=95% lag <=30 s; light publication <60 s; writer queue <=2 min; four real quarters and actual Git growth |
| WP4–WP5 / Phase 2 | #230/#232/#235: deterministic ledger, v4 writer contract and runtime; source/replay and synthetic writer-to-runtime tests | Ordered deployment; protected production genesis PR; first actual collector advance; current full snapshot with execution context |
| WP6 / Phase 2 | #236: versioned hourly/daily prompts; #238 explicit initializer; #241 timing report; #242 runtime end-to-end tests | Existing task migration to v4 at :05 after prerequisites; daily task migration; real model run, accepted forecast/plan and runtime processing; two weeks paper and >=30 closed trades before performance conclusions |
| WP7 / Phase 3 | #243 through #263: demo transport, durable journal, recovery layers, raw account/market evidence, quantity reconciliation, preflight and account logs; 603 offline Python tests green | Integrated sending/protective-management runner; remaining edit/trigger recovery; margin and net-accounting proof; dedicated demo setup; real fixtures; two-week demo/no-orphan/idempotency/readback acceptance |
| WP7 / Phase 4 | Live remains disabled | Complete prior gates, implement/test live-specific operation, and obtain separate approvals for 500/2000/5000 USD notional |

The unavailable demo host does **not** prevent Phase-1/2 paper rollout. Its public
endpoint returned a documented HTTP 301 to a marketing page from this environment
(see `ORACLE_V4_EXECUTOR.md` and the frozen redirect fixture); no production-host
substitution is authorized. That remains a separate Phase-3 dependency.

The open implementation PRs are a stack, not deployed main changes. Merging them
in dependency order and retargeting their bases remains necessary; a green check
on a child branch does not prove production activation or acceptance.

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
