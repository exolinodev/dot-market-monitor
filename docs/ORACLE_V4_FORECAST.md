# Executable forecast contract v4

The v4 writer accepts `schema_version: 2`, strategy `oracle-v4.0.0`, IDs ending
in `-oracle-v4`, and primary market `PF_DOTUSD`. The existing schema-version-1
forecasts retain their original validation and spot-barrier evaluator.

`schema/oracle_forecast_v4.schema.json` requires the original snapshot/method
bindings, an additional canonical **full ledger state document** hash,
`decision`, zero/one `orders`, `management`, the original 1h/4h/12h directional
forecasts, evidence, calibration context and text summary. V4 removes the old
`trade_setup` and alternative prose paths from the executable contract. Prices
and fractions in a submitted forecast are JSON numbers. Python converts a copy
to decimal strings for the engine; archived forecast bytes/values are not repaired.

`FLAT` means no new entry and can accompany management of existing exposure.
`LONG`/`SHORT` requires exactly one matching order. Each bound open object needs
exactly one management action. Quantity is absent from the model schema; unknown
fields, including model-provided size, are rejected. Tick alignment, directional
brackets, fractions, net reward/risk, expiry, risk limits, kill switch and
management coverage are checked by the deterministic ledger planner.

The bound snapshot must be a fresh usable full run for the UTC hour of forecast
creation. The execution context must attest `PF_DOTUSD`, the ledger-state hash,
configuration hash and a usable bid/ask quote timestamped within that hour and no
later than snapshot generation. A fresh-looking previous-hour snapshot cannot
satisfy this gate. Feature citations and reversal gates remain evidence-bound.

## Publication and audit

The GitHub submission envelope remains version 1 and accepts either forecast
schema. The writer reads snapshot, ledger state and frozen genesis configuration
from the declared ancestor `snapshot_commit`, never from model-supplied state or
an uncommitted checkout. It recomputes the plan, verifies the bound state is
reachable in the current replay and persists create-only plan/state evidence.
The existing GitHub-opened-time acceptance, snapshot reservation, immutable
forecast/input archive and readable receipt remain in force.

Publication stages only the forecast artifacts plus `ledger/plans` and
hash-named `ledger/states`. The Oracle producer cannot mutate balances, genesis,
journal or closed trades. The archive guard rejects plans without a published
v4 forecast and independently recomputes each published forecast's plan from its
archived snapshot/state/configuration. All artifacts become visible together in
the protected data commit. A failed local multi-file write is not a published
transaction and must not be promoted.

V4 spot outcomes use evaluator `2.0.0`: they retain directional returns and
excursions for comparison, but never invent spot fills for perpetual orders.
Directional entries report `direction_only`, with no trigger, trade R or target
claims. FLAT reports abstention. Executed returns, fees, funding and balances
come from the separate ledger. V3 remains evaluator `1.0.0`; partitions retain
forecast schema, strategy and evaluator versions.

## Remaining activation work

This contract does not initialize an account or enable trading. The current
production snapshots do not yet contain the required execution context. The
runtime adapter must create it from verified market/ledger evidence and consume
only published, matching forecast/plan pairs. It must also resolve instructions
published after their nominal effective minute without backdating fills, and
respect serialized writer/collector ownership. Prompt 4.0.0, the :05 job change,
funding settlement readback, paper observation and demo/live gates remain
separate rollout work. Direct `scripts/oracle.py write` without bound ledger
inputs fails closed for v4; the supported v4 route is the trusted submission
writer.
