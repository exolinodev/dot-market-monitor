import copy
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from time_fibs import CONFIG_PATH, build_time_fibs, cluster_projections, load_time_fibs

REFERENCE = '2026-09-14T17:50:44.142873Z'
SET_ID = 'count_b_same_degree_2026_09'


@pytest.fixture
def config():
    return json.loads(CONFIG_PATH.read_text())


def selected(config):
    return config['anchor_sets'][SET_ID]


def test_exact_durations_and_all_default_projections(config):
    block = build_time_fibs(REFERENCE, config)
    assert block['status'] == 'ok'
    assert block['durations'] == {'A_B_hours': 56.0, 'B_C_hours': 52.0, 'A_C_hours': 108.0}
    assert {(e['source_duration'], e['ratio']): e['projected_at_utc'] for e in block['projections']} == {
        ('B_C', 0.618): '2026-09-14T16:08:09.6Z',
        ('B_C', 1.0): '2026-09-15T12:00:00Z',
        ('B_C', 1.272): '2026-09-16T02:08:38.4Z',
        ('B_C', 1.618): '2026-09-16T20:08:09.6Z',
        ('A_B', 0.618): '2026-09-14T18:36:28.8Z',
        ('A_B', 1.0): '2026-09-15T16:00:00Z',
        ('A_B', 1.272): '2026-09-16T07:13:55.2Z',
        ('A_B', 1.618): '2026-09-17T02:36:28.8Z',
    }
    symmetry, = block['symmetry_projections']
    assert (symmetry['source_duration'], symmetry['ratio'], symmetry['projected_at_utc']) == (
        'A_C', 0.5, '2026-09-15T14:00:00Z')
    assert [a['price'] for a in block['anchors']] == [1.2822, 1.1642, 0.9959]
    assert block['anchors'][1]['role'] == 'corrective_high'


def test_two_exact_clusters_with_complete_sources_and_madrid_display(config):
    block = build_time_fibs(REFERENCE, config)
    first, second = block['clusters']
    assert (first['window_start_utc'], first['center_utc'], first['window_end_utc'], first['event_count']) == (
        '2026-09-14T16:08:09.6Z', '2026-09-14T17:22:19.2Z', '2026-09-14T18:36:28.8Z', 2)
    assert (second['window_start_utc'], second['center_utc'], second['window_end_utc'], second['event_count']) == (
        '2026-09-15T12:00:00Z', '2026-09-15T14:00:00Z', '2026-09-15T16:00:00Z', 3)
    assert [(e['source_duration'], e['ratio']) for e in first['events']] == [('B_C', 0.618), ('A_B', 0.618)]
    assert [(e['source_duration'], e['ratio']) for e in second['events']] == [('B_C', 1.0), ('A_C', 0.5), ('A_B', 1.0)]
    events = block['projections'] + block['symmetry_projections']
    assert all(e in events for c in block['clusters'] for e in c['events'])
    madrid = ZoneInfo('Europe/Madrid')
    assert [datetime.fromisoformat(c[k]).astimezone(madrid).strftime('%H:%M')
            for c in (first, second) for k in ('window_start_utc', 'center_utc', 'window_end_utc')] == [
                '18:08', '19:22', '20:36', '14:00', '16:00', '18:00']


@pytest.mark.parametrize('reference,state,to_center,to_start,since_end', [
    ('2026-09-15T11:00:00Z', 'upcoming', 180, 60, None),
    ('2026-09-15T12:00:00Z', 'active', 120, 0, None),
    ('2026-09-15T14:00:00Z', 'active', 0, None, None),
    ('2026-09-15T15:00:00Z', 'active', None, None, None),
    ('2026-09-15T16:00:00Z', 'active', None, None, None),
    ('2026-09-15T17:00:00Z', 'expired', None, None, 60),
])
def test_cluster_state_boundaries(config, reference, state, to_center, to_start, since_end):
    cluster = build_time_fibs(reference, config)['clusters'][1]
    assert [cluster[k] for k in ('state', 'minutes_to_center', 'minutes_to_start', 'minutes_since_end')] == [
        state, to_center, to_start, since_end]


@pytest.mark.parametrize('reference,state,until,since', [
    ('2026-09-15T11:59:59.4Z', 'upcoming', 0.01, None),
    ('2026-09-15T12:00:00Z', 'active', 0, None),
    ('2026-09-15T12:00:00.6Z', 'expired', None, 0.01),
])
def test_point_events_have_no_implicit_window(config, reference, state, until, since):
    event = next(e for e in build_time_fibs(reference, config)['projections'] if e['projection_id'] == 'B_C_1.000')
    assert [event[k] for k in ('state', 'minutes_to_center', 'minutes_to_start', 'minutes_since_end')] == [
        state, until, until, since]


@pytest.mark.parametrize('field,value', [
    ('market_type', 'perp'), ('market_type', 'perpetual'), ('market_type', None),
    ('pivot_method', 'atr_zigzag'), ('pivot_method', None),
    ('confirmed', False), ('confirmed', 'true'), ('confirmed', 1),
    ('candle_state', 'live'), ('candle_state', 'open'), ('candle_state', None),
    ('time_utc', '2026-09-08T20:00:00'), ('time_utc', 'invalid'),
    ('time_utc', None), ('price', 0), ('price', -1), ('price', 'NaN'),
    ('price', 'Infinity'), ('price', '1e9999'), ('price', '1e-9999'), ('price', True),
    ('confirmed_at_utc', '2026-09-08T20:00:00Z'), ('confirmed_at_utc', None),
    ('type', 'live_high'), ('id', 'B'), ('id', []),
])
def test_invalid_or_untrusted_anchor_rejects_whole_set(config, field, value):
    selected(config)['anchors'][0][field] = value
    block = build_time_fibs(REFERENCE, config)
    assert block['status'] == 'error'
    assert block['reason']
    assert block['anchors'] == block['projections'] == block['symmetry_projections'] == block['clusters'] == []
    assert block['durations'] is None


@pytest.mark.parametrize('key', ['time_utc', 'price', 'confirmed', 'confirmed_at_utc', 'market_type', 'candle_state'])
def test_missing_anchor_fields_do_not_get_defaults(config, key):
    del selected(config)['anchors'][1][key]
    assert build_time_fibs(REFERENCE, config)['status'] == 'error'


def test_absent_invalid_and_future_sets_do_not_fallback(config, tmp_path):
    assert build_time_fibs(REFERENCE, {})['status'] == 'unavailable'
    assert build_time_fibs(REFERENCE, None)['status'] == 'error'
    assert build_time_fibs(REFERENCE, {'active_anchor_sets': []})['status'] == 'error'
    assert load_time_fibs(REFERENCE, tmp_path/'absent.json')['status'] == 'unavailable'
    invalid = tmp_path/'invalid.json'
    invalid.write_text('{')
    assert load_time_fibs(REFERENCE, invalid)['status'] == 'error'
    assert build_time_fibs('2026-09-13T19:59:59.999999Z', config)['status'] == 'unavailable'
    assert build_time_fibs('2026-09-13T20:00:00Z', config)['status'] == 'ok'
    selected(config)['anchors'] = []
    assert build_time_fibs(REFERENCE, config)['status'] == 'unavailable'
    del config['anchor_sets'][SET_ID]
    assert build_time_fibs(REFERENCE, config)['status'] == 'unavailable'


@pytest.mark.parametrize('reference', [None, 'invalid', '2026-09-14T00:00:00'])
def test_bad_reference_time_is_error(config, reference):
    assert build_time_fibs(reference, config)['status'] == 'error'


def test_anchor_order_is_by_explicit_id_and_time_must_increase(config):
    expected = build_time_fibs(REFERENCE, config)
    selected(config)['anchors'].reverse()
    assert build_time_fibs(REFERENCE, config) == expected
    selected(config)['anchors'][1]['time_utc'] = '2026-09-08T20:00:00Z'
    assert build_time_fibs(REFERENCE, config)['status'] == 'error'


def test_additional_sets_require_explicit_selection_and_degree(config):
    other = copy.deepcopy(selected(config))
    other['anchors'][0]['time_utc'] = '2026-09-08T19:00:00Z'
    config['anchor_sets']['other'] = other
    assert build_time_fibs(REFERENCE, config)['durations']['A_B_hours'] == 56
    config['active_anchor_sets']['DOTUSD'] = 'other'
    assert build_time_fibs(REFERENCE, config)['durations']['A_B_hours'] == 57
    other['selection'] = 'automatic'
    assert build_time_fibs(REFERENCE, config)['status'] == 'error'
    other['selection'] = 'explicit_same_degree'
    other['market'] = 'DOTPERP'
    assert build_time_fibs(REFERENCE, config)['status'] == 'error'


def test_optional_ratios_and_invalid_ratios(config):
    selected(config)['ratios'] += ['2.000', '2.618']
    result = build_time_fibs(REFERENCE, config)
    assert result['status'] == 'ok'
    assert len(result['projections']) == 12
    assert next(e for e in result['projections'] if e['projection_id'] == 'B_C_2.000')['projected_at_utc'] == '2026-09-17T16:00:00Z'
    assert next(e for e in result['projections'] if e['projection_id'] == 'A_B_2.618')['projected_at_utc'] == '2026-09-19T10:36:28.8Z'
    for ratios in ([], None, [0.618, '0.618'], ['NaN'], [0], ['invalid'], [0.786]):
        selected(config)['ratios'] = ratios
        assert build_time_fibs(REFERENCE, config)['status'] == 'error'


def test_timezone_and_input_order_independence_without_mutation(config):
    original = copy.deepcopy(config)
    expected = build_time_fibs(REFERENCE, config)
    assert config == original
    previous = os.environ.get('TZ')
    try:
        for zone in ('UTC', 'Europe/Madrid', 'America/Los_Angeles', 'Pacific/Auckland'):
            os.environ['TZ'] = zone
            time.tzset()
            assert build_time_fibs(REFERENCE, config) == expected
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()
    for anchor in selected(config)['anchors']:
        for key in ('time_utc', 'confirmed_at_utc'):
            anchor[key] = datetime.fromisoformat(anchor[key]).astimezone(ZoneInfo('Europe/Madrid')).isoformat()
    selected(config)['ratios'].reverse()
    reference = datetime.fromisoformat(REFERENCE).astimezone(ZoneInfo('Pacific/Auckland')).isoformat()
    assert build_time_fibs(reference, config) == expected


def event(hour, source='A_B', ratio=1):
    return {'projection_id': f'{source}_{ratio}', 'source_duration': source, 'ratio': ratio,
            'projected_at_utc': (datetime.fromisoformat('2026-09-15T00:00:00Z') + timedelta(hours=hour)).isoformat()}


def test_cluster_no_chaining_dedup_independence_and_stable_order():
    a, b, c, d = event(0), event(4, 'B_C'), event(5, ratio=1.618), event(9, 'B_C', 1.618)
    groups = cluster_projections([d, b, c, a, a], REFERENCE, SET_ID)
    assert [g['event_count'] for g in groups] == [2, 2]
    assert [g['center_utc'] for g in groups] == ['2026-09-15T02:00:00Z', '2026-09-15T07:00:00Z']
    assert groups == cluster_projections([a, b, c, d], REFERENCE, SET_ID)
    assert cluster_projections([a, a], REFERENCE, SET_ID) == []
    assert cluster_projections([a, event(1, ratio=1.618)], REFERENCE, SET_ID) == []
    assert cluster_projections([a, event(4 + 1/3600, 'B_C')], REFERENCE, SET_ID) == []
    simultaneous, = cluster_projections([a, event(0, 'B_C')], REFERENCE, SET_ID)
    assert simultaneous['event_count'] == 2
    assert simultaneous['window_start_utc'] == simultaneous['center_utc'] == simultaneous['window_end_utc']


def test_cluster_uses_median_not_span_midpoint():
    group, = cluster_projections([event(0), event(1, 'B_C'), event(4, 'A_C', 0.5)], REFERENCE, SET_ID)
    assert group['center_utc'] == '2026-09-15T01:00:00Z'


def test_nonindependent_prefix_does_not_hide_later_cluster():
    a, b, c = event(0), event(3, ratio=1.618), event(6, 'B_C')
    group, = cluster_projections([a, b, c], REFERENCE, SET_ID)
    assert group['events'] == [b, c]


def test_no_directional_interpretation(config):
    text = json.dumps(build_time_fibs(REFERENCE, config))
    for forbidden in ('bullish', 'bearish', 'breakout', 'reversal', 'impulse_started', 'wave_count'):
        assert forbidden not in text
