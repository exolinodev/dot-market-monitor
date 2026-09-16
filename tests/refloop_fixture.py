"""Synthetic consumer contract cases; never feed these files to production history."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from test_oracle import bars, fixture_forecast
from oracle_common import digest, validate
from oracle_forecasts import forecast_id
from oracle_evaluator import evaluate_forecast
from oracle_scorecard import scorecard


def build_cases():
    cases = {}
    reference = '2026-09-17T07:00:00Z'
    for i, name in enumerate('abcdef', 1):
        f = fixture_forecast(direction='NONE' if name == 'e' else 'LONG')
        f['snapshot_sha256'] = str(i) * 64
        f['forecast_id'] = forecast_id(f['created_at_utc'], f['snapshot_sha256'])
        if name == 'd':
            f['strategy_version'] = 'oracle-v2-test-incompatible'
        if name == 'e':
            for horizon in f['forecast_horizons'].values():
                horizon['direction'] = 'ABSTAIN'
        frame = bars()
        if name in ('b', 'd'):
            frame.iloc[5, frame.columns.get_loc('high')] = 102.5
            frame.iloc[10, frame.columns.get_loc('low')] = 97.5
        if name == 'c':
            frame.iloc[4, frame.columns.get_loc('high')] = 103.
            frame.iloc[4, frame.columns.get_loc('low')] = 97.
        evaluated_at = '2026-09-16T19:30:00Z' if name in ('a', 'f') else reference
        outcome = evaluate_forecast(f, {1: frame}, evaluated_at)
        # One bounded complete horizon record, matching the archived file shape.
        outcome['horizons'] = {'1h': outcome['horizons']['1h']}
        validate(f, 'oracle_forecast.schema.json')
        validate(outcome, 'oracle_outcome.schema.json')
        day = '2026/09/16/' + f['forecast_id'] + '.json'
        stored = name != 'f'
        cases[name] = {
            'test_only': True, 'synthetic_market_data': True,
            'case_id': name, 'reference_at_utc': evaluated_at,
            'expected_current_strategy_version': 'oracle-v3.0.0',
            'forecast': f,
            'storage_readback_fixture': {
                'submission_exists': True, 'writer_status': 'completed_success' if stored else 'queued',
                'final_forecast_path': 'data/oracle/forecasts/' + day,
                'final_forecast_exists': stored,
                'final_forecast_sha256': digest(f) if stored else None},
            'outcome': outcome if stored else None,
            'scorecard': scorecard([f], [outcome], evaluated_at) if stored else None,
            'previously_consulted_outcome_ids':
                [[f['forecast_id'], '1h', outcome['evaluator_version']]] if name == 'c' else []}
    return cases


if __name__ == '__main__':
    out = ROOT / 'docs/evaluation/oracle-v3/refloop-smoke'
    out.mkdir(exist_ok=True)
    cases = build_cases()
    for name, case in cases.items():
        (out / (name + '.json')).write_text(json.dumps(case, ensure_ascii=False, indent=2) + '\n')
    (out / 'index.json').write_text(json.dumps({
        'test_only': True,
        'notice': 'Synthetic contract test, not market evidence or actual published performance. Storage receipts are simulated. Do not publish any fixture to data/oracle.',
        'cases': [{'case_id': name, 'path': name + '.json', 'sha256': digest(case)} for name, case in cases.items()]
    }, indent=2) + '\n')
