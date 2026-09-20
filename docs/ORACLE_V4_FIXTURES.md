# Oracle v4 fixture evidence — 2026-09-20

Writer PR #221 merged as `c859195`; capture PR #222 merged as `f02200f`.
All eleven rejected drafts (#35, #52, #78, #89, #94, #96, #101, #136,
#192, #203, #214) are closed. Parameters remain proposed, not approved.

## Runner evidence

- Main capture: https://github.com/exolinodev/dot-market-monitor/actions/runs/35535928049
- Documented analytics follow-up: https://github.com/exolinodev/dot-market-monitor/actions/runs/35536030863
- `tests/fixtures/v4/manifest.json` identifies the second run and includes request
  final URLs, parameters, HTTP status, raw-body length and SHA-256 for all 17 probes.
  This frozen manifest lost original paths when redirected (the guessed analytics
  routes became `/trade`). It is not retroactively edited. Future captures store
  `requested_url` (including query parameters) and `final_url` separately.
  The response bodies are stored byte-for-byte, without JSON reserialization.
  HTTP transfer/content decompression is handled by requests.
- Both artifacts are also downloaded to the parent workspace's
  `oracle-v4-fixtures/<run_id>/` directories. GitHub artifacts expire after 14 days.

| Source | Result | Interpretation |
|---|---|---|
| Kraken funding v4 | HTTP 200, success, 8,838 rates | Reachable; hourly series with gaps |
| Kraken trade/mark charts, 1m/5m/15m/1h | HTTP 200, non-empty candles | Reachable; current open candle included |
| Original `/api/analytics/v1/…` guesses | HTTP 200, HTML application shell | Invalid API routes; never treat as data |
| Documented charts analytics OI | HTTP 200, 60 timestamps and four-value rows | Candidate; field units/row semantics still require parser contract |
| Documented liquidation-volume | HTTP 200, 60 zero-valued buckets | Reachable; no nonzero liquidation example yet |
| OKX funding / current OI | HTTP 200, code `0`, non-empty data | Reachable in both runs; not yet a stability study |
| Bybit funding / OI | HTTP 403, explicit country block | Unavailable from this runner; exclude |

Documented analytics route:
`https://futures.kraken.com/api/charts/v1/analytics/PF_DOTUSD/{analytics_type}`,
with `since` and `to` in epoch seconds and numeric `interval=60`.
The response has 60 one-minute buckets, 19:36–20:35 UTC: this is consistent
with an interval in seconds, not minutes. A single request cannot exclude the
parameter being ignored in favour of a 1m default; verify another interval
before treating the unit as a confirmed API contract.
OI four-value rows look like per-bucket open/high/low/close: the first row is
constant and later rows vary. Their approximately 3.73 million magnitude is
consistent with the separately observed ticker OI of approximately 3.72 million.
This is a candidate interpretation, not proof of row order or contract units.
Types: `open-interest`, `liquidation-volume`.
Source: https://docs.kraken.com/api-reference/analytics/market-analytics

## Contract findings for Phase 1/2

Funding timestamps are ascending ISO UTC. The captured range is
2025-09-17 08:00 through 2026-09-20 20:00 UTC: 8,831 adjacent one-hour intervals,
five two-hour intervals, one three-hour interval. Do not silently interpolate
missing funding or book it as zero. Payload supplies both `fundingRate` and
`relativeFundingRate`; they are numerically different. Do not multiply notional
by the absolute field merely because its name contains “rate”. This public
response alone does not prove payer/sign convention, accrual rules or the
reference mark used for conversion. Confirm those before funding accounting.
For the last 24 records, `fundingRate / relativeFundingRate` ranges from
1.07578 to 1.16636 USD, consistent with a DOT reference mark. The implied
relationship is `absolute_rate = relative_rate × reference_mark`. For a
1-DOT contract, the candidate units are USD per contract and a dimensionless
fraction of notional, respectively. Equivalent cash-flow bases, before applying the payer convention, are
`qty_contracts × fundingRate` and `notional_at_reference_mark × relativeFundingRate`,
where `notional_at_reference_mark = qty_contracts × 1 DOT × reference_mark`.
Use the funding interval's reference mark, not an arbitrary later mark. The
fixture test pins the observed ratio range and this dimensional equivalence;
it does not independently prove the reference mark or who pays whom. The
future ledger test must additionally establish those conventions.

Phase 2 rule: a missing expected funding interval means funding is unknown,
never zero. Mark affected trades `funding_incomplete`, count them separately
in `performance.json`, and exclude them from complete-net-return metrics until
backfilled. Do not present provisional PnL as fully costed. The endpoint returned
a full approximately 1 MB history in both captures; hourly ingestion should
persist only new timestamped rows, detect gaps, and handle corrections explicitly
without rewriting the frozen source evidence.

Reference: https://docs.kraken.com/api-reference/historical-funding-rates/historical-funding-rates
(current documentation shows a v3 hyphenated route; the captured v4 route
is independently evidenced by its actual success response).

Chart timestamps are epoch milliseconds; analytics timestamps are epoch seconds.
Chart prices/volumes are numeric strings with inconsistent formatting (for
example `679.0` and `3759.00000000`); parse numerically, preferably with Decimal. Trade and mark candle counts in the
follow-up are 60 / 12 / 4 / 1. They include a current, unclosed candle; consumers
must enforce `open_time + duration <= reception_time`. Mark candle volume is
zero in the capture and is not a traded-volume signal.

The request `from=19:35:32 UTC` starts its 1m response at 19:36:00: the bucket
containing `from` is omitted. Round `from` down to the resolution boundary or
request one earlier bucket, then deduplicate and select closed candles locally.
`more_candles` is the pagination signal (false in all eight captured series);
when true, continue with overlapping boundaries and verify no gaps. A false
value does not mean the current candle is closed.

No parser, accounting, collector schedule or order execution is activated by
these fixtures. Reachability is established; sign/unit acceptance and sustained
source reliability remain implementation gates. Raw funding is approximately
1 MB and is frozen once, not refreshed on every scheduled collector run.
