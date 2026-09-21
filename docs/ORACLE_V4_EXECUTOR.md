# Oracle v4 executor — implementation status

WP7 now has an offline-tested demo transport, durable attempt records, execution
history/reconciliation, protective-order/action planning, and an isolated paper
CLI in `scripts/execute_orders.py`. It is **not yet an operational exchange
executor**. A separate read-only CLI can now acquire private demo evidence from environment
credentials. There is no deployment workflow or placed exchange order. Demo/live orchestration, durable
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
host. Executions, orders, triggers and account logs now have account-bound capture/parsers; account-log ID pagination is described separately below.

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
rejection events as described below, plus explicit abandonment before the durable
dispatch barrier. Edit/activated-trigger lifecycles still need specific
evidence-based recovery before the CLI can send. There is intentionally
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

Any edit in that order's control history, except an explicitly abandoned
never-dispatched edit, blocks this original-size shortcut.
An edit may alter total quantity, including while its result is unknown; the
runner needs separately verified effective-order terms before concluding full
execution. Cancellation/rejection evidence is handled by the order-history layer below;
open-edit/activated-trigger outcomes remain outstanding. No generic absence-based or manual
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


## Private demo evidence capture

`scripts/capture_demo_evidence.py` is a read-only acquisition entrypoint. Supply
`KRAKEN_DEMO_API_KEY` and `KRAKEN_DEMO_API_SECRET` through the private runner's
secret environment (never as command arguments, in Git, or in chat). Use a
read-only demo key. The fixed transport cannot target the live host.

```sh
python scripts/capture_demo_evidence.py \
  --account-uid VERIFIED_DEMO_ACCOUNT_UUID \
  --since 2026-09-21T00:00:00Z --through 2026-09-21T01:00:00Z \
  --output-dir /private/demo-evidence/new-capture
```

The output parent must exist outside a Git worktree; the capture directory must
be new. Directories/files are owner-only. A credential-free public market probe runs first. If available, one client/key fetches raw open orders,
recent fills, positions and accounts, followed by paginated execution, order and
trigger histories for the explicit fixed window. Every history checks the
independently supplied account UID. The bundle records the API key fingerprint,
raw-file hashes, window and pagination coverage; it never stores signing headers
or credentials. Stdout contains only the bundle hash and coverage/authority flags.
Failures leave private evidence in place and do not retry or overwrite it.

`verify_bundle` requires the separately retained acquisition hash and expected
account/key identity, verifies every file and replays each history. A locally
recomputed hash is integrity evidence, not proof of remote authenticity. The
trusted acquisition process must retain the returned hash before later importing
any report into the journal. Optional `--journal-dir` now imports under the existing journal lock as described below.

The readbacks are sequential, not atomic, and may be newer than the requested
history window. Even complete history does not establish contemporaneous position
reconciliation or authorize trading. Pagination-budget exhaustion is reported as
incomplete evidence. These bundles stay outside the public replay archive; do not
upload them as public CI artifacts. Real capture, account reconciliation, actual
fee/funding accounting and demo trading acceptance remain outstanding.


## Acquisition-to-journal integration

Omitting `--through` derives the history endpoint from the latest readback
`serverTime`, rounded upward to a millisecond. Explicit historical windows remain
supported for evidence-only captures. Add `--journal-dir /private/existing-journal`
to hold its account/key-bound lock across acquisition and import. The journal must already exist outside Git unless explicit first-time setup is
selected as described below. Existing journals are never reset.

`import_observation` requires complete execution/order/trigger histories covering
all four readback server timestamps. Any history event between the earliest
readback and the history endpoint rejects import: sequential snapshots spanning
account activity cannot safely settle an unknown order. Obtain a later capture;
no exchange mutation is retried. Empty history establishes only the documented
pagination contract, not an undocumented exchange ingestion-lag guarantee.

The journal retains a full raw bundle under `evidence/<bundle_sha256>`, plus the
exact parsed history reports and readback observation as hash-addressed artifacts.
It revalidates the copied bundle before appending a capture event. Every journal
replay verifies the raw source again and requires the derived observation and
history hashes to match. Tampered/missing evidence and symlinks stop replay.
Numbers in readbacks retain their JSON decimal spelling instead of float rounding.
An interrupted copy remains incomplete evidence and is not automatically repaired.

This closes the authenticated acquisition/import path when called by the CLI
with its single credential-bound client. The library caller remains responsible
for trusted acquisition provenance. The resulting observation explicitly keeps
`quantity_reconciled=false` and `authorizes_execution=false`: trusted initial
position baselines, account/quantity reconciliation, freshness limits and mutation
orchestration remain required. No real journal has been initialized or populated
outside isolated tests.


## Flat demo baseline and position reconciliation

An existing exchange account is not presumed flat. First-time local journal setup
is explicit and follows authenticated read-only acquisition:

```sh
python scripts/capture_demo_evidence.py \
  --account-uid VERIFIED_DEMO_ACCOUNT_UUID --since BASELINE_WINDOW_START_UTC \
  --output-dir /private/demo-evidence/first-capture \
  --journal-dir /private/demo-journal \
  --initialize-journal --establish-flat-baseline --reconcile
```

The parents must exist outside Git. Initialization is exclusive, refuses any
existing journal before network access, and creates only local private files; it
does not create/fund/reset an exchange account. Baseline establishment requires a
raw-verified latest observation with no open orders and no nonzero position in
**any** instrument, before any journal operation. It is recorded once and cannot
be replaced. If setup fails after creation, inspect the retained journal and
capture; do not delete/reinitialize them to bypass unresolved state.

Later captures use the same journal with `--reconcile`, omitting initialization
and baseline flags. Their `--since` must reach at least the baseline timestamp.
History is filtered strictly after that baseline, avoiding pre-baseline fills.
The report derives owned orders and authorized quantities from durable dispatched
intents; callers cannot supply a replacement starting quantity or known-order map.
Complete baseline-to-observation execution coverage is required.

The report checks signed fill totals against the actual position, fill identity,
size ceilings, native filled-size/history equality, orphan orders/fills, missing
owned open orders, terminal orders still present, unresolved operations and foreign
instrument activity. Matching net quantities cannot conceal external trades.
Edited-order effective terms still require recovery and produce a discrepancy.

Reports bind the baseline, observation and current control-state hash; journal
replay recomputes them from raw sources and persisted intents. Any later capture
or control event invalidates the latest report pointer. CLI output provides the
report hash, reconciliation boolean and issue count; detailed private evidence
stays in the journal. A quantity match still has `authorizes_execution=false`,
`actual_costs_verified=false` and no verified net PnL. Freshness/risk gates,
effective-term verification, account-log costs/funding and sending orchestration
remain necessary. All verification so far uses isolated synthetic clients, not
real demo credentials or placed orders.


## Journal-bound entry preflight

`execute_orders.py --mode demo --preview` accepts `--journal-dir`,
`--account-uid` and `--key-fingerprint` together to run `entry_preflight` while
holding the existing journal lock. This adds an actual integration path from
immutable Git publication and raw acquired account evidence to the entry checks;
it does not send, initialize, resize or modify anything.

The check requires the exact current **locally fetched** `origin/main` SHA, a
replayed current positive quantity reconciliation and its unchanged control-state
binding. The operator must fetch before invoking it. A later known main revision
cannot be omitted by passing an older ancestor. Newer or same-time conflicting
published plans supersede an older entry candidate.

`config/demo_executor.json` is read from that committed SHA, never the worktree.
It leaves demo execution disabled and configures a 30-second maximum readback age
and 10-second maximum readback span. Future timestamps, excessive spans and stale
readbacks fail checks. The published quote retains its ledger-configured age
limit; current captured demo quotes are additionally required. Entry eligibility comes from the verified first-parent publication;
expiry is the earlier of explicit validity and publication plus auto-cancel age.
A used client identity or a non-flat demo account cannot submit a fresh entry.

The flex wallet parser follows the official wallets reference: portfolioValue
plus separately reported unrealizedFunding, capped by marginEquity, supplies a
conservative usable-capital bound. Available margin must be positive. The current
and baseline wallet also support the configured account-equity floor. The Python
sizer recomputes a quantity ceiling using the smaller of current paper equity and
usable demo capital, retaining the kill switch, costs, stop bounds, leverage cap
and reward/risk check. The request keeps the exact published quantity; exceeding
the current ceiling rejects it rather than silently resizing it.

This is still preflight evidence, with `authorizes_execution=false`. It does not
verify an exact exchange initial-margin requirement. Account-log flow classification
is now required as described below; actual net accounting is still unverified. These
limits are explicit in the output. Successful real demo market acquisition,
cash-flow/cost accounting, effective-term recovery and final dispatch orchestration remain
required before demo sending. A preview cannot be cached as permission: the
future sender must rerun checks against its held lock immediately before dispatch.


## Public demo market probe and observed availability

On **2026-09-21 at 01:06:51 UTC** (response Date), a credential-free request from
the local environment to the documented demo ticker URL returned **HTTP 301** to
`https://www.kraken.com/gb/features/futures`, with 167 bytes of HTML. The official
[Futures introduction](https://docs.kraken.com/exchange/guides/futures/introduction)
still documents `https://demo-futures.kraken.com/derivatives/api/v3/tickers` as the
demo REST URL. This is evidence of unavailability on this network at that time,
not proof of a global outage or a replacement API host. No redirect was followed,
no production host was substituted and no credentials/private calls were used.
The byte-exact response and selected headers are retained under
`tests/fixtures/demo_market_redirect/`; its manifest replays in CI.

The read-only diagnostic can be run without credentials:

```sh
python scripts/capture_demo_market.py --output-dir /private/new-demo-market-probe
```

It returns nonzero when the host does not supply a valid PF_DOTUSD market. The
transport uses only the fixed demo host, no auth headers, redirects, environment
proxies or retries, and bounded requests. New account captures run this probe
**before** private calls and retain a failed probe without creating a complete
account bundle. A redirect therefore cannot send credentials to another host.

Bundle version 2 adds raw public tickers/instruments, normalized bid/ask/mark,
server timestamps, suspended/post-only/tradeable status and tick/contract sizes.
Replay checks them against the raw response. Existing version-1 bundles remain
replayable but cannot satisfy the new current-market preflight requirement.

Preflight checks both market timestamps against the committed age limit, requires
an active market and matching instrument configuration, and sizes against the
**captured demo spread**, retaining the original published quantity. A wider
spread, market halt or changed tick size can reject an otherwise eligible entry.
Margin tiers remain raw evidence only; no undocumented margin formula is inferred.
Successful market payload tests are synthetic until the documented demo endpoint
returns usable real data. Real demo acceptance has not started.

The official [instruments reference](https://docs.kraken.com/api-reference/instrument-details/get-instruments)
distinguishes platform `marginSchedules`, professional `marginLevels` and
`retailMarginLevels`. The authenticated
[leverage preferences](https://docs.kraken.com/api-reference/multi-collateral/get-leverage-settings)
are not yet captured. The corresponding setter documents that specifying
`maxLeverage` selects isolated margin. Neither the public tier list nor positive
`availableMargin` establishes the applicable account requirement. The documented
[portfolio-margin simulator](https://docs.kraken.com/api-reference/account-information/calculate-portfolio-margin-pnl-and-greeks)
is explicitly restricted to pre-production environments; its availability for
this demo account is unproven. Do not assume it provides a usable margin oracle.
Real account classification, preferences and applicable requirement evidence
remain prerequisites for the sending path; no preference is changed here.

## Recovery of an intent that never reached dispatch

`scripts/recover_demo_journal.py` inspects unresolved operations under the existing
account/key-bound journal lock. Without a mutation argument it leaves the event
chain unchanged. `--abandon-prepared OPERATION_ID` records a create-only
`abandon_prepared` event only while that operation is still `prepared`:

```sh
python scripts/recover_demo_journal.py --journal-dir /private/demo-journal \
  --account-uid <demo-account-uuid> --key-fingerprint <sha256-of-api-key>
python scripts/recover_demo_journal.py --journal-dir /private/demo-journal \
  --account-uid <demo-account-uuid> --key-fingerprint <sha256-of-api-key> \
  --abandon-prepared <operation-id>
```

The sender contract requires `before_dispatch()` to be durably committed before
any network request. Once that barrier exists, abandonment is refused even if
the process crashed before opening a socket. Unknown and acknowledged requests
still require positive exchange evidence. An incomplete journal head/chain also
blocks recovery; the command cannot repair or reset it.

An abandoned operation is resolved with outcome `not_dispatched`. Its ID remains
consumed permanently. For a send, ownership is terminal without an exchange ID;
for an edit/cancel, the existing order is unchanged. Reconciliation excludes an
abandoned send from known exchange intents, so any actual matching fill/order
remains an unexplained discrepancy. An abandoned edit cannot change effective
quantity or price, expand terminal-recovery bounds or prevent original-size fill
recovery. The event invalidates the cached position report. New work must pass
fresh reconciliation and normal preflight; this local recovery authorizes no
exchange request and does not enable demo execution.


## Account-log evidence and external-flow gate

`src/account_log.py` implements the documented
[account log endpoint](https://docs.kraken.com/api-reference/account-history/get-account-log),
whose response is `{accountUid, logs}` rather than execution-history `elements`.
It requests all entry types, ascending order, count 500 and conversion details.
The initial fixed millisecond window is expanded by one millisecond at each edge,
then locally filtered to the requested inclusive window. RFC3339 entry dates
retain supported microsecond precision; finer nonzero precision is rejected.

Pagination uses documented inclusive `from` IDs: each next request starts at the
last ID plus one while keeping the original time window. Even a short page is
followed; only an explicit empty page establishes pagination coverage. Duplicate
IDs/bookings, decreasing chronology, wrong accounts, out-of-window entries,
invalid response Date, changed request bindings or exhausted page budgets cannot
claim complete evidence. Replay verifies every raw page and reconstructs the
manifest. Coverage follows the API contract, not an undocumented ingestion-delay
guarantee. IDs need not be contiguous within a time-filtered query.

Bundle version 3 adds this source to private acquisition and journal replay.
Version-1/2 bundles remain replayable. Any account-log activity overlapping the
readback interval rejects observation import, just like executions/order changes.
The stored rows retain exact fee, realized-funding, realized-PnL, old/new balance,
asset, collateral, contract, wallet and optional conversion fields. Null is not
converted into zero, and wallet balance changes are not confused with position
size changes. No total in USD is inferred from potentially duplicated or
non-USD wallet/position rows; `actual_costs_verified` remains false.

Entry preflight now requires complete account-log coverage from the flat baseline
through its current observation. Deposits, withdrawals and documented transfer
categories produce `external_account_flow`; unknown categories produce
`unclassified_account_activity`. A futures-trade row must name PF_DOTUSD and link
to an execution in the acquired history; a funding-rate-change row must name
PF_DOTUSD. Otherwise it remains unclassified. Only a fully covered interval with
no external/unclassified activity sets `account_flows_verified=true`—a statement
about declared API activity, not a net-profit calculation. A matching flat
position cannot hide a deposit. Detected external flows do not automatically
reset the baseline or relax the equity floor.

Tests are synthetic, including the wallet/position field examples. Authenticated
account-log fixtures, currency/booking reconciliation, actual fee/funding totals
and verified exchange net performance remain outstanding while the public demo
host is unavailable from the current environment.

## Evidence-backed recovery command

`recover_demo_journal.py` now exposes the existing journal validators for a
previously dispatched mutation. It still makes no exchange request. Select
exactly one action:

| Argument | Required positive proof |
|---|---|
| `--resolve-present OPERATION_ID` | Exact open-order identity, quantity and policy after the send |
| `--resolve-filled CLIENT_ID` | Complete execution history totaling the dispatched quantity, with no open order |
| `--resolve-cancelled CLIENT_ID` | Explicit ordinary-order cancellation plus consistent fills |
| `--resolve-rejected CLIENT_ID` | Explicit ordinary-order rejection plus consistent fills |
| `--resolve-trigger-cancelled CLIENT_ID` | Explicit cancellation of a stop that never activated |

All five require `--capture-sha256` equal to the latest journal observation and
`--exchange-order-id`. The three terminal-history actions additionally require
`--event-id`; that option is rejected for presence/full-fill recovery. The
history hash is obtained from the observation itself, never supplied separately.
The observation must have an acquired bundle whose raw files replay and bind to
the expected account/key. A standalone normalized JSON capture is insufficient.

```sh
python scripts/recover_demo_journal.py --journal-dir /private/demo-journal \
  --account-uid <demo-account-uuid> --key-fingerprint <sha256-of-api-key> \
  --resolve-present <operation-id> --capture-sha256 <latest-observation-sha256> \
  --exchange-order-id <exchange-order-id>
```

Each successful command appends one validated event under the journal lock and
invalidates any cached reconciliation. A repeated successful resolution is
refused without another event. Empty readbacks, partial fills, changed identity,
stale observations and incomplete histories leave the unknown operation
unresolved. Full fills after unresolved edits and activated-stop child-order recovery
remain unsupported by their underlying validators. Run fresh reconciliation
before later dispatch; the output always says `authorizes_execution: false`.

Integration tests exercise acquisition of synthetic raw API responses, import,
CLI resolution and restart replay for all five paths, plus refusal cases. These
are offline integration evidence, not proof of a working demo account or actual
exchange executions. This command does not enable the demo/live sender.


## Proven effective terms after ordinary limit edits

The journal now accepts `edit_resolved` for a dispatched ordinary limit edit
whose exact outcome is supported by the latest observation. The observation
binds complete account-specific order history over the edit lifetime. Recovery
requires a unique `OrderUpdated` or `OrderEditRejected`, matching old terms and
requested new/attempted terms, and an exact current open-order readback. Other
lifecycle events for the same order during that interval make recovery ambiguous.
Changed account, client/exchange identity, direction, reduce-only policy, order
type, price, quantity or decreasing cumulative fills are refused. Stops and
already-terminal orders still require their separate recovery paths.

The recovery command adds `--resolve-edit-applied OPERATION_ID` and
`--resolve-edit-rejected OPERATION_ID`, each requiring `--capture-sha256`,
`--exchange-order-id` and `--event-id`. The exchange ID must already belong to the
journal's proven open order. Acquired raw evidence remains mandatory in the CLI.
A successful resolution invalidates cached reconciliation; it sends no request.

`demo_edits.effective_params` derives current size/price by replaying proven edit
outcomes in journal order. Applied edits update terms, rejected or never-sent
edits preserve them, and an unresolved edit still blocks effective-size claims.
Position reconciliation uses the derived size. Full-fill recovery can therefore
resolve a later terminal fill at the proven edited quantity, rather than the
original send quantity. Successive edits must each start from the previous
proven terms. Raw history and original intents remain immutable.

Fifteen new synthetic tests cover applied/rejected CLI recovery followed by
position reconciliation and a later complete fill, successive edits, replay,
stale/repeated resolution, conflicting history and wrong readback. This closes
ordinary open-limit edit recovery; it does not establish stop-market edit
semantics, activated-stop child mapping, margin sufficiency or real demo results.
