# Oracle v4 paper ledger core

Status (2026-09-21): the production paper account is active. Genesis was
initialized from published 06:45 market evidence (epoch
`2026-09-21T06:45:25.677258Z`, PR #283) and is advanced by every collector and
light run; the v4 writer binds plans to it. `config/ledger.json` deliberately
stays `enabled: false`: it is the reviewed baseline template, while the frozen
copy inside `data/ledger/genesis.json` carries `enabled: true` and the hash that
every plan and state binds. Changing a parameter still requires a new, explicitly
initialized epoch. There are no exchange calls; demo/live execution stays
disabled. v3 forecasts and their spot evaluator remain archived and comparable.

## Parameters and profitability

On 2026-09-20 the user delegated paper parameter choices, prioritizing net
profitability and configurable values. The initial baseline is USD 5000,
1% equity risk scaled by FULL/HALF/QUARTER, notional at most 2x equity,
72-hour hold and 150-minute pending-entry expiry. These are versioned settings,
not claims of optimality. Fees are maker 0.02% and taker 0.05%; setting both to
zero supports sensitivity analysis. Slippage is explicitly zero. Live stages
USD 500/2000/5000 still need separate user approval.

Sizing includes estimated entry and stop fees/spreads in the risk budget by
default (`risk_budget_includes_costs`). This refines the plan's price-only
formula to avoid spending the nominal risk budget before paying costs. The
notional calculation reserves entry-cost headroom. Quantity is fixed when the
plan is accepted: gaps can exceed either planned constraint and generate
`EXECUTION_RISK_VARIANCE`; the simulator never retrospectively resizes a fill.
One position, no pyramiding, three reduce-only targets, tick/quantity rounding,
net T1 reward/risk and the equity-floor kill switch are enforced in Python.
A latched kill switch blocks entries, while existing protection stays active.

Genesis freezes the configuration for one account epoch. Changing parameters
requires a distinct, explicitly initialized epoch/directory; rewriting historic
assumptions would invalidate replay. Compare strategy versions on complete net
results, without treating more frequent trading as a success metric.

## Event and fill contract

Money, prices and quantities persist as decimal strings. Calculations use a
local 34-digit Decimal context. Input timestamps are UTC. Equal-time ordering is
funding rate, spread, instruction, closed candle. Instructions become effective
at the next strictly later minute, including publication exactly on a minute.
Trade/mark candles carry their minute-open timestamps and their OHLC values in
the journal; input and before/after state hashes bind every derived effect.
The journal therefore contains the complete replay input, not external mutable
references. Runtime ingestion still must bind these inputs to collected evidence.

Limit fills require trading through by a tick and use the limit price. A limit
already crossing the bound quote is rejected as incompatible with maker pricing.
Market fills use the first eligible trade open plus/minus half the last recorded
spread; stop entries and protective stops use mark triggers with adverse gaps.
The configured spread floor also applies. Spread freshness and coverage must be
checked by the future runtime adapter; the core only rejects a missing/future
spread. No liquidity, queue position or partial entry fill is simulated.

Stop precedes target if both occur in one candle. If entry occurs inside the
candle, targets are deferred until a later candle: the profitable excursion
might have preceded entry. Protective stops remain active in that entry candle.
A close already requested before the candle, or maximum hold, executes at open
before subsequent intrabar excursions. T1 stop tightening is conservative if
its new stop and a further target can both have traded. These explicit OHLC
approximations must be compared with demo fills before any live rollout.

Missing candles while an order/position is open fail replay instead of silently
skipping possible fills or stops. Exact event retries are idempotent; conflicts,
reordered inputs and changed historical evidence fail verification. The disk
store requires a single serialized writer (future workflow concurrency gate).

## Funding: documented convention and simulation limits

Kraken's official [historical funding API documentation](https://docs.kraken.com/api-reference/historical-funding-rates/historical-funding-rates.md)
defines `timestamp` as “Start of the period to which the funding rate applies.”
Frozen fixtures show hourly rates with the six gaps listed in
`ORACLE_V4_FIXTURES.md`.

The official [linear Multi-M contract specification](https://support.kraken.com/articles/4844359082772-linear-multi-collateral-derivatives-contract-specifications)
(updated 2026-09-08, read 2026-09-21) explicitly states positive funding is paid
by longs to shorts; negative funding reverses the flow. Absolute rate is USD per
contract unit per hour. Accrual is continuous, settled at the hour end or a net
position change, whichever occurs first. Funding itself has no trading fee.
Relative rate converts using the **spot index at rate calculation**, not current
mark or entry price: `absolute = relative * fixed_reference_index`. Thus payment
to a short is `qty * absolute * elapsed_hours`, equivalently
`qty * fixed_reference_index * relative * elapsed_hours`. Published hourly rates
must not be divided by the premium-calculation multiplier of eight again.

`tests/fixtures/funding_convention.json` records manually transcribed source
metadata and examples, not raw HTTP evidence. `test_ledger_funding_convention.py`
checks the engine against them and the original frozen DOT rates. Example 3's
rounded 1.233 USD/minute leads to the page's 36.99 USD half-hour figure; the
unrounded stated formula gives 37 USD, which is the test expectation.
`funding_convention_verified` is now **true** for the documentary convention.
This neither enables the account nor claims demo/account settlement parity.

The core prorates the hourly absolute rate by 1/60 for remaining quantity at the
end of each modelled exposure minute. This explicitly refines the original
whole-interval wording. Intraminute entry/exit times are unknown: entry minutes
can count a full minute and exited quantity counts zero for its exit minute.
It is an approximation, not exchange settlement parity. Missing hour or
unverified convention marks any exposed trade `funding_incomplete`, including
same-minute closure. Cash keeps known funding; unknown funding is never described
as confirmed zero. Such trades are counted separately and excluded from complete
strategy net totals, profit factor, hit rate and expectancy. Overall account
returns are marked provisional. Historical rates must be made available to the
replay in chronological order before processing exposure; late historical-rate
backfill cannot silently rewrite an already published journal.

## Storage, replay and CI

- `ledger/genesis.json`: create-only configuration, epoch and initial state hash.
- `ledger/plans/*.json`: create-only instruction and Python sizing, bound to the
  canonical full state document hash. No model-provided quantity is trusted.
- `ledger/states/*.json`: create-only decision snapshots whose hashes must be
  reachable from genesis and prior journal records.
- `ledger/events/YYYY/MM/DD.jsonl`: append-only inputs, effects and state chain.
- `ledger/trades/*.json`: create-only closed trades, costs and funding status.
- `ledger/state.json`, `ledger/performance.json`: bounded, replaceable views.

`python scripts/ledger_replay.py --data-dir <directory>` verifies all plans,
reachable state bindings, journal effects, trades and rolling views. Explicit
`--rebuild-views` repairs derived views after an interrupted write; it cannot
change the journal or overwrite conflicting closed-trade evidence.

The archive guard checks every commit and the final candidate tree, or the Git
index for staged checks. Rewriting/restoring an artifact within a PR is rejected.
It replays the candidate's exact bytes, independently of unstaged working files.
The existing required `test` job runs this guard on code changes; ledger data
publications are guarded by the producer run before the protected merge (since
2026-09-21 they no longer trigger the tests workflow). The runtime integration opens narrow producer allowlists for derived ledger
views, journal entries and closed trades; genesis and plan ownership remain separate.

Current metrics include minute-close maximum drawdown and strategy net results;
30 complete trades is only a minimum sample flag, never proof of profitability.

`ledger_performance.report` derives the passive benchmark and compact curve from
replayed inputs and MARK effects without changing account state or journal hashes.
The benchmark buys a fixed quantity of PF_DOTUSD at the first observed trade
candle open plus half the recorded spread, rounds down to the configured quantity
step, and includes the taker fee in its one-times-initial-capital budget. It holds
that quantity without rebalancing, adds each observed minute's absolute funding,
and values at the same minute-close mark as strategy equity. Both marked equity
figures exclude hypothetical exit fees/spread on still-open exposure. Entry fees
and spread, funding, net PnL, drawdown and strategy excess net PnL are explicit.
This is a **passive perpetual benchmark**, not spot DOT or a separately executable
account: margin liquidation is not simulated. Missing funding or candle coverage
makes the comparison provisional; missing funding is never silently zeroed.
The old `buy_hold_gross_pnl_usd` remains a labelled gross mark-price proxy only;
use `buy_hold.net_pnl_usd` for the cost-adjusted comparison.

`performance.json.equity_curve` retains at most 192 quarter buckets (48 hours
with complete coverage), each containing the latest minute-close time, strategy
equity and passive equity. The latest bucket can be partial. It exposes truncation
and the count of full-resolution MARK points. Every minute remains reconstructible
from the journal; drawdown uses every minute, not the downsampled curve. CI verifies
this view by recomputing it, and `--rebuild-views` can repair view-only corruption.
The execution context supplies summary performance without the curve to keep
model input bounded.

A synthetic 2,941-minute replay (49 hours plus one minute, constant prices and
hourly funding) produced 192 curve points, a 18,587-byte pretty-printed complete
performance view (10,470 bytes compact), and a 1,007-byte compact account state.
This measures view bounds only; production journal/Git storage growth remains
a rollout acceptance measurement.

Paper activation still requires explicit initialization and prompt/job rollout.
Demo execution and live authorization remain separate rollout gates.
