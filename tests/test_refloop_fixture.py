"""Ensure browser test evidence has real Python grades and remains reproducible."""
import json
from pathlib import Path

from refloop_fixture import build_cases


def test_browser_feedback_cases_are_reproducible_and_distinct():
    cases = build_cases()
    root = Path(__file__).parents[1] / 'docs/evaluation/oracle-v3/refloop-smoke'
    for name, case in cases.items():
        assert json.loads((root / (name + '.json')).read_text()) == case
        assert (root / (name + '.json')).stat().st_size <= 10000
    assert cases['a']['outcome']['horizons']['1h']['status'] == 'pending'
    hit_then_stop = cases['b']['outcome']['horizons']['1h']
    assert hit_then_stop['target_before_failure'] is True and hit_then_stop['r_multiple'] == -1
    assert cases['b']['scorecard']['groups'][0]['target_before_failure_rates']['T1'] is None
    assert cases['c']['outcome']['horizons']['1h']['status'] == 'ambiguous'
    assert cases['c']['scorecard']['groups'][0]['resolved_triggered_count'] == 0
    assert cases['d']['scorecard']['groups'][0]['strategy_version'] != cases['b']['scorecard']['groups'][0]['strategy_version']
    assert cases['e']['outcome']['horizons']['1h']['status'] == 'abstained'
    assert cases['e']['scorecard']['groups'][0]['directional_sample_count'] == 0
    assert cases['f']['outcome'] is None and not cases['f']['storage_readback_fixture']['final_forecast_exists']
