# Recovery after missed quarters

The 2026-09-22 outage had two interacting causes: GitHub retained run
35652866974 as `queued` even though its cancellation endpoint reported it as
completed, and the paper ledger correctly rejected new quarters after missing
intermediate minutes. Collecting only the newest quarter could not repair the gap.

The scheduler now reports and disregards waiting runs (`queued`, `waiting`,
`pending`, `requested`) with no creation/update activity for more than 30 minutes.
An `in_progress` run always blocks another dispatch. Freshness checks, the
two-attempt-per-cycle budget and GitHub's collector concurrency group remain in
effect. `stale_waiting_run_ids` makes ignored records visible in scheduler status.

Before collecting a current quarter, an active paper ledger identifies absent
quarters after its last applied minute. It retrieves only historical PF_DOTUSD
trade and mark candles, with source URLs, request parameters and actual reception
timestamps. Each recovered quarter must contain every one of its 15 closed
minutes, with aligned trade and mark coverage and no pagination remainder.

Recovery is bounded to 48 hours, with four concurrent quarter fetches and two
public requests per quarter. All missing quarters must validate before any are
appended. Existing rows and ledger history are never replaced. Larger gaps,
unavailable historical candles or corrupt existing evidence still stop collection
for explicit investigation. The normal ledger replay and publication guards
continue to verify all inputs before publication.

Recovered rows are `partial`, `fresh: false` and marked
`recovery: historical_candles_only`. Historical books, ticker snapshots and trade
tapes are unavailable, not reconstructed from current quotes. CVD restarts after
the gap. The live quarter is collected after recovery, so its current quote is
not aged by the backfill. The paper engine retains its existing cost and funding
conventions; recovered candles are not evidence of historical execution prices.
