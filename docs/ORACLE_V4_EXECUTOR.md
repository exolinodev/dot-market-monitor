# Oracle v4 executor — implementation status

WP7 now has an offline-tested demo transport, durable attempt records, execution
history/reconciliation, protective-order/action planning, and an isolated paper
CLI in `scripts/execute_orders.py`. It is **not yet an operational exchange
executor**. There is no credentials lookup, deployment workflow, private API
fixture capture or placed exchange order. Demo/live orchestration, durable
account/capture binding, operational recovery, rollout gates and live support
remain required. The sections below describe the implemented layers and their
remaining integration obligations.

## Verified API contract

Official Kraken documentation read on 2026-09-21:

- [Derivatives REST](https://docs.kraken.com/exchange/guides/futures/rest):
  `Authent = base64(HMAC-SHA512(base64decode(secret),
  SHA256(encodedParams + optionalNonce + /api/v3/endpoint)))`.
  Hash the actual URL-encoded form bytes; `/derivatives` is not in the signing
  path. Nonce is optional and omitted. GET parameters go in the query; mutating
  POST parameters go in the body.
- [Send order](https://docs.kraken.com/api-reference/order-management/send-order):
  `lmt`, `mkt`, `stp`; `cliOrdId` is globally unique and at most 100 characters.
  `mkt` is IOC with 1% price protection, so it can partially fill or not fill.
  A plain `lmt` may take liquidity: paper maker fees are not proof of actual fees.
  Stops use `stopPrice` and `triggerSignal=mark`, with no `limitPrice` for a
  stop-market order. `reduceOnly` must be true for exits.
- [Edit order](https://docs.kraken.com/api-reference/order-management/edit-order)
  and [Cancel order](https://docs.kraken.com/api-reference/order-management/cancel-order):
  use `cliOrdId` to address the existing order. These are separate mutations,
  not new entry sends. Stop-edit documentation also describes a `limitPrice`
  requirement; verify stop-market edits in demo before claiming a lossless
  MODIFY mapping. Never silently introduce a stop-limit price.
- [Specific order status](https://docs.kraken.com/api-reference/order-management/get-specific-orders-status):
  returns open orders and orders filled/cancelled in the last **five seconds**.
  An empty result is not proof that a timed-out request failed.
- [Fills](https://docs.kraken.com/api-reference/historical-data/get-your-fills):
  recent results are bounded to 100; historical queries use `lastFillTime`.
  A single readback cannot establish complete fill coverage.

The readback fields also follow
[Open positions](https://docs.kraken.com/api-reference/account-information/get-open-positions)
(`openPositions` array) and
[Wallets](https://docs.kraken.com/api-reference/account-information/get-wallets)
(`accounts` object).

`result=success` means the request was assessed. The nested operation status can
still be `insufficientAvailableFunds`, `iocWouldNotExecute`, or another rejection.
Neither a send acknowledgement nor an open-order response proves a fill.

## Durable attempt contract

The caller must provide one persistent directory for one demo account and keep
it across reruns. It must not recreate the journal to work around an unknown
outcome. State must live on durable storage before enabling the eventual runner;
a fresh ephemeral Actions workspace is insufficient.

Each logical mutation has a stable operation ID. For sends it **must equal
cliOrdId**, so a different operation name cannot bypass the entry journal.
CANCEL/MODIFY IDs must be deterministically derived from the approved management
instruction by the future orchestrator.

Before a mutation the module exclusively creates the operation directory, saves
and fsyncs the exact intended parameters, reads openorders and retains its raw
response, then saves and fsyncs the attempt marker. A duplicate entry already
visible at the exchange stops before sending. Only then is the mutation sent.

Completed attempts return the archived response without network activity.
Reusing an ID with changed parameters fails. Incomplete attempts, crashes,
concurrent attempts and timeouts stop without retry. Even a preflight failure
consumes the operation ID conservatively. Reconciliation/recovery is a separate
remaining implementation; absence from openorders never authorizes resending.
This provides at-most-once submission per retained operation, not guaranteed
execution or exactly-once exchange fills.

Raw HTTP response bytes, HTTP status, SHA-256 and length are retained. API keys,
signing headers and secrets are not journaled. The fixed client only targets
`demo-futures.kraken.com`, refuses unsupported endpoints, disables redirects and
environment proxies, has no automatic retries, and bounds timeouts/response size.
There is no live-host option in this layer.

`entry_request` is a pure adapter from an already verified writer plan/config:
Python's quantity passes through unchanged and LONG/SHORT map to buy/sell. The
caller must still verify immutable forecast/plan/publication bindings. The
adapter does not authorize or place orders and does not manage protective exits.

`capture_readback` creates a new raw snapshot of openorders, fills, openpositions
and accounts. It does not paginate or claim reconciliation. Its directory must
be outside the paper replay archive until exchange evidence schemas and archive
allowlists are implemented. It must never overwrite an earlier capture.

## Remaining integration requirements

- The paper/demo/live CLI must load only persisted, verified forecast/plan pairs.
- Brackets must follow actual partial fills, use reduce-only exits, cancel stale
  siblings, enforce expiry/holding limits and handle reconnects and stop edits.
- Fills need pagination/deduplication, exact account binding, durable private
  state, position/account readback and reconciliation against paper performance.
- Demo fixtures and the two-week demo acceptance window remain outstanding.
- Live requires explicit approval per 500/2000/5000 USD stage and all rollout
  gates. This module does not satisfy those gates or enable live trading.

## Fill-history and quantity reconciliation

`src/exchange_reconciliation.py` adds read-only helpers, still without a runner:

- `capture_fills` retains each raw page plus request cursor, HTTP status, SHA-256
  and length. The manifest selects an inclusive `[since, through]` window and
  requires response server time at or after its end. All futures fills count
  toward the API's 100-row page budget, including other instruments.
- Pages before `lastFillTime` must advance strictly. A short page or a page
  reaching strictly before `since` ends collection. A full page requiring a
  cursor inside the requested window makes coverage ambiguous: more fills could
  share the oldest timestamp and be skipped by the exclusive next cursor.
  Continuing to older pages does not erase that ambiguity. Reaching the page
  budget is also incomplete, never success. Resolving these cases requires a
  stronger exchange history source or continuously retained fill evidence.
- Deduplication uses `fill_id`, preserving all economic identity fields and
  rejecting conflicts. Optional historical `realized_pnl` is not part of fill
  identity: Kraken documents it as null for `lastFillTime` queries. Missing
  client IDs can be recovered only through known exchange order IDs.
- `reconcile` computes per-order filled size and volume-weighted fill price,
  signed adverse basis-point difference versus the paper fill price, and net
  position quantity from a caller-supplied starting baseline. It reports unknown
  fills, orphan resting orders, identity/side/quantity/reduce-only mismatches,
  duplicate exchange mappings, overfills and incomplete history.

The caller must prove that the capture window starts at the trusted baseline,
that all snapshots belong to the same account and observation interval, and that
known-order facts come from verified persisted intents. Readbacks across several
HTTP calls are not atomic. A concurrent fill may require a later capture before
quantities reconcile. This helper neither proves those bindings nor repairs
state, cancels orders, authorizes execution or settles unknown send attempts.
`quantity_reconciled` is deliberately narrower than full account reconciliation.

The fills schema does not provide actual fees or settled funding. A matching
quantity or a `realized_pnl` value is not a verified net return. Reports keep
`actual_costs_verified=false`, `exchange_net_pnl_usd=null` and
`authorizes_execution=false`. Exchange account-log evidence and the eventual
paper-performance integration remain necessary. Existing paper performance and
its deterministic replay are not modified by these helpers.

Tests use synthetic API-shaped pages. No authenticated fixture or successful
real-account pagination is claimed, and this does not start the two-week demo
acceptance window.

## Account-bound execution history

`src/exchange_history.py` supplies the stronger history source for cases where
`/fills` timestamp pagination is ambiguous. It uses the documented
[execution events endpoint](https://docs.kraken.com/api-reference/account-history/get-execution-events)
at `/api/history/v3/executions`. The demo transport now supports read-only
history calls to executions/orders/triggers/account-log on the same fixed demo
host. Executions, orders and triggers have account-bound capture/parsers; account logs are not yet parsed.

The signing path includes `/api/history/v3/...`; it is not a trading
`/api/v3/...` endpoint and has no `/derivatives` URL prefix. This follows the
[official SDK's Sign implementation](https://github.com/krakenfx/api-go/blob/main/pkg/derivatives/rest.go),
which removes only a leading `/derivatives` from the URL path (read 2026-09-21).

The caller provides an independently trusted account UID and a fixed millisecond
window. Every page and nested execution order must match that account. Queries
use ascending order, count 1000, and the opaque continuation token from the
`Next-Continuation-Token` header or `continuationToken` body field. If both are
present they must agree. Request parameters, selected response headers, raw bytes,
status, SHA-256 and byte count are retained for every page.

The API reference does not state whether since/before are inclusive. Capture
requests one extra millisecond at both edges, then filters normalized fills to
the exact inclusive requested window. It rejects finer-than-millisecond window
boundaries, out-of-window data, stale response Date, changing account identity,
incorrect lengths, descending new events, repeated tokens and contradictory
event duplicates. Shared timestamps across pages are valid and do not cause the
ambiguity of timestamp-only cursors. Exhausting the page budget leaves coverage
incomplete. Exhausting the API token chain establishes coverage only according
to the authenticated API's pagination contract, not a claim about undocumented
exchange ingestion delays.

The history response nests execution data under
`event.execution.execution`; its execution UID becomes fill_id and its order
UID becomes order_id. Decimal price/quantity strings are retained without float
conversion. History account binding is retained in the manifest. Per-fill
optional orderData/fee fields stay in raw evidence; this adapter does not yet
assert their accounting semantics or calculate verified net PnL.

`verify_capture` rebuilds the report from the raw page files using a separately
supplied account/window. It rejects hashes, request bindings, derived reports,
extra files and symlinks that differ from that replay. This is local integrity
checking, not independent exchange authentication: the eventual runner must bind
the capture hash to its trusted authenticated acquisition and durable storage.
The capture's normalized fills can feed the quantity reconciler; linking that
report to contemporaneous positions/accounts and protected performance artifacts
is still an orchestration responsibility.

Validation remains offline and synthetic. Real demo credentials, raw authenticated
fixtures and the two-week operational test are still outstanding.

## Protective-order requirements from actual fills

`src/exchange_brackets.py` computes protective order **requirements**, not API
mutations. Its inputs are the original verified entry plan/config, complete
trade-lifetime fills from a flat account baseline, durable client-ID-to-role
ownership, current owned open orders, reconciled signed position, trusted
publication time and a reference time. Unknown entry outcomes, incomplete
coverage, foreign orders/fills, wrong directions, non-reduce-only exits or a
position mismatch stop planning. It creates no exchange identities or fills.

- Stop size is actual entered quantity minus actual exits, never the submitted
  paper size. All desired exits are reduce-only and opposite the entry side.
  Stops retain `triggerSignal=mark` and no stop-limit price.
- T1/T2 allocations floor their shares of cumulative actual entry quantity to
  the configured quantity step; T3 takes the remainder. Actual fills of each
  target are subtracted from that target. Zero-size targets are omitted for
  very small partial entries. Multiple historical exit IDs can share a role.
- Any exit requires cancellation of the remaining entry, preventing deliberate
  replenishment during unwind. Fills already in flight must still be captured
  and reconciled on the next cycle. This is not an atomic exchange OCO bracket.
- A partial STOP/CLOSE requires a market close for the remaining actual position.
  Existing stop protection remains desired while the close is unresolved;
  obsolete profit targets are cancelled. At flat, all owned exit siblings are
  removed and any still-resting entry is cancelled.
- Entry expiry is the earlier of its explicit validity and publication plus the
  configured auto-cancel age. `entry_cancel_required` also blocks a pending,
  not-yet-sent entry after its deadline; an empty cancellation list is not
  permission to submit it. Expiry alone does not flatten an existing position.
- Maximum holding time starts at the first actual entry fill. Expiry, holding
  period and lot/tick rules come from the plan-bound configuration.
- A completely filled T1 applies its explicit trailing stop only once the entry
  is terminal, when further entry fills cannot change the target allocation.
  Partial T1 fills alone do not imply that the whole target executed.
- MODIFY cannot loosen protection or change target allocations after an exit.
  Existing tighter stops are retained. Returned `effective_terms` preserve
  modified prices and a latched close request/reason through later HOLD cycles.
  The runner must durably persist and pass these terms (or reproduce them from
  accepted instruction history); omitting them after a modification is invalid
  orchestration. They are bound to the original plan hash.

Desired `size` is the **remaining** quantity for that logical role. It must not
be copied blindly into editorder: the API order's total size can include already
filled quantity. The future mutation planner must account for filledSize, assign
stable generation-specific client IDs, resolve all prior attempts, and verify
readback after each create/edit/cancel. Stop-market edit behavior remains subject
to the documented demo verification requirement. The desired-role list is not a
safe send sequence; it must not be submitted as a batch without reconciliation.

Tests cover long/short partial entries, rounding dust, target reductions,
entry-remainder cancellation, full-T1 trailing, partial stop/close, flat cleanup,
expiry, holding deadlines, persistent MODIFY/CLOSE terms and rejection of
ambiguous or inconsistent evidence. No actual bracket has been placed. A durable
runner, short reconciliation cadence, API mutation ordering, account/quote
freshness and crash recovery remain necessary before demo activation.

## Executor CLI and one-action planning

`scripts/execute_orders.py` now supplies a real isolated paper execution path and
a read-only demo preview. Full demo/live execution is still unavailable; their
activation is not implied by accepting a `--mode` argument.

```sh
python scripts/execute_orders.py --mode paper \
  --repo . --trusted-head FULL_MAIN_SHA --forecast-id PUBLISHED_V4_FORECAST_ID \
  --boundary 2026-09-21T00:15:00Z --output-dir /path/to/new/paper-candidate

python scripts/execute_orders.py --mode demo --preview \
  --repo . --trusted-head FULL_MAIN_SHA --forecast-id PUBLISHED_V4_FORECAST_ID
```

The caller must fetch and select the trusted immutable main SHA first. It must
be an ancestor of the local `origin/main` reference; the CLI does not fetch or
trust an unmerged checkout. It copies only regular committed blobs into an
isolated tree, verifies ledger replay and original market-source/publication
evidence, validates the forecast/plan/snapshot binding, and uses first-parent
main publication time for instruction eligibility. It never reads credentials.

Paper mode advances the existing committed account through the requested UTC
quarter, using the same runtime as the collector. All eligible persisted v4
plans are considered, not only the forecast selected for reporting. The output
is a new tree with `data/ledger`, required source archives and an
`execution_report.json`. It neither initializes nor resets an account, modifies
the source checkout, publishes data, nor calls an exchange. Existing output
directories are refused. Failed/interrupted outputs must be inspected separately;
the command never overwrites them. Publication of an output remains subject to
the protected producer/archive workflow.

Demo preview reports the verified entry request, management instructions and
publication timing. Its eligibility flag is a time check only, not account,
market, balance or rollout approval. Demo without `--preview` and every live
invocation fail explicitly until the remaining orchestrator/gates exist.

`src/exchange_actions.py` converts verified requirements plus a fresh bound
readback into at most **one** API mutation. Entry cancellation precedes unwind;
a required market close precedes target adjustments; stop protection precedes
profit-target work. It requires stable owned exchange identities and no pending
or unknown prior mutation. Missing previously-open orders must be resolved from
history, not treated as permission to send replacements. Duplicate open roles
also require reconciliation.

A send gets a deterministic <=100-character client ID derived from the original
entry, logical role, desired parameters and verified reconciliation capture hash.
The same input cannot create a second send identity. After a terminal attempt,
reusing that same capture is refused. The caller must durably persist ownership
and attempt evidence before sending and obtain new readback before planning the
next mutation. The capture hash must bind account, positions, fills, open orders,
reference time and effective terms; this library does not establish that binding
by accepting a hash string.

Native edits use `filledSize + desired_remaining_size`. Stop-market edits require
an explicit, independently verified demo capability; the planner does not invent
a stop-limit price or cancel protection to work around missing evidence. These
are planned actions, not automatic authority to call the transport. Durable
orchestration, account/capture binding, private-state persistence, execution
freshness, recovery and demo/live rollout gates remain to be connected.

## Durable demo control journal

`src/demo_journal.py` adds the persistent control state for the future runner.
Initialization is explicit, creates a new private directory, and pins the demo
account UID plus a SHA-256 fingerprint of the API key. It neither verifies that
pair against an exchange by itself nor stores credentials; the orchestrator must
establish the authenticated pairing before initialization. Existing directories
are never reset or rebound implicitly. Keep this private directory on durable
storage, outside the public paper archive and ephemeral Actions checkout.

Use `locked(...)` for the entire prepare/dispatch/readback cycle. A nonblocking
exclusive filesystem lock rejects a second runner. The context verifies account
identity and replays numbered, create-only events and hash-addressed artifacts.
Every event links its predecessor. A separately fsynced head records the committed
sequence and digest, so removing the last event does not silently expose a
previous prepared state. Event/head disagreement, interrupted head replacement,
corrupt artifacts and symlinks fail closed. There is no automatic repair or reset.
This protects local consistency; it cannot detect a coherent replacement of the
entire directory without an independent trusted checkpoint/storage guarantee.

The control lifecycle is:

1. Retain an independently verified observation bound to account, key fingerprint
   and reference time. Time must not move backwards.
2. Prepare a stable mutation bound to the latest capture, immutable plan SHA and
   trusted publication SHA. New sends claim their client-ID ownership durably;
   exits must be reduce-only. One unresolved mutation blocks another preparation.
3. Persist `dispatch` **before** calling the existing transport. That state is
   `unknown` until independent evidence resolves it; the same operation cannot
   be dispatched twice, including after a process restart.
4. Preserve a response as evidence. An acknowledgement alone does not declare a
   fill, open order or terminal outcome.
5. For an unresolved send, a strictly later capture may prove unique open-order
   presence. Client/exchange IDs, instrument, side, type, total quantity, prices,
   reduce-only and trigger must agree with the dispatched intent. Only then is
   ownership marked open and the operation resolved. Empty openorders never
   resolves the attempt and never permits resending.

Observation artifacts are supplied by the trusted acquisition/verification layer;
merely supplying matching account labels to this low-level store does not prove
authentication, freshness or raw-response provenance. The future orchestrator
must construct them from the bound history/readback evidence already described,
and attach the verified plan and publication hashes to planned actions.

Recovery covers open-order presence, full fills and explicit cancellation or
rejection events as described below. Edit/trigger lifecycles and never-sent crash
cases still need specific evidence-based recovery before the CLI can send. There is intentionally
no generic manual `resolved=true` switch. The existing `DemoAttempts` transport
journal remains responsible for retaining exact request/response evidence; the
runner must connect both under the same lock. No demo journal has been initialized
outside isolated tests, and demo/live CLI sending remains disabled.

## Recovery after complete fills

The demo journal now supports `resolve_filled_order` in addition to positive
open-order presence. It can resolve a timed-out/acknowledged send that fully
executed, or update an already known open order to terminal after execution.
The latest observation must bind the exact execution-history artifact; history
must be complete, demo/account-bound, and cover the entire interval from the
send's original observation through the resolving observation.

Every matching fill is deduplicated and checked for order/client identity,
instrument, direction and lifetime. Their exact Decimal quantities must sum to
the dispatched total, and the latest open-order readback must not still contain
the order. If fills omit cliOrdId, a prior proven mapping to the exchange order
ID is required; merely supplying an arbitrary exchange ID is insufficient.
Empty, partial, overfilled, conflicting or incomplete histories do not resolve
an attempt. A never-dispatched intent cannot acquire exchange fills.

When full execution races a pending cancellation, the journal may resolve that
cancel as `order_already_filled`. This means its desired terminal condition is
proven, not that the exchange executed the cancellation. A completed send remains
non-dispatchable, and every decision is reproduced by journal replay.

Any edit in that order's control history blocks this original-size shortcut.
An edit may alter total quantity, including while its result is unknown; the
runner needs separately verified effective-order terms before concluding full
execution. Cancellation/rejection evidence is handled by the order-history layer below;
open-edit/activated-trigger outcomes and never-sent crash recovery remain outstanding. No generic absence-based or manual
resolution override is added, and CLI demo/live sending remains disabled.


## Order-history recovery for cancellation and rejection

`capture_orders` uses the same raw-page/token/account verifier as execution
history, with `/api/history/v3/orders`, `opened=true`, `closed=true`. It normalizes
OrderPlaced, OrderUpdated, OrderRejected, OrderCancelled, OrderNotFound and
OrderEditRejected according to the official
[order events reference](https://docs.kraken.com/api-reference/account-history/get-order-events).
All nested order accounts must match, and an update cannot switch exchange IDs.
`verify_capture(..., source="order_history")` replays this specific source;
execution verification does not silently accept an order-history manifest.

The journal's `resolve_terminal_order` accepts only a selected OrderCancelled or
OrderRejected event in complete account-bound history tied to the latest later
observation. It checks exchange/client identity, side, instrument, reduce-only,
order type and the maximum size authorized by persisted dispatched intents.
Native size reductions are allowed; filled quantity must still exactly match
complete execution history. An open readback, later/same-time contradictory order
event, excess quantity or mismatching fill leaves the operation unresolved.

Positive terminal evidence may also settle a cancellation/edit that lost the
race as `order_terminal`. This records that its target order cannot remain open,
not that the requested modification or cancellation executed. OrderNotFound,
OrderEditRejected and an update alone do not prove termination. Stop orders are
excluded from this resolver until their separate trigger lifecycle is verified.
Open edited orders still need effective-term recovery before further management.

Both history paths remain offline-tested against API-shaped fixtures, not real
authenticated demo captures. The orchestrator must verify raw artifacts before
putting their reports into the control journal; no execution authorization or
CLI sending is enabled by this addition.


## Trigger-history recovery

`capture_triggers` retains account-bound raw pages from `/api/history/v3/triggers`
with `opened=true`, `closed=true`, using the shared token pagination and replay
checks. It normalizes OrderTriggerPlaced, OrderTriggerCancelled,
OrderTriggerUpdated, OrderTriggerActivated and OrderTriggerEditRejected from the
[trigger events reference](https://docs.kraken.com/api-reference/account-history/get-trigger-events).
`verify_capture(..., source="trigger_history")` verifies this source explicitly.
Updates retain old/new terms; rejected edits retain original/attempted terms.

`resolve_cancelled_trigger` accepts an explicit cancellation of a dispatched stop
only when complete trigger and execution histories cover its lifetime and are
bound to the later journal observation. Identity, instrument, direction,
reduce-only, MarkPrice trigger policy and dispatched size/price authorizations
must agree. An open readback, execution, contradictory later event or any matching
activation prevents this recovery. Journal replay repeats these checks.

Activation is not a fill or a terminal trade. The documented activation response
does not supply a child-order ID; this implementation does not invent one.
Activated triggers require independently evidenced child-order/fill reconciliation
before management can continue. Open edits and rejected edit recovery remain
unimplemented. These tests use synthetic responses; authenticated demo fixtures,
operational orchestration and acceptance remain outstanding.
