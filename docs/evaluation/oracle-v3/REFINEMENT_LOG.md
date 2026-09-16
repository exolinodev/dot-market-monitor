# Engineering refinement log

| Iteration | Change and evidence | Result / limitation |
|---|---|---|
| 1 | Implement separate deterministic features, immutable forecasts, Python evaluator, scorecard, analogs and optional v2 context. Baseline: 199 Python tests passed. Replay 94 original hourly snapshots. | 12 downside-extension states; four downside and five upside inefficiency states; zero simultaneous A+B+C. Not evidence of forecasting success. |
| 2 | Review showed that touch-entry/exit candles cannot reveal exact pre-exit extrema. Null trade excursions when ordering is unknown; retain market-path excursions. Add explicit `failure_scope` for pre-entry invalidation. | Target/stop, trigger/target ambiguity, close-trigger ordering, pre-entry failure and gap cases have deterministic tests. No guessed fills or look-ahead. |
| 2 | Require real aligned DOT/BTC return endpoints, confirmed-candle receipt boundaries, contemporaneous manual anchor selection, future-history exclusion and archived config/input hashes. | Historical replay reproducible from original inputs; unavailable old flow/OI remains missing. |
| 2 | Bound consumer scorecard groups; cache schema validators; atomically create forecast **and input evidence**; reject archive changes in CI; retry pushes without force or conflict replacement. | Compact context remains below 45 KB. Concurrent publication permits exactly one complete forecast. Historical forecasts remain absent rather than fabricated. Final grades are rechecked by deterministic recomputation of archived candle evidence; matured missing coverage is counted and remains retryable. |
| 2 | Rewrite consumer around actual implemented IDs/schema; preserve original v2 prompt; prepare point-in-time model harness requests. | No model API key: no model invocations or claimed v2/v3 performance comparison. |

Final boundary audit: the existing measurement layer permits a small positive
source-clock skew. A regression fixture with an OI timestamp one second beyond the
snapshot reproduced a future Oracle coverage endpoint, even though its value was
unavailable. Oracle now rejects future source timestamps independently and clears
invalid/future coverage endpoints. Existing measurement freshness semantics stay
unchanged. The new regression passes and the historical replay is unchanged.

Iteration 3 was justified by a concrete long-term lifecycle defect: on-demand
analog labels disappeared after minute-cache rolloff, preventing a sufficient
horizon-spaced 12h sample from ever accumulating. Add a separate retained market
outcome archive with one-time matured labels and source/config/time identity.
A simulation with 22 successive 12h states and a rolling 12h candle cache now
retains all 22 neighbours after the cache is empty. This is a lifecycle test, not
a profitability result. Replay now walks every original historical cache revision,
so genuine early outcome windows are recovered instead of being declared missing
solely because they have left the final cache. No signal thresholds changed.

No signal threshold was optimised or changed after the replay. Fixed scales and
minimum samples were chosen as transparent starting assumptions. The only config
addition in refinement caps scorecard context size. No train/holdout profitability
result is reported because no fitting or actual model backtest occurred. The lack
of simultaneous A/B/C around the washout is an explicit remaining sensitivity
limitation, not concealed by adapting thresholds to that case.
