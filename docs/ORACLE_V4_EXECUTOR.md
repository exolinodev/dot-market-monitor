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
