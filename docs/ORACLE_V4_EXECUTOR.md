# Oracle v4 executor — implementation status

`src/kraken_execution.py` supplies the first offline-tested transport layer for
WP7. It is **not an operational executor**. There is no credentials lookup,
workflow, CLI activation, private API fixture capture or exchange order in this
change. `scripts/execute_orders.py`, bracket/management orchestration, paginated
fill reconciliation, rollout gates and live support remain required.

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
host. Only executions have a capture/parser in this change.

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
