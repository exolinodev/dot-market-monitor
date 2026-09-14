import copy
import numpy as np
import pandas as pd
import pytest
from observation_candles import price_level_observations, anchored_vwap, historical_context, market_relative


def candles(closes, start='2026-09-01T00:00:00Z', minutes=60):
    values = np.array(closes, dtype=float)
    index = pd.date_range(start, periods=len(values), freq=f'{minutes}min')
    return pd.DataFrame({'open': values, 'high': values+.5, 'low': values-.5,
        'close': values, 'vwap': values, 'volume': np.ones(len(values)), 'trade_count': 1}, index=index)


def end(frame):
    return frame.index[-1]+pd.Timedelta(hours=1)


def anchors(frame):
    return {'status': 'ok', 'anchor_set_id': 'fixture', 'anchors': [{
        'id': 'C', 'time_utc': frame.index[0].isoformat(), 'confirmed_at_utc': frame.index[1].isoformat(),
        'market_type': 'spot', 'confirmed': True, 'candle_state': 'closed'}]}


def test_level_cross_closed_counts_and_frozen_atr():
    frame = candles([9]*24+[9, 11, 13, 12, 10])
    result = price_level_observations(frame, 60, end(frame), [10], window=4)
    row, = result['data']
    assert (row['closes_above'], row['closes_below'], row['closes_equal']) == (3, 0, 1)
    assert row['last_close_side'] == 'equal'
    assert row['consecutive_closes_on_last_side'] == 1
    crossing = row['last_cross_in_window']
    assert crossing['bars_since'] == 3
    assert crossing['close_side'] == 'above'
    assert crossing['max_close_move_against_cross_usd'] == 3
    assert crossing['atr_at_cross_usd'] == pytest.approx((13+2.5)/14)
    assert crossing['max_close_move_against_cross_atr'] == pytest.approx(3/((13+2.5)/14))
    assert crossing['atr_at_cross_usd'] != row['atr14_usd']


def test_no_cross_is_not_an_invented_ancient_cross_and_counts_are_capped():
    frame = candles([11]*30)
    row, = price_level_observations(frame, 60, end(frame), [10], 24)['data']
    assert row['last_cross_in_window'] is None
    assert row['consecutive_count_capped']
    assert row['consecutive_closes_on_last_side'] == 24


def test_live_candle_and_future_price_cannot_change_level_observations():
    frame = candles([10]*30+[999])
    reference = frame.index[-1]+pd.Timedelta(minutes=30)
    expected = price_level_observations(frame.iloc[:-1], 60, reference, [10], 24)
    assert price_level_observations(frame, 60, reference, [10], 24) == expected
    assert expected['coverage']['window_end_utc'] == frame.index[-1].isoformat().replace('+00:00', 'Z')
    assert price_level_observations(frame.iloc[:-2], 60, reference, [10], 24)['status'] == 'unavailable'
    assert price_level_observations(frame.drop(frame.index[10]), 60, reference, [10], 24)['status'] == 'unavailable'


def test_anchored_vwap_uses_exchange_vwap_and_volume_exactly():
    frame = candles([10, 20, 30])
    frame['vwap'] = [9, 19, 29]
    frame['volume'] = [1, 2, 3]
    result = anchored_vwap(frame, 60, end(frame), anchors(frame))
    row, = result['data']
    assert row['data']['vwap_usd'] == pytest.approx(134/6)
    assert row['data']['slope_last_bar_usd'] == pytest.approx(134/6-47/3)
    assert row['data']['cumulative_volume_dot'] == 6
    assert row['coverage']['anchor_time_semantics'] == 'pivot_candle_open'


def test_zero_volume_candles_do_not_require_a_fabricated_vwap():
    frame = candles([10, 20, 30])
    frame['volume'] = [1, 0, 3]
    frame.loc[frame.index[1], 'vwap'] = np.nan
    row = anchored_vwap(frame, 60, end(frame), anchors(frame))['data'][0]
    assert row['data']['vwap_usd'] == 25


@pytest.mark.parametrize('issue', ['missing_start', 'gap', 'zero_volume', 'bad_vwap', 'unconfirmed', 'perp'])
def test_anchored_vwap_requires_proven_spot_anchor_and_complete_input(issue):
    frame = candles([10, 20, 30, 40])
    config = anchors(frame)
    if issue == 'missing_start': frame = frame.iloc[1:]
    if issue == 'gap': frame = frame.drop(frame.index[2])
    if issue == 'zero_volume': frame['volume'] = 0
    if issue == 'bad_vwap': frame['vwap'] = np.nan
    if issue == 'unconfirmed': config['anchors'][0]['confirmed'] = False
    if issue == 'perp': config['anchors'][0]['market_type'] = 'perp'
    result = anchored_vwap(frame, 60, end(frame), config)
    assert result['data'][0]['status'] == 'unavailable'
    assert result['data'][0]['data'] is None


def test_historical_midrank_excludes_current_observation_and_reports_sample_size():
    frame = candles([10]*60)
    expected = historical_context(frame, 60, end(frame), baseline=20, minimum=10, movement=4)
    atr = expected['data']['atr14_pct']
    assert atr['historical_percentile'] == 50
    assert atr['sample_count'] == 20
    assert atr['baseline_complete']
    assert atr['baseline_end_utc'] == frame.index[-1].isoformat().replace('+00:00', 'Z')
    assert expected['data']['movement_efficiency']['current_value'] is None
    assert expected['data']['movement_efficiency']['historical_percentile'] is None
    changed = frame.copy()
    changed.iloc[-1, changed.columns.get_loc('high')] = 20
    actual = historical_context(changed, 60, end(changed), baseline=20, minimum=10, movement=4)
    assert actual['data']['atr14_pct']['historical_percentile'] == 100
    assert actual['data']['atr14_pct']['sample_count'] == 20
    limited = historical_context(frame, 60, end(frame), baseline=720, minimum=168)
    assert limited['data']['atr14_pct']['historical_percentile'] is None
    assert not limited['data']['atr14_pct']['baseline_complete']


def test_efficiency_and_range_have_known_values():
    frame = candles([10]*30+[11, 12, 11, 14])
    row = historical_context(frame, 60, end(frame), 20, 10, movement=4)['data']
    assert row['movement_efficiency']['current_value'] == pytest.approx(4/6)
    assert row['range_width_pct']['current_value'] == pytest.approx(4/14*100)


def test_beta_fit_excludes_measured_period_and_is_not_a_forecast():
    r = np.sin(np.arange(61))*0.005
    btc = candles(100*np.exp(np.cumsum(r)))
    dot = candles(10*np.exp(np.cumsum(2*r)))
    reference = end(dot)
    first = market_relative(dot, btc, reference, horizons=[4], estimation=24)['data']['4h']
    assert first['data']['beta'] == pytest.approx(2)
    assert first['data']['beta_adjusted_log_return_pp'] == pytest.approx(0, abs=1e-12)
    dot.loc[dot.index[-1], 'close'] *= np.exp(.1)
    changed = market_relative(dot, btc, reference, horizons=[4], estimation=24)['data']['4h']
    assert changed['data']['beta'] == pytest.approx(2)
    assert changed['data']['beta_adjusted_log_return_pp'] == pytest.approx(10)
    assert changed['coverage']['estimation_end_utc'] == changed['coverage']['return_start_utc']
    assert changed['coverage']['estimation_observations'] == 24


def test_relative_returns_reject_missing_misaligned_or_flat_peer_history():
    dot, btc = candles(np.linspace(10, 20, 60)), candles([100]*60)
    for bad in (btc, btc.drop(btc.index[-5]), btc.iloc[:-1]):
        result = market_relative(dot, bad, end(dot), horizons=[4], estimation=24)
        assert result['status'] != 'ok'
        if result['data']: assert result['data']['4h']['data'] is None
