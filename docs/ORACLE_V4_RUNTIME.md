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
