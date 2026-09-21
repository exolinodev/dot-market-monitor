# Oracle v4 runtime integration (active in production paper mode)

## Publication chronology

The nominal plan effective minute is not sufficient: a forecast may spend time
in the writer queue or await protected data publication. `ledger_publication`
resolves the plan's first appearance on the first-parent history of the supplied
immutable trusted main revision. It uses the publishing commit's committer time,
not the earlier submission/producer branch author time. This depends on the
existing protected-main workflow and GitHub-created merge commits; it does not
claim that an arbitrary Git author timestamp is a trusted external clock.

The runtime instruction is eligible at the next strictly later minute after the
maximum of declared creation and first-main-publication time. Example: created
20:00:00, merged 20:04:30 -> eligible 20:05:00, never 20:01:00. Its journal input
includes publication commit, UTC timestamp and plan hash. Pure replay checks the
hash and effective-time arithmetic. CI additionally recomputes publication from
Git and rejects missing/forged evidence for production `-oracle-v4` plans.
A supplied trusted revision predating the plan cannot authorize its execution.

If validity ended before eligibility, the engine emits
`ENTRY_REJECTED: expired_before_publication` without creating a resting order.
Management of an already-closed object retains the explicit stale-action no-op.
No past candle or archived forecast is rewritten to accommodate publication delay.

## Activation status

Collector integration operates on the explicitly initialized production paper
account since 2026-09-21 06:45 UTC (see `ORACLE_V4_ROLLOUT_STATUS.md` for the
first accepted order, the first closed trade and the measured timing). The
funding convention is documented and fixture-tested (see `ORACLE_V4_LEDGER.md`);
exchange settlement readback is still pending. The replay-derived compact equity
curve and cost-adjusted passive perpetual benchmark are implemented. Prompt 4.0.1
and the :03 hourly task are live. Still open: the 48-hour timing/storage
acceptance, two weeks of paper observation with at least 30 closed trades, and
the demo/live gates. Nothing here performs exchange execution.

Operational limit worth knowing: `advance` requires continuous closed candles
while the account exists. If a quarter is never published (for example two
failed Kraken chart fetches in one round), every later light and full run fails
until that quarter is captured. Recovery is currently manual: dispatch
`market-data.yml` with `run_kind=light` and the exact missing `boundary_utc`,
oldest first. The Worker only reconciles the current round.

## Market evidence adapter

`ledger_market` converts the immutable quarter candle arrays, received-time
perpetual spread and monthly funding rows into ordered ledger inputs. Each input
binds its relative source path and a canonical row hash. The quarter's derived
`ledger` summary is excluded from the market hash to avoid self-reference; all
market/source/candle fields remain bound. CI extracts the source files from the
same Git candidate tree and rederives inputs, catching invented prices even if
the internal journal replay itself is consistent.

A batch ending at 20:15 includes closed candles through the 20:14 open. A quote
received at 20:15:08 remains archived but cannot affect any of those candles; it
enters a later batch between the appropriate minute opens. Funding initialization
mid-hour retains the true hour-start label while applying the rate event no
earlier than the account epoch. No elapsed pre-account funding is charged.

`ledger_runtime.advance` operates only on an explicitly initialized account. It
loads matching published forecast/plan pairs, uses first-main-publication
eligibility, merges all eligible market inputs in time order, and writes through
the replay-verifying store. Exact reruns are no-ops. Changed already-used evidence,
backdated newly discovered inputs and a missing final closed minute fail before
normal publication. An optional pending quarter can receive the derived account
summary before its first immutable append; market hashes remain stable. The
collector calls this adapter before the quarter's first append and stages both
artifacts for the same protected producer commit.


## Collector and consumer integration

An existing `data/ledger/genesis.json` activates account processing; collectors
never create an account implicitly. An active account requires an explicit
cycle. Light runs finish the account before archiving the enriched quarter. Full
runs fetch incremental funding and perpetual history first, then finish the
quarter, attach the account summary and emit `execution_context` in the snapshot
and bounded consumer projection. Repeated quarters remain immutable.

Execution context includes canonical account/configuration hashes, full bounded
state, bid/ask with reception timestamp, risk parameters, funding prediction and
performance. Financial state uses decimal strings, preserving its hash through
snapshot rounding. Snapshot validation checks the full account hash and internal
state/configuration hashes. Taker round-trip cost estimate is two taker fees plus
the recorded/floor spread; funding is explicitly excluded from this estimate.
`execution_quote_max_age_seconds` is configurable (initially 900 seconds, one
collection interval), checked both when building context and when accepting a
forecast. A missing boundary mark or expired quote makes context unavailable.

Collector/light producers may change only ledger state/performance, append-only
journal files and new closed trades. They cannot create genesis or plans. The
writer owns plans and decision-state snapshots. Both workflows retain the shared
`dot-market-data` concurrency group. Light publication runs the market archive
guard, exact-candidate ledger replay/source verification and focused runtime
tests before attaching its successful check. Producer commits still fail if
main advances during production; no stale account is rebased over newer state.
