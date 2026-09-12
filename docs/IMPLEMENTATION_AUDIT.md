# Extension audit

Baseline inspected: `d35dcc157dda8c3e4350fb98152b31db5a753f61` plus the first bot snapshot `5cf6903`. Existing public Kraken/CoinGecko access, Wilder-RSI/ATR, EMA/MACD/DEMA, daily VWAP, basic depth/tape, hourly workflow and regression tests were reviewed before extension.

Retained and extended: the corrected Wilder seed, EMA/MACD/DEMA conventions, Kraken source OHLC/ticker parsing, session VWAP anchoring, public-only collection, configured hourly :55 workflow, core numerical regressions and original input price anchors.

Changed defects/contracts:

- Replace unconditional `iloc[:-1]` assumptions with source-time-aware open/closed classification, including resampled bars and a run crossing a boundary.
- Replace abort-on-missing-core-data v1 behavior with explicitly null v2 source/component failure envelopes, as requested.
- Remove coarse textual peer-strength categories. Expose measured returns, medians, breadth, correlations and beta.
- Replace limited/stale-window tape summaries with bounded pagination, stable-ID deduplication, source-side aggregation, coverage and window-reset CVD.
- Replace guessed/alternative Futures field mappings with fields observed in the real public response and the official OpenAPI schema. No uninspected mark-candle fallback parser remains.
- Replace immature 24h/7d nearest-point assumptions with exact target-age tolerance and a bounded hourly v2 state.

Added missing components: 3m/2h/12h/2d/4d resampling; DOTBTC/BTC/ETHBTC coverage; the remaining requested indicators, slopes and crosses; causal fractal/ATR pivots, conditional geometry and divergence; complete depth/notional/slippage measurements; absorption and wall persistence; derivatives history; breadth/dominance/TOTAL3; realized volatility and dependence; compact self-described consumer tables; raw provenance, schema validation and bounded transport failure handling.

Source evidence: `tests/fixtures/manifest.json` identifies exact capture URLs/times. Spot fixtures contain ticker, OHLC, trades, depth and spread. Futures fixtures contain actual ticker, orderbook, trades and the exact PF_DOTUSD instrument object. CoinGecko fixtures prove the global timestamp and 1h/24h/7d return fields. These are public market records, with no customer/account data. Official schemas inspected: https://docs.kraken.com/openapi/spot-rest.yaml and https://docs.kraken.com/openapi/futures-rest.yaml.

Validation inventory: scalar/reference calculations for each indicator family; all derived intervals; candle boundaries and no lookahead; source parsing/optional fields; stale/future/missing clocks; timeout/rate-limit/circuit behavior; tape coverage/CVD; book and execution estimates; Fibs/pivots/divergence; retention and history deltas; known correlation/beta; total and isolated API outages; hard schema and timeframe inventory; live smoke and GitHub Actions validation. Live prices are not hardcoded as expected test results.

Intentional limits remain explicit: initial Kraken history/warmup, nullable immature 7d state, best-effort schedules, partial capped trade windows, displayed-only depth, and lack of individual-order identity. None is replaced with an invented value or wave count.
