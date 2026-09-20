# Explicit paper initialization

`scripts/initialize_paper.py` prepares a new local paper ledger. It does not call
an exchange, publish Git changes, alter scheduled tasks or enable live trading.
The collector never initializes implicitly. Production activation still depends
on deployment, consumer/job readiness and the rollout gates in the v4 plan.

## Preview, review, write, publish

Run from the trusted deployed checkout containing current immutable quarter and
funding evidence, with no concurrent local collector/writer:

```sh
python scripts/initialize_paper.py --data-dir data --config config/ledger.json
```

This is read-only. Review the printed plan: initial equity, fees, risk policy,
limits, source bindings, epoch and plan SHA. The configuration is copied into
immutable genesis with `enabled=true`; the baseline file is not changed. All
financial parameters come from that configuration, not from model output or
additional CLI defaults.

To create the reviewed account locally, pass that exact SHA:

```sh
python scripts/initialize_paper.py --data-dir data --config config/ledger.json --write --expected-plan-sha256 <reviewed-plan-sha256>
python scripts/ledger_replay.py --data-dir data
```

Both commands require real tooling; do not simulate their output. The write
operation re-reads current evidence and configuration, rechecks freshness and
requires the same plan SHA. If the quarter changed, obtain a new preview. Any
existing ledger directory, including an incomplete or empty one, is refused;
there is no reset or overwrite switch. For an existing valid account use replay;
repair of its derived views remains the separate explicit `--rebuild-views` path.

Initialization builds the complete genesis, initial hash-bound state, two source-
bound seed events, state and performance in a temporary directory, verifies the
replay, then renames the staged ledger into place. Failure before publication
leaves no partial target ledger. This is a single-writer operation. Repeating it
refuses rather than silently creating a new accounting epoch.

Submit the generated ledger through a normal, protected code/data PR and require
the exact-candidate archive/replay checks. Do not use the collector or Oracle
producer allowlists to create genesis, bypass checks, or push directly to main.
Before merging, verify market evidence is still published and continuous into
the next quarter. After merge, verify an actual collector advance and current
full-hour execution_context before migrating the model job to v4.

## Epoch and evidence

The initial epoch is the current archived quarter's Perp-book reception time.
It must be within the current UTC quarter, at or before the record generation
and present time, and within the configured quote-age limit. A unique historical
funding rate for that UTC hour and the documented funding convention are
required. Symlink/non-file market sources and ambiguous duplicate matches fail.
Both seed events use the runtime adapter's original path/row hashes, so future
collector replays see exact idempotent duplicates, not substitute quotes.

Candles before the epoch are excluded. For an epoch at 20:30:08, the first whole
eligible minute opens at 20:31; the next quarter must supply all closed minutes
through 20:44. Account creation itself places no order, fabricates no fill, and
changes no private account. Real model plans still require main-publication
chronology before execution. The passive benchmark starts at the first modelled
minute; report that start time rather than implying earlier performance.
