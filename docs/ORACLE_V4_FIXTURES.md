# Oracle v4 fixture evidence — 2026-09-20

Writer PR #221 merged as `c859195`; capture PR #222 merged as `f02200f`.
All eleven rejected drafts (#35, #52, #78, #89, #94, #96, #101, #136,
#192, #203, #214) are closed. Parameters remain proposed, not approved.

## Runner evidence

- Main capture: https://github.com/exolinodev/dot-market-monitor/actions/runs/35535928049
- Documented analytics follow-up: https://github.com/exolinodev/dot-market-monitor/actions/runs/35536030863
- `tests/fixtures/v4/manifest.json` identifies the second run and includes request
  URLs, parameters, HTTP status, raw-body length and SHA-256 for all 17 probes.
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
Reference: https://docs.kraken.com/api-reference/historical-funding-rates/historical-funding-rates
(current documentation shows a v3 hyphenated route; the captured v4 route
is independently evidenced by its actual success response).

Chart timestamps are epoch milliseconds; analytics timestamps are epoch seconds.
Chart prices/volumes are numeric strings. Trade and mark candle counts in the
follow-up are 60 / 12 / 4 / 1. They include a current, unclosed candle; consumers
must enforce `open_time + duration <= reception_time`. Mark candle volume is
zero in the capture and is not a traded-volume signal.

No parser, accounting, collector schedule or order execution is activated by
these fixtures. Reachability is established; sign/unit acceptance and sustained
source reliability remain implementation gates. Raw funding is approximately
1 MB and is frozen once, not refreshed on every scheduled collector run.
