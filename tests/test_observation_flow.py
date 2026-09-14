import pandas as pd
import pytest
from observation_flow import execution_window, flow_windows, volume_profile

END = pd.Timestamp('2026-09-14T18:00:00Z')


def tape():
    frame = pd.DataFrame({'time': [END-pd.Timedelta(minutes=m) for m in (120, 90, 60, 30, 0)],
        'price': [1, 1.0099, 1.01, 1.015, 1.01], 'volume': [10, 2, 3, 4, 5],
        'side': ['b', 's', 'b', 'b', 's'], 'trade_id': ['0', '1', '2', '3', '4']})
    frame.attrs = {'coverage_start': (END-pd.Timedelta(hours=4)).isoformat(), 'coverage_end': END.isoformat()}
    return frame


def test_adjacent_windows_exact_boundaries_and_shared_cutoff():
    frame = tape()
    result = flow_windows(frame, frame, END+pd.Timedelta(seconds=10))
    assert result['status'] == 'ok'
    spot = result['data']['spot']
    assert spot['previous']['data']['trade_count'] == 2
    assert spot['current']['data']['trade_count'] == 2
    assert spot['previous']['data']['signed_volume_dot'] == 1
    assert spot['current']['data']['signed_volume_dot'] == -1
    assert spot['current_minus_previous']['signed_volume_dot'] == -2
    assert spot['current']['data']['first_to_last_price_change_pct'] == pytest.approx((1.01/1.015-1)*100)
    assert spot['current']['coverage']['window_start_utc'] == spot['previous']['coverage']['window_end_utc']
    assert spot['current']['asof_utc'] == result['data']['perp']['current']['asof_utc']
    assert result['data']['perp']['current']['data']['signed_volume_dot'] == -1


def test_partial_window_values_are_not_exported_as_full_measurements():
    frame = tape()
    frame.attrs['coverage_start'] = (END-pd.Timedelta(minutes=30)).isoformat()
    result = flow_windows(frame, None, END)
    assert result['status'] == 'partial'
    assert result['data']['spot']['current']['data'] is None
    assert result['data']['spot']['current_minus_previous'] is None


def test_complete_empty_window_is_zero_flow_with_no_invented_prices():
    frame = tape().iloc[0:0]
    row = execution_window(frame, END-pd.Timedelta(hours=1), END, 'fixture')
    assert row['status'] == 'ok'
    assert row['data']['signed_volume_dot'] == 0
    assert row['data']['trade_count'] == 0
    assert row['data']['first_trade_price_usd'] is None


def test_volume_profile_fixed_decimal_bins_conserve_trades_volume_and_notional():
    frame = tape()
    result = volume_profile(frame, END, 240, '0.005')
    assert result['status'] == 'ok'
    bins = {r['price_lower_usd']: r for r in result['data']}
    assert bins[1.005]['volume_dot'] == 2
    assert bins[1.01]['volume_dot'] == 8
    assert bins[1.015]['volume_dot'] == 4
    assert sum(r['volume_dot'] for r in bins.values()) == frame.volume.sum()
    assert sum(r['notional_usd'] for r in bins.values()) == pytest.approx((frame.price*frame.volume).sum())
    assert sum(r['trade_count'] for r in bins.values()) == 5
    frame.attrs['coverage_start'] = (END-pd.Timedelta(hours=1)).isoformat()
    assert volume_profile(frame, END)['data'] is None


def test_duplicate_ids_and_stale_endpoints_are_unavailable():
    frame = tape()
    frame.loc[1, 'trade_id'] = '0'
    assert volume_profile(frame, END)['data'] is None
    result = flow_windows(tape(), tape(), END+pd.Timedelta(minutes=10))
    assert result['data']['spot']['current']['data'] is None
    assert result['data']['perp']['current']['data'] is None


def test_stale_perp_does_not_discard_fresh_spot_measurements():
    stale = tape()
    stale.attrs['coverage_end'] = (END-pd.Timedelta(minutes=10)).isoformat()
    result = flow_windows(tape(), stale, END)
    assert result['data']['spot']['current']['status'] == 'ok'
    assert result['data']['perp']['current']['status'] == 'unavailable'


def test_perpetual_nonexecution_events_excluded_and_unknown_side_rejected():
    frame = tape()
    frame['type'] = ['fill', 'assignment', 'liquidation', 'fill', 'block']
    result = execution_window(frame, END-pd.Timedelta(hours=4), END, 'fixture')
    assert result['data']['trade_count'] == 3
    frame.loc[0, 'side'] = 'unknown'
    with pytest.raises(ValueError): execution_window(frame, END-pd.Timedelta(hours=4), END, 'fixture')
