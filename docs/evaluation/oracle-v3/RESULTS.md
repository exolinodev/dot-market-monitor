# Oracle v3 replay and forensic evaluation

This report is a deterministic feature replay, **not an LLM backtest or realised
trading-performance claim**. No compatible model API key was available. No real
historical Oracle forecasts were invented. Original data end at
2026-09-16 18:50:47 UTC; this is also the preserved timestamp of `example_llm_snapshot.json.gz`.
Collector-owned files on the PR branch retain the latest main data and are not
replaced by this older example.

## Scope and reproducibility

- 94 unique genuine hourly v2 snapshots, 2026-09-12 18:41:28 to 2026-09-16 18:50:47 UTC.
- Git source tip: `beea3272ab8de4b3ed58ea27934837589c9e7a86`.
- Last original market-data commit: `adfd6e3f80dee69607002a3ec3aed23316392e71`.
- 48 original observation records starting 2026-09-14 19:50 UTC. Earlier candle,
  breadth and relative-strength observations are usable, but unavailable Oracle
  flow-window and OI-history fields are not reconstructed.
- All feature snapshots recomputed twice from identical historical input and prior
  history, with equal output. Original commit IDs are retained for every record.
- Fixed interpretable thresholds, no tuning against this dataset, no train/holdout
  profitability claim. An unused holdout was not invented after viewing the case.
- `replay_summary.json` contains counts and point-in-time case evidence;
  `replay_records.json.gz` contains all retrospective feature records and labels.
  These research artifacts are separate from the actual live feature archive.

The historical exporter covers all 94 snapshots. Model request preparation accepts
only the 48 with an original measurement-config hash and valid Oracle context;
46 earlier snapshots are explicitly skipped rather than inventing that contract.
Each eligible request contains only information matured as of its own reference. Request preparation and schema
fixture tests are not actual model invocations. The original v2 consumer is frozen
in `consumer_v2_frozen.md`; the v3 prompt is version 3.0.0.

## Verification

- Full Python suite: **234 passed** (199 existing plus 35 Oracle cases).
- Cloudflare scheduler suite: **17 passed**.
- Root snapshot validates as schema v2: 43 sources, 291,278 bytes.
- Canonical Oracle context: 23,273 bytes, below its 45,000-byte budget.
- All pre-existing exported measurement fields equal the original snapshot after
  removing only `oracle_context`; the preserved detailed example also retains its
  original measurement numbers.
- Archive-integrity guard, Python compilation and workflow YAML parsing passed.
- Integration covers snapshot → features → synthetic forecast → future candle
  fixture → evaluation → scorecard → consumer context. Publication races, forged
  outcome grades, ambiguity, unavailable coverage, stale/future inputs and version
  isolation are exercised without fabricating published forecasts.

## Availability and labels

| Quantity | Available snapshots / 94 |
|---|---:|
| Closed 1h/4h CLV, wick/ATR, EMA extension | 94 |
| Complete current spot/perp execution windows | 48 |
| Comparable same-direction spot impact change | 22 |
| Comparable same-direction perp impact change | 19 |
| OI 1h / 4h / 24h change | 47 / 44 / 24 |
| Relative return (1h/4h/24h) | 94 |
| Relative acceleration (1h/4h/24h) | 90 / 83 / 45 |
| Breadth turn, full matched universe | 90 |
| Contemporaneously registered AVWAP anchors | 48 |
| OI/flow percentiles meeting 168 prior samples | 0 |

| Forward spot labels | Fully covered | Partial | Pending |
|---|---:|---:|---:|
| 1h | 67 | 25 | 2 |
| 4h | 64 | 25 | 5 |
| 12h | 56 | 25 | 13 |

The next complete minute is the outcome anchor, with the delay explicitly recorded.
Twenty-five early snapshots lack exact minute-aligned coverage in the retained
cache. Coarser candles cannot silently include pre-publication movement. No partial
window was reported as a win.

The default rules identified 12 downside-extension states, four downside-flow
inefficiency states and five upside-flow inefficiency states. **Zero snapshots
satisfied simultaneous A+B+C; zero triggered reversal candidates.** There are
therefore zero scored early reversal warnings, and zero samples with which to claim
that the warning rule succeeds. This is conservative but may be under-sensitive.
A later prospective sample is needed to test that tradeoff. Missing efficiency
confirmation is not permission to assert confident continuation.

## Washout: what was actually knowable

The 0.9342 low occurred inside the 18:00–19:00 UTC candle on September 15. At 18:50
that candle was still open: using its eventual low/close/CLV as confirmed evidence
would be look-ahead. At 19:50 it was closed, but the hourly fractal low was **not**
yet confirmed. The confirming candle opened at 20:00 and closed at 21:00.

| Snapshot UTC | Known evidence at that time | Defensible state |
|---|---|---|
| Sep 15 18:50 | Spot 0.9392; OI 1h −4.63%; spot/perp signed flow approximately −96,559 / −206,043 DOT; last closed hour ended 18:00 at 0.9855 | Intrabar selloff/deleveraging, no completed washout reversal confirmation |
| Sep 15 19:50 | Closed hour low 0.9342, close 0.9554, CLV 0.418; EMA20 extension −2.345 ATR; negative-spot-flow/flat-up divergence; efficiency-loss flag false | EXHAUSTION_WATCH; A and a response available, B missing |
| Sep 15 21:50 | Extension −2.248 ATR; spot efficiency loss true; CLV 0.448, no matching downside response; 0.9342 now confirmed as LL | A+B, no C: still watch, no high-confidence long |
| Sep 15 23:50 | CLV 0.866; extension −2.243 ATR; perp divergence and relative turn; no contemporaneous sell-efficiency-loss flag | A+C, no B: improving response, still not REVERSAL_ARMED |
| Sep 16 03:50 | Hourly close 0.9480, CLV 0.816; 4h extension −2.283 ATR; relative improvement; no B | Recovery evidence improves; no fabricated A+B+C or confirmed new HL |

The default implementation does **not** retrospectively claim it caught the exact
bottom or the subsequent rally. In this case the required evidence appears across
different snapshots, not all at once. Carrying an exhaustion episode forward might
be a future version, but adding a fitted memory window after inspecting this one
case would not establish out-of-sample improvement.

## Before/after output examples (editorial, not model results)

These are illustrative renderings of the same available information, not quotes
from actual historical ChatGPT runs. No v2/v3 model comparison was performed.

**At Sep 15 19:50 UTC**, original snapshot commit
`02d7f89b40dcc4ed085968370acfdccf674540ae`:

Before, indicator-report format:

> Spot 0.9529. Closed 1h RSI 30.2, OI 1h −0.71%, spot flow −18,702 DOT.
> Structure remains weak; latest candle low 0.9342, close 0.9554.

After, v3 decision format:

> **ORACLE CALL: EXHAUSTION_WATCH.** Structural weakness and tactical entry quality
> are separate. The closed hour is 2.34 ATR below EMA20 and negative spot flow now
> coexists with flat/up tape price, but reduced impact with at least comparable
> selling effort is not confirmed. No high-confidence reversal call. An additional
> short near the low needs a fresh trigger and sufficient remaining downside.
>
> **PRIMARY:** If a new closed-hour reclaim of 0.9726 holds, assess a tactical long
> toward 0.9849 (washout-candle high / reaction), 0.9882 (current EMA20 / primary
> mean-reversion and potential reversal zone), then 0.9989 (confirmed pivot / structural decision).
> Failure below 0.9342 would invalidate that broad washout thesis. At nominal
> 0.9726 entry versus 0.9342 failure, T1 offers only about **0.32 R**: unattractive;
> this plan is not permission to enter. A later confirmed HL could change the risk
> distance, but cannot be invented now.
>
> **ALTERNATIVE:** Without reclaim, remain in watch/NO_TRADE rather than infer a
> long from RSI. **SQUEEZE/FAILURE:** A breach of 0.9342 challenges the washout thesis;
> no unobserved lower price target is fabricated. T1/T2 may reject a bounce; T3 is
> a structural decision, not a promised destination. A/B/C is incomplete.

Every number above was in that snapshot: 0.9726 and 0.9989 were confirmed pivots;
0.9849 and 0.9342 were the just-closed candle's extremes; 0.9882 was the contemporaneous EMA20 (frozen for the hypothetical target).
The later hourly **pivot classification** of 0.9342 is not used.

**At Sep 15 21:50 UTC**, commit `eaeb6498d23ef93a285c273f8db683e2a5b9ea07`:

> **ORACLE CALL: EXHAUSTION_WATCH, execution NO_TRADE.** Extension and diminishing
> sell impact now coexist, but the hour has not supplied the required positive
> response. The 0.9342 low is now a confirmed LL, not a new HL. If a subsequent
> closed reclaim supplies C, reconsider tactical long asymmetry; until then neither
> “RSI low, buy” nor “structure bearish, short here” is justified by these features.

Forensic outcome labels show roughly −0.99% and −0.81% over the following four hours
from the 19:50 and 21:50 snapshots respectively. Those values were excluded from
all features and examples; they explain why premature long certainty would also
have been unwarranted. They are not strategy returns because no forecast was issued.

## Refinement and readiness

See [REFINEMENT_LOG](REFINEMENT_LOG.md). Iteration 2 kept all signal thresholds
unchanged. It improved timestamp validation, explicit failure scope, entry/exit
ambiguity, strict schema coverage, bounded context and atomic publication evidence.
The replay counts remained stable after those corrections.

Recommendation: **ready for prospective parallel/shadow testing**, with human
review of the first published forecasts and the resulting deterministic labels.
Not validated for autonomous trading. First priorities are accumulating real
published forecasts, checking rejection/coverage rates, observing whether the strict
simultaneous gate suppresses useful setups, and running actual frozen-prompt API
experiments before considering any threshold changes.
