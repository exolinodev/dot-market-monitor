# Oracle v3 – Recursive Feedback Loop (Draft)

Status: conceptual draft created from the ChatGPT market-monitor discussion.

## Goal

Extend the existing deterministic `measurements_only` market-data contract with a separate Oracle feedback layer. The existing measurement pipeline remains authoritative for facts. Forecasts are stored immutably and evaluated later from future market data.

## Separation of concerns

1. **Measurements** – deterministic observed market data produced by the existing collector.
2. **Oracle Features** – deterministic, measurable features derived from data available at forecast time.
3. **Oracle Forecast** – structured model interpretation: bias, trigger, failure, targets, horizon, regime and confidence.
4. **Oracle Outcome** – deterministic ex-post evaluation: trigger, target-before-failure, MFE, MAE, returns and timing.
5. **Oracle Scorecard** – historical calibration and performance summaries fed back into future model context.

## Proposed files

- `config/oracle.json`
- `src/oracle_features.py`
- `src/oracle_evaluator.py`
- `data/raw/oracle_feature_history.json.gz`
- `data/raw/oracle_forecasts.json.gz`
- `data/oracle_scorecard.json`

A compact `markets.DOTUSD.oracle_context` block should be exported into `data/llm_snapshot.json`.

## Candidate deterministic features

- effort vs result / price impact per signed flow
- OI deleveraging intensity
- candle close-location value
- wick size relative to ATR
- failed-breakdown / reclaim state
- flow-price divergence
- distance from EMA/VWAP/levels in ATR units
- independent reversal-stack count with lineage protection
- relative-strength acceleration
- breadth turn / breadth acceleration

## Forecast contract

Each forecast must be immutable after creation and should include at least:

- unique forecast id
- creation timestamp
- source snapshot timestamp / identifier
- strategy / schema version
- horizon
- regime
- macro bias
- tactical / trade bias
- trigger definition
- failure / invalidation definition
- one or more targets
- qualitative or explicitly uncalibrated confidence
- feature references used in the decision

## Evaluation contract

The evaluator, not the model, determines whether a forecast succeeded. Recommended metrics:

- trigger reached
- target 1/2/3 before failure
- maximum favourable excursion (MFE)
- maximum adverse excursion (MAE)
- realised return after fixed horizons (e.g. 1h / 4h / 12h)
- time to trigger / targets / failure
- R-multiple where a valid trigger/failure geometry exists
- calibration metrics once sufficient sample size exists

## Guardrails

- Never modify an existing forecast after publication.
- Never use observations that were unavailable at forecast time.
- Version feature logic and scoring rules.
- Keep old and new strategy versions statistically separate.
- Preserve input lineage to avoid double-counting indicators derived from the same data.
- Historical percentages are descriptive until minimum sample and validation requirements are met.
- Use walk-forward evaluation rather than optimising and scoring on the same period.

## Recursive loop

`market data -> deterministic features -> structured oracle forecast -> immutable archive -> future data -> deterministic evaluator -> scorecard -> oracle_context -> next forecast`

This is contextual online calibration, not live weight-training of the base model.
