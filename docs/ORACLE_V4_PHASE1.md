# Oracle v4 Phase 1 — boundary collection and intraday evidence

Implementation branch; activation and the 48-hour acceptance window are still
pending. Phase 2 ledger/forecast changes and the ChatGPT :05 job are not enabled.

## Cycle identity and publication

Worker cron `59,14,29,44 * * * *` dispatches the following boundary, with the
existing `*/5` verifier. Boundary is UTC :00/:15/:30/:45, full only at :00.
The workflow installs dependencies before waiting until boundary + 8 seconds.
Wait is capped at 90 seconds; late starts still run and record measured lag.
Late means >360 seconds for full or >240 seconds for light. The independent
GitHub fallback is `3,18,33,48 * * * *`. Dispatch carries both boundary and kind.

Duplicate committed quarters are never replaced. The due guard checks cycle
identity and source freshness; rapid dispatches for the same cycle coalesce.
Manual dispatch without a boundary and code pushes run the current full hour.
A manual refresh cannot overwrite an existing intraday quarter. Full snapshot
measurements may refresh while that original quarter remains preserved.

Light promotion can only change `data/intraday/latest.json` and daily JSONL.
The producer checks schema, candle bounds, byte-prefix append-only history and
focused tests before attaching its SHA-bound `test` check with “light validation”.
Full and Oracle validation retain the Oracle archive guard and additionally
check intraday/funding archives. No direct main push or bypass merge is used.

## Twelve-call budget

Four spot OHLC requests (DOT 1m/5m/15m, BTC 15m), spot depth and trades, futures
ticker/book/history, futures trade/mark 1m charts, Coinbase level-one book.
Spot quote and spread come from the depth snapshot, avoiding the two redundant
Ticker/Spread calls in the original 14-call list. Coinbase light quotes flag
product status as unchecked; the full collector retains product validation.
Calls run concurrently with separate connections, no retries or redirects.

Tape requests are bounded to one page each. The spot cursor is the preceding
quarter boundary in nanoseconds, not Kraken's post-boundary page cursor. Perp
history scans its latest page to the previous coverage boundary. A page that
does not establish complete quarter coverage is explicitly partial; no fabricated
CVD bridge is emitted across missing/incomplete quarters. This budget does not
guarantee complete tape during busy periods. A larger tape budget is a separate
tradeoff. Large trades use an explicit USD 10,000 threshold.

Each row is at most 25 KB and retains the 15 closed trade/mark candles; source
failures and partial tapes are visible. Missing required perp quote/candles abort
before archiving so a failed attempt cannot poison the immutable quarter.
No gzip file, feature history, observations, analogs or scorecard is touched by
light collection. Ledger mark-to-market is explicitly unavailable until Phase 2.

## Full-hour additions and storage

Full collection adds its quarter, then includes four quarter summaries in the
snapshot and consumer bundle; absent quarters stay explicit. Perp trade/mark
1m/5m/15m/1h closed candles enter the existing cache under `DOTPERP.<kind>.<minutes>`.
Per-series retention is 120 candles; the immutable quarter archive holds 1m evidence.
Candle fields missing from Charts (VWAP/count) remain null. Collection filters at
source reception time and requested boundary and reports pagination/coverage gaps.

Funding history is fetched in full but persisted by appending only previously
unseen rows to monthly `data/funding/YYYY/MM.jsonl`. Historical corrections or
backfills require explicit reconciliation; old evidence is not rewritten. This
replaces the plan's proposed recurring funding gzip rewrite. Six historical gaps
remain visible, and funding accounting is disabled pending the ledger contract.

## Validation and activation gates

Local offline tests cover quarter rollover, late starts, wait limit, duplicate
publication, append-only guards, failure recovery, chart closure, funding gaps
and corrections, path allowlists and full/light scheduler dispatch.
`Tests` workflow manual `live_collection=true` exercises both light and full
collection in isolated directories and uploads their evidence, never promotes it.

After review/merge: deploy the Worker from the same main revision, confirm
`/health` and actual dispatch inputs, then measure 48 hours of boundary lag and
publication duration. Acceptance remains >=95% lag <=30 seconds, light end-to-end
publication <60 seconds and Oracle-writer queue <=2 minutes. Local capture speed
alone proves none of these. Check the Oracle job's boundary-aware wait before
activating; the :05 prompt/job migration is a Phase 2 change.

## Local smoke evidence (2026-09-20)

An isolated live quarter made 12 requests in 0.304 seconds and produced 6,386
bytes before request-provenance fields were added. Its latest perp-history page
was insufficient, correctly reported as partial. A full isolated collection
completed in 22.425 seconds; all eight closed-candle series loaded (60/12/4/1),
and 8,839 funding rows were bootstrapped. No working-tree data was modified.

Replaced-file bytes in that full smoke were 6,046,917 versus 6,020,754 for the
previous production files (+26,163 bytes, approximately 0.43%); funding bootstrap
added 990,971 bytes once. This is not a same-input v3/v4 benchmark and does not
establish the strict “no more replaced bytes” target. Storage acceptance remains
open; track it during rollout before claiming Phase 1 complete.


### Lossless storage correction

The hourly `history.json` now uses `hourly-columnar-v1`: repeated field paths are
stored once, followed by observation value rows and explicit missing-field
indices. Null, missing fields, nested objects/lists, numeric precision and all
720 retained hourly observations remain distinguishable. `HistoryStore` reads
both the legacy array and the new envelope; migration occurs only on the next
successful hourly update. Snapshots, forecast hashes and feature inputs retain
the same decoded values. Raw-history consumers must call `decode_history`.

`python scripts/benchmark_history_storage.py <history.json>` compares both
encodings using exactly the same observations and asserts canonical decoded
identity. On the retained full-smoke history (193 observations), legacy storage
was 2,245,110 bytes and columnar storage 1,618,400 bytes, saving 626,710 bytes.
Applying only this lossless serialization change to that full-smoke output
reduces its replaced-file total from 6,046,917 to 5,420,207 bytes, below the
previous production total of 6,020,754. This closes the measured storage overrun;
the total comparison still involves different collection times, while the
encoding comparison uses identical observations. Growth and timing must still
be measured after deployment. No existing repository data was rewritten by this
implementation change.
