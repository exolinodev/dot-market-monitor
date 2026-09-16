# Deterministic formulas, version 2.0.0

All prices and volumes originate from the sources in `sources` and the raw HTTP archive. Calculations use pandas 2.3.2 / NumPy 2.1.3, float64, ascending UTC timestamps, and the available native candle history. No interpolation, search-engine prices, model calls, wave-count selection, count probabilities, or trade recommendations occur in Python. JSON exports use `null` for undefined/nonfinite results. The compact view rounds numerical values to **12 significant digits**; `latest.json` keeps full float precision.

## Candles, UTC and causality

Kraken-native intervals: 1, 5, 15, 30, 60, 240, 1440, 10080 minutes. Native weekly boundaries are preserved exactly as returned by Kraken, in UTC. Derived intervals: 3m from 1m; 2h from 1h; 12h from 4h; 2d and 4d from 1d. Derived buckets are left-labelled, left-closed and aligned to **1970-01-01 00:00:00 UTC**, including multiday buckets. The first incomplete bucket and buckets missing any elapsed component are excluded. No missing candles are fabricated.

Aggregation: first open, max high, min low, last close, sum volume and trade count. Candle VWAP = sum(component VWAP × volume) / sum(volume). Zero volume gives null VWAP.

At the **source reception time** T, a bar starting at t with interval Δ is closed iff t + Δ ≤ T. `live` includes the current bar t ≤ T < t + Δ; `last_closed` stops at the latest completed bar. If the API did not return a current bar, `live` is null. Both indicator modes are computed independently. A bar fetched while open is never promoted to a confirmed close because another slow request finished later. Pivots in both modes use only closed bars. `calculation_at_utc`, `asof_utc`, `source_rows` and `gap_count` expose the calculation boundary.

The cache merges exact timestamps, with new source rows replacing old versions. At most 4096 native bars per instrument/interval are retained. Kraken initially supplies at most about 720 bars. Therefore a fresh 4d series initially has about 180 bars and **cannot have a valid EMA200 or SMA200**. These remain null until actual history is sufficient. The cache also allows a complete 1m daily VWAP after collection crosses the first UTC midnight. No unsupported backfill is claimed.

## Moving averages and momentum

- **SMA(n):** arithmetic mean of n bars; null until n values exist.
- **EMA(n):** α = 2/(n+1); recursive Eₜ = αxₜ + (1−α)Eₜ₋₁, seeded by the first available observation, hidden until n observations. EMA periods 9/20/21/50/100/200. The seed and retained history can differ from TradingView's longer exchange history.
- **Wilder RMA(n):** seed with the arithmetic mean of the first n valid observations; thereafter Rₜ = (xₜ + (n−1)Rₜ₋₁)/n. Leading missing observations are ignored. No missing OHLC rows are filled.
- **RSI14:** Δ = close.diff(); gain = max(Δ,0), loss = max(−Δ,0); Wilder RMA14 of each; RSI = 100−100/(1+gainRMA/lossRMA). Zero loss with positive gain → 100; both zero → 50. First valid RSI needs 15 closes.
- **Stoch RSI 14/3/3:** RSI14; raw = 100×(RSI−min(RSI,14))/(max(RSI,14)−min(RSI,14)); K = SMA3(raw), D = SMA3(K). Flat RSI range → null. This is the 0–100 convention.
- **MACD 12/26/9:** line = EMA12(close)−EMA26(close); signal = EMA9(line); histogram = line−signal. Signal starts with the first valid MACD line.
- **DEMA20:** 2×EMA20(close)−EMA20(EMA20(close)); the second EMA starts at the first valid first EMA.

## Range, trend strength, flow and volume indicators

- **TR:** max(high−low, abs(high−previous close), abs(low−previous close)); first TR = high−low.
- **ATR14:** Wilder RMA14(TR). **ATR%:** 100×ATR14/close.
- **Bollinger 20/2:** middle = SMA20(close); standard deviation uses population `ddof=0`; lower/upper = middle ± 2σ. Bandwidth% = 100×(upper−lower)/middle.
- **DMI / ADX14:** up = high−previous high, down = previous low−low. +DM = up if up > down and up > 0, otherwise 0; −DM analogously. The first DM and its TR are undefined. +DI = 100×RMA14(+DM)/RMA14(TR), −DI analogously. DX = 100×abs(+DI−−DI)/(+DI+−DI); zero DI sum gives 0. ADX = RMA14(DX), first valid at bar index 27. A flat zero-TR market gives null DI/ADX.
- **OBV:** cumulative sum(sign(close change)×volume), seeded at zero. Equal close adds zero. This is relative to `obv_origin_utc`, not an exchange-wide all-time OBV; a rolling cache changes the origin.
- **MFI14:** typical = (H+L+C)/3; raw money flow = typical×volume. Positive/negative flows depend on the sign of the typical-price change. MFI = 100−100/(1+sum14(positive)/sum14(negative)); no negative flow → 100, both zero → 50. Initial change is undefined.
- **CMF20:** multiplier = (2C−H−L)/(H−L), or 0 for zero range; CMF = sum20(multiplier×volume)/sum20(volume). No volume → null.
- **Volume:** SMA20, ratio = current volume/SMA20; Z = (volume−SMA20)/population std20. Zero denominator → null. Live volume is partial-bar volume.
- **Session VWAP:** resets at **00:00 UTC**, using exchange candle VWAP×volume, divided by cumulative volume within that UTC day. Missing midnight or a missing elapsed bar invalidates that session value. A typical-price approximation is used only by generic indicator tests when no candle VWAP column is supplied; production Kraken bars always carry their source VWAP.

Every indicator has a **one-bar slope**, current value minus previous value in the same mode. Null means warmup/undefined. Positive/negative/zero slope supplies direction without a textual market assessment.

Crosses require previous difference ≤0 and current >0 (up), or previous ≥0 and current <0 (down). No event is manufactured across missing indicator values. Output is the latest observed event and its integer bar distance (0 = this bar). Relevant pairs: price vs EMA9/20/21/50/100/200 and SMA50/200/VWAP/Bollinger edges; EMA9/21, 20/50, 50/200; MACD/signal and zero; Stoch K/D and 20/80; RSI 30/50/70; ADX25; +DI/−DI; MFI20/80; CMF0. `null` cross means no cross in the available valid history. Exact event timestamps are in `latest.json`; compact event tables carry direction and bars-since.

## Pivots, divergences and Elliott helpers

**Fractals:** strict extremum above/below both 2 left and 2 right completed bars. Ties create no pivot. Confirmation occurs only at the close of the second right bar. Stored `confirmed_at` identifies that confirming bar's **opening timestamp**, so actual availability is confirmed_at + timeframe interval. A bar can independently qualify as both a high and low; no intrabar ordering is inferred.

**ATR ZigZag:** close-based extrema; reversal ≥ 2×ATR14 at the candidate extreme confirms it. The candidate's ATR is frozen when it forms. Close-based construction avoids guessing whether an intrabar high or low occurred first. The current unconfirmed terminal extreme is excluded. The confirming timestamp again identifies the confirming completed bar. Same-kind successive pivots classify strictly as HH/LH for highs and HL/LL for lows; equality is EH/EL. Full output retains 12 pivots per method; the compact file retains the last 8 per method.

**Divergence:** compare the last two same-kind confirmed fractal pivots, separated by 3–60 bars. Required absolute price change: max(0.1% of first pivot price, 0.1×ATR14 at second pivot). Required RSI difference: 2 points; required MACD-line difference: 0.02×ATR14 at second pivot. Bullish = lower price low and higher indicator low; bearish = higher price high and lower indicator high. Both inequalities must pass. Missing/warmup → false with status. Flagged events include pivot and confirmation times, prices, indicator values, distances and thresholds. The detailed file also retains diagnostics for false flags.

**Fibs:** for directed anchors A → B and range R=B−A, retracement(r)=B−rR for r=0.236/0.382/0.5/0.618/0.786; extension(r)=A+rR for r=1/1.272/1.618/2/2.618. Decimal arithmetic is used before float export. Fixed anchors: A=0.7324, B=1.2848. Dynamic anchors: the latest two confirmed ATR-ZigZag pivots, separately per timeframe. Downward spans retain their direction.

**Level distances:** current price−level, and 100×(current price/level−1); positive = above level. DOT levels: 1.03/1.14/1.27/1.2848/1.31/1.42 USD; BTC: 80000/70000/60000 USD. A current price may be a fresh trade or a labelled fresh spread midpoint; the price type is explicit.

**wave_rule_flags:** the last five alternating ATR-ZigZag pivots are conditionally called p0..p4 only for geometry. Test whether closed price intervals [p0,p1] and [p3,p4] intersect, endpoints included. This is `hypothetical_1_4_price_overlap`. It neither labels those segments as actual waves nor accepts/rejects a count. Insufficient anchors → null. No Elliott A/B/C/D, wave-count probability, or preferred scenario is generated.

## Fibonacci time projections (DOT/USD)

**Fibonacci Time ≠ Fibonacci Price.** `markets.DOTUSD.time_fibs` contains timestamps and elapsed durations; prices are anchor provenance only. It makes no assertion about price direction, breakouts, reversals or whether a window will matter. Python assigns no Elliott count or degree. A/B/C are configured anchor IDs, not automatically identified Elliott waves.

`config/time_fibs.json` is the explicit anchor registry. `active_anchor_sets.DOTUSD` selects one named set from `anchor_sets`; additional sets can be stored without becoming active. `selection=explicit_same_degree` is required. Selection never reads the latest fractal/ATR-ZigZag extrema, live indicators, perp wicks or history deltas. The selected set remains fixed until deliberately edited; it does not change when old pivots roll out of the compact history. The file is a reviewed record of confirmed spot fractals, not an automated degree detector or a fresh exchange observation.

Every anchor must declare `market_type=spot`, `pivot_method=fractal`, `candle_state=closed` and boolean `confirmed=true`, a finite positive price, high/low type, and timezone-aware pivot and confirmation times. A < B < C is required. `confirmed_at_utc` is the **actual confirming candle close/availability time**, not the opening timestamp used by the existing fractal `confirmed_at` field. All confirmations must be at or before the snapshot reference. Missing configuration/selection/anchors or a set not yet confirmed as of the reference yields `status=unavailable`; invalid configuration yields `status=error`. Both include a reason, empty anchors/projections/clusters and null durations. There is no fallback or invented data; other market components remain usable.

The initial set `count_b_same_degree_2026_09` uses the confirmed DOT/USD **4h spot fractals** below. The confirmation instants are the observed confirming-bar opening timestamps plus four hours (see the fractal convention above).

| Anchor | Type / configured role | Price USD | Pivot UTC | Confirmed/available UTC |
|---|---|---:|---|---|
| A | high | 1.2822 | 2026-09-08 20:00 | 2026-09-09 08:00 |
| B | high / corrective_high | 1.1642 | 2026-09-11 04:00 | 2026-09-11 16:00 |
| C | low | 0.9959 | 2026-09-13 08:00 | 2026-09-13 20:00 |

**Formula:** `projected_time = anchor_time + duration * fibonacci_ratio`. Here `anchor_time=C.time_utc`, A→B=56h, B→C=52h and A→C=108h. A→B and B→C each project ratios 0.618, 1.000, 1.272 and 1.618. Configuring 2.000 and 2.618 is also supported; unsupported/duplicate ratios are errors. Separately, `symmetry_projections` always includes A→C×0.500 from C, i.e. 2026-09-15T14:00:00Z.

All timestamp arithmetic uses timezone-aware **UTC**, including when inputs have another explicit offset. Naive times are rejected; the host timezone and clock are never used by this module. Durations are integer microseconds and ratios use Decimal multiplication. Sub-microsecond results round to the nearest microsecond (ties to even); no hour/minute rounding occurs. ISO timestamps end in Z and retain fractional seconds without unnecessary trailing zeros. Minutes follow the existing compact snapshot's 12-significant-digit numeric export convention.

| Source | Ratio | Projected UTC |
|---|---:|---|
| B→C | 0.618 | 2026-09-14T16:08:09.6Z |
| A→B | 0.618 | 2026-09-14T18:36:28.8Z |
| B→C | 1.000 | 2026-09-15T12:00:00Z |
| A→C symmetry | 0.500 | 2026-09-15T14:00:00Z |
| A→B | 1.000 | 2026-09-15T16:00:00Z |
| B→C | 1.272 | 2026-09-16T02:08:38.4Z |
| A→B | 1.272 | 2026-09-16T07:13:55.2Z |
| B→C | 1.618 | 2026-09-16T20:08:09.6Z |
| A→B | 1.618 | 2026-09-17T02:36:28.8Z |

**Clusters (±2-hour confluence rule):** use a maximum total event span of four hours, inclusive. Combine normal and symmetry projections, deduplicate duration/ratio identities, sort chronologically (ties by projection ID), and greedily take the longest prefix within four hours of the earliest remaining event. If the group qualifies, consume it and repeat, producing disjoint, reproducible clusters; otherwise discard only its earliest event and retry so a later independent pair is not lost. Neighbouring events cannot chain into a span greater than four hours. Emit a cluster only if it contains at least two distinct source durations; multiple ratios of one duration alone are not independent. Distinct source durations count separately even when their projected times coincide. This is arithmetic confluence, not statistical independence.

`window_start_utc`/`window_end_utc` are the actual earliest/latest events, with no added padding. `center_utc` is the median event time (even count: mean of the two middle times, to microsecond precision). The four-hour membership rule means a common ±2h midpoint exists; the exported **median** can differ from that midpoint for asymmetric groups. `events` retains each complete source projection and its own state; `event_count` counts them. Cluster IDs depend on the set ID and member projection IDs, not on snapshot time or whether another cluster expires.

The initial two clusters are:

- 2026-09-14T16:08:09.6Z–18:36:28.8Z, median 17:22:19.2Z: B→C×0.618 + A→B×0.618. Europe/Madrid display: 18:08:09.6–20:36:28.8.
- 2026-09-15T12:00:00Z–16:00:00Z, median 14:00:00Z: B→C×1.000 + A→C×0.500 + A→B×1.000, **three events**. Europe/Madrid display: 14:00–18:00, center 16:00. Display conversion never enters calculations.

**Snapshot-relative state:** the sole reference is `meta.generated_at_utc` (the same `generated_at_utc` value in the detailed generator output), repeated as `reference_at_utc`. A cluster is upcoming before its start, active from start through end inclusive, and expired after its end. A single projection is a point: start=center=end=projected time; it is active only at that exact instant, with no implicit tolerance window. `minutes_to_center` is nonnegative until/at center, otherwise null; `minutes_to_start` is nonnegative until/at start, otherwise null; `minutes_since_end` is positive only after end, otherwise null. An active cluster after its median can therefore have all three minute fields null. Expired events/clusters stay in the output. These states express only timing; subsequent interpretation belongs to the analysis layer.

`python src/main.py --from-latest` regenerates `latest.json` and `llm_snapshot.json` from the existing detailed measurements and configured anchors without network collection, timestamp advancement, or history/cache changes. Use it for a reproducible local regeneration; `python src/main.py` continues the normal collector path. The new field is optional in schema v2 so older snapshots remain valid. Existing price Fibonacci, pivot, indicator and history formulas are unchanged.

## Descriptive observation layer

`markets.DOTUSD.observations` is an additive version-1 contract inside snapshot v2. `contract=measurements_only` means that Python exports observations and explicit mathematical transformations. It does **not** add forecast models, directional scores, regime labels, probabilities, target prices, Elliott scenarios or forecast evaluation. ChatGPT performs the hourly interpretation using `CHATGPT_MONITOR_PROMPT.md`. Existing indicator/price-Fibonacci/time-Fibonacci/history calculations remain unchanged.

The reference is the snapshot's `generated_at_utc`, repeated as `reference_at_utc`. Each component has `status`, `reason`, `source_ids`, `asof_utc`, `coverage` and `data`. `ok`/`partial`/`unavailable`/`error` describe data availability or validity. Null data is not zero. Component failures do not replace other market data. The strict payload schema rejects undeclared fields such as a directional score; observation timestamps may not exceed the snapshot reference. The observation schema is resolved locally, without schema-validation network requests.

Parameters live in `config/observations.json` and are checked against `schema/observations.config.schema.json`. The SHA-256 in each block covers the canonical JSON of all three input configurations: observations, time-Fibonacci anchors and scheduled events. The separate archive retains the actual configurations referenced by its observations. Formula changes require a deliberate contract/version review; parameter changes are visible through the fingerprint.

### Closed-candle availability

New candle measurements use only bars closed at the **source reception cutoff**, and also closed at the snapshot reference. Their most recent bar must end at the latest elapsed UTC timeframe boundary; collection crossing a boundary cannot promote a bar that was open when received. Source failures, missing latest closes, gaps and insufficient windows return unavailable. UTC calculations do not depend on the host timezone. Earlier bars remain historical measurements, not claimed observations of a later close.

### Price-level observations

`price_levels` uses the configured USD levels (initially the same 1.03/1.14/1.27/1.2848/1.31/1.42 levels already present), separately for closed 1h and 4h candles. Default observation window: the last 24 bars, plus the preceding close to detect a crossing. No importance/ranking is assigned to levels.

- Distance USD = last closed close − level; distance % = 100×(close/level−1); distance ATR = (close−level)/current Wilder ATR14. Undefined/zero ATR produces null normalized distance.
- Count closes strictly above, strictly below and exactly equal to the level. The final side is an arithmetic comparison, not a forecast. Count consecutive final-side closes within the window; `consecutive_count_capped=true` marks reaching the configured window limit.
- An upward close crossing requires previous close ≤ level and current close > level; a downward crossing requires previous close ≥ level and current close < level. Report only the latest crossing **inside the observed window**, its closing UTC instant and bars since. No observed crossing means null, without assuming an earlier one.
- For an upward crossing, `max_close_move_against_cross_usd` = max(running maximum of subsequent closes − each close), including the crossing close. For a downward crossing use max(each close − running minimum). Normalize by ATR **at the crossing**, frozen at that time. This uses closing-price paths; intrabar order is not inferred.

These are retrospective facts relative to the currently configured levels, not claims that those levels had already been selected for a historical decision. Terms such as acceptance, rejection, support, resistance and confirmed breakout remain interpretation outside this module.

### Anchored VWAP

`anchored_vwap` uses each anchor from the explicitly selected, confirmed spot time-Fibonacci set and native 1h candles. The start is the **opening timestamp of the pivot candle**, not the unknown exact execution time of its high/low. The full anchor candle participates once closed. All elapsed bars from that opening through the last closed bar must be available without gaps.

AVWAP = sum(exchange candle VWAP × candle volume) / sum(candle volume). No typical-price approximation is used. Positive-volume candles require valid positive exchange VWAPs. A verified zero-volume bar contributes zero notional and zero quantity, even if its VWAP is absent; a zero total volume yields null. Export AVWAP, cumulative DOT volume, change from the preceding bar's anchored average, last closed price and its USD/% distance. A missing previous average gives null slope. Anchor IDs, set ID, actual confirmation time, sampling interval and coverage remain visible. No automatic re-anchoring occurs.

### Historical context

`historical_context` uses closed hourly candles. It supplies current ATR14%, realized volatility over 24 hourly returns, movement efficiency over 24 changes, and range width over 24 candles:

- Realized volatility % = 100×sqrt(sum(log(Cᵢ/Cᵢ₋₁)²)); not annualized.
- Movement efficiency = abs(Cₜ−Cₜ₋ₙ)/sum(abs(Cᵢ−Cᵢ₋₁)) over n changes. A zero path denominator is undefined/null.
- Range width % = 100×(max(high,n)−min(low,n))/Cₜ.

The percentile reference is the preceding 720 bar positions (30 days), **excluding the current observation**. Warmup/undefined values do not become observations. At least 168 valid samples are required for a rank. Percentile = 100×(number strictly below current + 0.5×number exactly equal)/sample count. Export actual baseline start/end, sample count, requested size and completeness. Tied populations rank at 50; insufficient baselines retain a valid current value but no rank. These are descriptive historical ranks, not event probabilities or regime classifications. As with existing indicators, Wilder seeding uses the retained source history.

### Market-relative measurements

`market_relative` measures DOT/BTC log returns over matching completed 1h/4h/24h periods. Beta = sample covariance(DOT hourly log returns, BTC hourly log returns) / sample variance(BTC hourly log returns), using the **168 hourly returns strictly preceding the measured interval**. The estimation interval ends where the measured return interval starts. Missing/misaligned candles, insufficient samples or zero BTC variance yield unavailable.

Beta-adjusted log return in percentage points = 100×(sum(DOT log returns in measured interval) − beta×sum(BTC log returns in measured interval)). The period's DOT and BTC log returns, beta, exact estimation/measurement boundaries and observation count are exported. This is a backward-looking decomposition of already observed returns, not a predicted DOT return or proof of a causal BTC effect.

### Execution windows and volume by price

`flow_windows` chooses a shared cutoff at the latest whole UTC minute covered by the available fresh spot/perp trade tapes, no later than the snapshot reference. A stale tape is unavailable without discarding the other venue. The two default 60-minute windows are `(T−60m,T]` and `(T−120m,T−60m]`; a boundary trade is counted once. Both source coverage boundaries must prove the entire requested interval. Missing/incomplete coverage gives null data, not extrapolated totals.

For each window export buy/sell DOT quantity and USD notional, signed DOT volume (buy−sell), trade count, first/last observed execution prices/times and their percent change. A proven empty window has zero flow/counts and null prices. Perpetual assignment/termination/block records are excluded, while fills and liquidations are executions. Unique trade IDs and exchange-provided taker sides are required. Unknown sides fail validation.

`current_minus_previous` subtracts the two complete, adjacent equal-length window totals for quantities/counts. Each signed-volume sum resets independently; this is **not** a change in a continuous CVD series or a comparison of differently reset/overlapping windows. First-to-last execution price change describes observed trade endpoints, not invented prices exactly at the interval boundaries. No absorption, positioning or participant-intent label is added.

`volume_profile` bins actual **spot executions** over a completely covered trailing 4h interval. Fixed initial bin width: USD 0.005; origin: USD 0. Decimal division/floor defines bin index, with `[lower,upper)` boundaries. Export observed bins in ascending price order, with total/buy/sell DOT quantity, USD notional and trade count. Bins conserve the included trades, quantities and notionals. Zero-activity bins are omitted; a proven empty interval produces an empty array. More than 200 observed bins or incomplete tape coverage yields unavailable, without silently changing resolution or treating partial trades as a complete profile. Candle volume is never redistributed into guessed price bins. This profile is not a long-term cost-basis estimate.

### Actual observation history and derivatives changes

`data/raw/observation_history.json.gz` separately retains the latest genuine observation per UTC hour for up to **365 days / 8760 entries** by default. It starts with the new collector and has no synthetic backfill from older fields lacking the required provenance. Comparisons use only observations from earlier UTC hours and reserve one retention slot for the current hour; a rerun within the same hour or at the retention limit therefore reproduces the same history view after saving. The existing `data/history.json` remains a separate 30-day contract.

Each archived measurement carries value/null, unit, source IDs, source timestamp and method. Values must have been fresh within 120 seconds at their observation time. Records include their configuration hash; retained configurations include the exact content and first recorded timestamp. Loading validates units, finite values, ordered unique hours and configuration hashes. A corrupt archive is reported and preserved; a collector cannot overwrite a later observation with an earlier snapshot. Offline regeneration never appends to the archive.

`spot_perp_history` compares genuine observations nearest T−1h/4h/24h/168h, within ±20 minutes. Source timestamp separation must independently fit that tolerance, and unit/method/source IDs must match. Export current/reference values, absolute change, both source times and actual elapsed minutes. A missing/null/mismatched reference is not replaced by another value.

Fields are spot quote midpoint USD (consistently midpoint, never silently switched to last trade), exchange perp mark USD, open interest DOT only with validated DOT/USD contract size 1, and the unchanged absolute API funding rate. Quote/mark basis = 10000×(mark/spot quote midpoint−1), only if the observations are at most 60 seconds apart. This new quote-based basis does not change the existing trade/quote-based `spot_perp_basis`. Funding differences are absolute API-rate differences, without invented percent or annualization conventions. Its 30-day historical rank uses only earlier valid archived observations and the same midrank/minimum-sample rule as above. Archived signed-volume windows are available for auditing; they are not subtracted across hourly snapshots with potentially overlapping resets.

### Additional venue and verified calendar

`cross_venue` adds the public Coinbase Exchange DOT-USD product and level-one book. The product must be online, enabled, non-auction and explicitly DOT/USD spot. Bid/ask, sizes and exchange book timestamp are checked. Kraken's corresponding book uses its explicitly labelled reception timestamp because that feed lacks an equivalent book-wide source instant. Only fresh quotes within the configured maximum 60-second skew produce the arithmetic `10000×(Coinbase midpoint/Kraken midpoint−1)` difference in bps. Quotes and spreads stay separate by venue; the module does not claim leadership, market-wide coverage, order identity or executable arbitrage. Coinbase raw responses are archived alongside existing public responses.

`scheduled_events` reads `config/scheduled_events.json`: an explicitly verified, limited registry, not an automatically refreshed complete economic calendar. Every event has a source URL and verification time. An event verified later than a replay reference is excluded. Current initial entries are the official Fed's September 15–16, October 27–28 and December 8–9 **2026 meeting dates**. No decision release time was established by that date-only source.

For `precision=date`, export inclusive local start/end dates and their IANA timezone; event UTC instant and minute countdown fields remain null. Upcoming/active/expired compares the reference's date in that explicit timezone; active means within the listed dates, not proof a session is presently in progress. For separately verified `precision=instant` records, normalize the source instant to UTC and calculate nonnegative minutes until, or positive minutes since, relative to the snapshot. Export verification age and scope; do not add surprise forecasts, impact rankings or directional conclusions. Captured source excerpts and response hashes live in `tests/fixtures/observation_sources_manifest.json`.

### Shared inputs and replay

`input_lineage` exposes shared candle/trade inputs, the time-Fibonacci duration dependency A→C=A→B+B→C, and the midpoint identity of the total-symmetry projection. `event_count=3` remains the arithmetic count of projections. No independence weight or confidence score is manufactured. The initial anchor selection's `selection_recorded_at_utc=2026-09-14T18:24:05Z` is the first committed configuration record (`f028161`), not an assertion about an earlier human decision time. Existing time-Fibonacci arithmetic is unchanged; this extra provenance lets the consumer distinguish retrospective configuration from information demonstrably recorded at the time.

`--from-latest` reconstructs source-bound closed frames from the saved cache and reception cutoffs, and deduplicates saved raw spot/perp trade pages using their existing coverage metadata. It requires matching raw/snapshot generation times; missing source inputs remain unavailable. Configuration is explicit, source data are not fetched, and snapshot reference/history/cache are not advanced. A full fresh collector run and its offline replay are checked for equal output. The entire compact snapshot must remain below the existing 500 KB limit.

## Orderbook and execution estimates

Spot requests 500 visible L2 levels per side; public futures orderbook returns its visible depth. Orders are aggregated per price, not individual identities. Quantity units for PF_DOTUSD are validated against the actual `flexible_futures`, DOT/USD instrument definition; `contractSize` must be 1.

Best bid=max(bids), ask=min(asks), mid=(bid+ask)/2. Spread absolute=ask−bid; bps=10000×spread/mid. Microprice=(ask×best_bid_size + bid×best_ask_size)/(best_bid_size+best_ask_size). Imbalance=(bid volume−ask volume)/(sum). Depth bands are symmetric ±5/10/25/50/100/200 bps around mid. Each reports quantities, price×quantity notionals, volume and notional imbalance, and whether the returned depth extends to both band edges. Top imbalance uses level 1; generic `imbalance` uses ±200bps.

Walls are the three largest visible price×quantity levels on each side within 0.5/1/2% of mid; deterministic ties use price. This is a ranking, not a claim about hidden liquidity.

Slippage walks asks ascending for buys and bids descending for sells. USD-sized scenarios spend/receive 1000/5000/10000 USD; base-sized scenarios buy/sell 5000 DOT. A final level is partially filled as required. VWAP=filled quote/filled base; cost bps=10000×(VWAP/mid−1) for buys and its negative for sells. If visible depth cannot fill the order, full-order VWAP and slippage are null, with filled amount, remainder, partial VWAP and `fill_complete=false`. Fees and book changes during execution are excluded.

## Tape, CVD and absorption

Kraken Spot Trades `b/s` and Futures history `buy/sell` are source-provided taker directions. No direction is inferred from candles or price changes. Public futures history documents the side as the **taker side**. Futures off-book `assignment`, `termination` and `block` events are excluded from aggression statistics and counted separately; `fill` and `liquidation` remain.

Spot: paginate forward with returned `last` cursor, starting at T−4h (nanosecond timestamp). Futures: paginate backward with documented `lastTime`, starting at current history. Futures dedup uses stable `uid`, since the observed `trade_id` repeats between pages; spot uses stable trade ID. Each path is capped at 30 pages. Partial pagination returns explicit coverage and partial measurements instead of silently claiming a full window.

Windows (T−window, T]: 1m/5m/15m/30m/1h/4h. Buy/sell quantities and notionals, directional and total counts, arithmetic average quantity/notional, delta=buy−sell, notional delta, and delta/total are measured. `window_complete` requires proven continuous pagination coverage spanning both boundaries. Incomplete quantities and deltas are **observed partial sums**; they must not be read as totals. The API response time and last execution time are distinct.

**CVD:** start at zero exactly at each window's left boundary; add +volume for buy-taker trades, −volume for sell-taker trades in chronological order. End CVD equals the window delta; min/max include the initial zero. This is window-reset CVD, not an unobserved lifetime cumulative series. Large trades: linear sample quantiles 95% and 99% of observed trade base sizes, sample count, count at/above each threshold, and last 12 qualifying executions. Coverage is explicit.

**Absorption candidate (5m):** needs complete 4h tape, at least two current executions and valid last-closed DOT 1m ATR14. Baseline is the preceding 47 disjoint five-minute deltas (population mean/std, minimum 24 required). Z=(current delta−baseline mean)/baseline std. Buy-side candidate iff delta<0, Z≤−2 and current last−first execution price ≥−0.25×ATR1m. Sell-side candidate iff delta>0, Z≥2 and price change ≤+0.25×ATR1m. Thus flat or opposite price movement qualifies. Zero baseline variance or incomplete data → false with unavailable status. Output is booleans and measurements only. The heuristic does not prove an absorbing participant exists.

## Derivatives, returns, breadth and volatility

Futures parsing is based on the captured real response and official schema. All observed fields remain in the raw archive; the ticker maps mark, last/time/size, index, bid/ask and sizes, 24h high/low/open/change/VWAP, base volume, quote volume, open interest, current/predicted absolute funding, suspended/postOnly, pair/tag/symbol. Missing optional fields are null with `field_status=unavailable`. Absolute API funding rates are **not labelled as percentages** or annualized. Basis = mark−fresh spot; percent=100×(mark/spot−1), bps=10000×(mark/spot−1).

Instrument 1h/4h/24h/7d returns use exact hourly **completed close-to-close** references: 100×(Cₜ/Cₜ₋ₖ−1). Timestamp and reference price are included. Missing exact reference → null. Returns intentionally do not compare a live tick to an unmatched historical hourly open. DOT/BTC is fetched directly; DOT vs BTC also reports DOT-return minus BTC-return in percentage points and ratio-return=(1+rDOT)/(1+rBTC)−1. Both source close timestamps must align. ETH/BTC uses its own hourly bars and fresh quoted/traded current price.

CoinGecko supplies actual `price_change_percentage_1h_in_currency`, `24h`, `7d` and `last_updated` per coin. Peers: ETH/BNB/XRP/SOL/DOGE/ADA/LINK/AVAX, DOT excluded from peer aggregates. Report arithmetic mean, median, positive fraction and score=100×(positive_count−negative_count)/available_count. Zero-return coins count in the denominator. Partial source sets expose sample count vs expected 8. DOT relative return is DOT minus median in pp. Outlier iff abs(return−median)>max(10pp,5×median absolute deviation). Outliers are listed; the median is computed over the declared peer set, and the mean is explicitly allowed to reflect extremes.

Global market cap and BTC/ETH cap percentages come from CoinGecko `/global`. BTC/ETH cap proxies=total cap×their reported percentage/100. TOTAL3 proxy=total cap−these two cap proxies. This is a CoinGecko-universe proxy, not TradingView TOTAL3. BTC/ETH dominance, global cap/volume and TOTAL3 history changes are derived from stored hourly observations.

**Realized volatility:** nonannualized 100×sqrt(sum(log(Cₜ/Cₜ₋₁)²)). 1h uses 60 completed 1m returns; 24h uses 24 completed hourly returns; 7d uses 168 completed hourly returns. Full regular sampling is required; missing/gapped history → null. ATR%, BB bandwidth and volume Z-score provide additional per-timeframe volatility/volume measures.

**Correlation and beta:** aligned completed hourly log returns, 24 or 168 observations, no missing observations or hourly gaps. Pearson correlation DOT/BTC and DOT/ETH. Beta=cov(DOT,peer)/var(peer), sample `ddof=1`; zero peer variance → null. DOT/ETH beta is included as an additional mathematical measure. No qualitative regime label is emitted.

## History, freshness, failure and wall persistence

`history.json` retains the latest observation per UTC hour, at most **30 days / 720 entries**. Values include current DOT/BTC/ETHBTC and BTC quotes, perp mark, dominance, cap/volume/TOTAL3, OI/funding/predicted funding/basis, breadth and selected indicator states. `since_previous_run` compares the latest earlier observation (including an earlier run in the same hour, before replacement). 1h/4h/24h/7d choose the nearest genuine observation to the target with **±20 minutes tolerance**. Missing reference or current value → null; no interpolation or v1-history mislabelling. Both absolute difference and relative percentage are supplied; absolute dominance differences are percentage points. A relative percentage of a signed rate is only arithmetic, not a funding yield. History accumulates naturally; no 7d claim is made on day one.

Each source exposes URL/ID, reception time, source timestamp when available, timestamp kind, age, TTL, fresh boolean, status and error. A response without a semantic market timestamp uses reception time explicitly labelled `received_at`; it does not certify that a ticker's last execution is recent. Freshness rejects timestamps >5 seconds in the future. Spot/current quote and live tape/perp/depth TTL=120s; CoinGecko=1800s; instrument definitions=86400s. OHLC freshness is measured from the last native candle start with TTL=interval+120s, while open/closed mode also checks the exact interval. Sources are rechecked at run completion. Stale price/value fields are null; archived raw responses remain inspectable. Consumer document TTL=5400s (90 minutes); `meta.fresh` is only freshness at generation, so consumers **must recompute age** when reading.

Every fetch and derived component is isolated. One API failure produces a null component and source error while other components remain usable; even a total network outage publishes a valid `partial` error envelope. Schema errors are programming errors and prevent snapshot publication. Transport: 12s request timeout, at most 3 attempts, exponential 1.5/3s backoff, Retry-After honoured up to a 15s wait cap, retries on HTTP 408/429/5xx, transport errors and API error payloads; other HTTP 4xx fail once. After three consecutive transient request failures per host, a 300s circuit breaker prevents a host outage consuming the entire workflow budget.

Wall persistence matches **side + exact price**, tracks first-seen time and successive snapshots, size/notional and distance to current mid. New/stable and closer-to-market are measurements. A level disappearing from the top-three rank is checked against the complete visible book. If outside visible range, removal is unknown. If absent within visible range, executions at the same price and correct taker side are checked only over the observed tape. `execution_observed_at_price=true` is consistent with a fill but does not prove it was this wall. `wall_removed_without_observed_trade` is null when tape coverage is incomplete, false if still visible, otherwise the absence of a matching observed execution. Order identity, cancellations, movements between hourly snapshots and spoofing intent cannot be proven by this sampler.

A separate `relocation_candidates` heuristic pairs an absent prior wall with exactly one newly appearing same-side wall when base size differs by at most 10% and price by at most 25bps. It reports both prices, size/price change, and whether the new distance to mid is smaller. `wall_moved_closer_candidate` is conditional on that matching rule; `identity_proven=false` always. Observed execution volume and its ratio to the previous wall size are included for removed walls without asserting that the wall itself was filled.

## Oracle v3 formulas

This additive layer is outside `observations.contract=measurements_only`.
Every feature names its source IDs, coverage, units, status and method version.
`config/oracle.json` is canonical-JSON SHA256 fingerprinted alongside the existing
measurement config. All formula inputs are available at the feature reference;
future prices enter only the evaluator's labels.

| Feature | Formula / guard | Units |
|---|---|---|
| OI change, 1h/4h/24h | `100*(current/reference-1)`, positive reference, valid contract units, source-time separation within existing tolerance | percent |
| Historical extremity | `100*(count(past<x)+0.5*count(past==x))/n`; matching versions/configs, 30-day baseline, current hour excluded, n>=168 | percentile, not probability |
| Flow signed / absolute | `buy-sell` / `buy+sell`, complete executed window only | DOT |
| Signed-flow fraction | `signed/absolute`, absolute >=1 DOT | [-1,1] |
| Tape return | `10000*(last_executed_price/first_executed_price-1)` in the same complete window | bps |
| Impact efficiency | `return_bps / signed_flow_fraction`, only `abs(fraction)>=0.05` | bps per signed fraction |
| Efficiency loss flag | comparable adjacent equal windows, same flow sign, `abs(signed_current)>=abs(signed_previous)`, previous impact >0 and current impact <=0.5*previous impact | boolean |
| Flow/price divergence | negative flow fraction <=-0.05 with return >=-5bps, or positive fraction >=0.05 with return <=5bps | observed category |
| CLV | `(close-low)/(high-low)`; zero range unavailable | [0,1] |
| Wicks / body | `(min(open,close)-low)/ATR`, `(high-max(open,close))/ATR`, `abs(close-open)/ATR`; positive ATR14 | ATR |
| EMA extension | `(closed_close-EMA20)/ATR14` | signed ATR |
| AVWAP / level / pivot distance | `(closed_close-reference_level)/ATR14`; AVWAP requires contemporaneously registered anchor | signed ATR |
| Failed breakdown/out | `low<level<close` / `high>level>close`; levels are configured/exported or pivots confirmed before candle start | boolean per USD level |
| Reclaim persistence | previous comparable timeframe candle had failed penetration; next distinct closed candle remains on reclaimed side; exact adjacent candle boundaries required | array of held USD levels |
| Structure transition | previous and latest classified confirmed fractal lows/highs, e.g. `LL/LH->HL/HH`; new-pivot flags only on the confirmation-close bar | category |
| Relative acceleration | current h-hour DOT/BTC ratio return minus same return observed h hours earlier, same configs and comparable snapshot time | percentage points |
| Breadth turn | `100*(current_positive_fraction-prior_hour_positive_fraction)`; same full eight-peer universe | percentage points |

Necessary reversal candidate gates: A = EMA20 extension >=2 ATR against trend on
closed 1h or 4h; B = same-direction executed-flow efficiency loss; C = strong hourly
close off the relevant extreme (CLV>=.75 / <=.25), failed penetration, appropriate
flow/price divergence, relative-strength acceleration or a newly confirmed HL/LH. All require actual available
values. `abc_ready=A and B and C`. A `trigger_candidate` additionally requires a
newly confirmed hourly HL for downside / LH for upside. These are candidate flags,
not a deterministic market regime or calibrated probability. RSI is not a gate.

Analogs: RMS distance of shared numeric coordinates divided by fixed configured
scales, plus `(total_coordinates-shared_coordinates)/total_coordinates`. At least
four of six shared coordinates; default distance cap 1.5. Matching version/configs,
compatible EMA-extension sign, outcome labels matured as of current snapshot,
nearest 40 with horizon-spaced starts. At least 20 neighbours before statistics.
Fixed scales do not use future returns, future medians, or a fitted full-history scaler.

Outcome labels: anchor at next complete minute after creation, forward return
`100*(last_close/first_open-1)`; market MFE `max(0,100*(max_high/first_open-1))`,
MAE `min(0,100*(min_low/first_open-1))`. Directional trade excursions use entry and
trade sign, only where intrabar ordering permits them. Theoretical R uses signed
exit-minus-entry divided by absolute entry-minus-failure; full-size T3/stop/horizon
exit, no assumed T1/T2 partial fills or execution costs. Ambiguous same-candle
barriers give no R. See [Oracle evaluation rules](ORACLE_V3.md#evaluator-rules).
