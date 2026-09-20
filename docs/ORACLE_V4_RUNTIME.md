# Oracle v4 runtime integration (in progress)

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

## Remaining adapter work

This component is not yet called by the collector. Runtime ingestion still must
merge published forecast/plan instructions with evidence-bound candle, spread
and funding inputs, ensure continuous market coverage, produce execution context
and bounded equity curves, and publish replay-verified state under serialized
collector/writer ownership. Public-market evidence and the funding convention
must be verified before enabling an account. No account was initialized by this
change and no exchange execution is enabled.

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
collector still needs to call this adapter and publish both artifacts together.
