"""Measured execution windows and trade-price bins; no participant inference."""
from decimal import Decimal, ROUND_FLOOR
import pandas as pd
from common import freshness
from observation_common import block, utc, iso
from orderflow import validate_trades


def covered_trades(frame, start, end):
    if frame is None:
        return None, 'trade_source_unavailable'
    validate_trades(frame)
    begin = frame.attrs.get('coverage_start')
    finish = frame.attrs.get('coverage_end')
    if begin is None or finish is None or utc(begin) > start or utc(finish) < end:
        return None, 'complete_trade_window_unavailable'
    work = frame[(frame.time > start) & (frame.time <= end)].copy()
    if 'trade_id' not in work or work.trade_id.duplicated().any():
        return None, 'unique_trade_ids_required'
    if 'type' in work:
        work = work[work.type.isin(['fill', 'liquidation'])]
    return work.sort_values(['time', 'trade_id']), None


def execution_window(frame, start, end, source):
    work, reason = covered_trades(frame, start, end)
    coverage = {'window_start_utc': iso(start), 'window_end_utc': iso(end),
                'window_boundary': '(start, end]', 'complete': work is not None}
    if work is None:
        return block(reason=reason, sources=[source], asof=end, coverage=coverage)
    buys, sells = work[work.side == 'b'], work[work.side == 's']
    first = float(work.price.iloc[0]) if len(work) else None
    last = float(work.price.iloc[-1]) if len(work) else None
    return block({
        'buy_volume_dot': float(buys.volume.sum()), 'sell_volume_dot': float(sells.volume.sum()),
        'buy_notional_usd': float((buys.price * buys.volume).sum()),
        'sell_notional_usd': float((sells.price * sells.volume).sum()),
        'signed_volume_dot': float(buys.volume.sum() - sells.volume.sum()),
        'trade_count': len(work), 'first_trade_price_usd': first, 'last_trade_price_usd': last,
        'first_trade_utc': iso(work.time.iloc[0]) if len(work) else None,
        'last_trade_utc': iso(work.time.iloc[-1]) if len(work) else None,
        'first_to_last_price_change_pct': (last / first - 1) * 100 if first else None,
    }, sources=[source], asof=end, coverage=coverage)


def flow_windows(spot, perp, reference, minutes=60):
    # A shared cutoff supports comparison even when requests finish at different times.
    spot, perp = (frame if frame is not None and freshness(frame.attrs.get('coverage_end'), utc(reference), 120)['fresh']
                  else None for frame in (spot, perp))
    available = [utc(f.attrs['coverage_end']) for f in (spot, perp)
                 if f is not None and f.attrs.get('coverage_end') is not None]
    end = min([utc(reference), *available]).floor('min')
    if (utc(reference) - end).total_seconds() > 180:
        return block(reason='trade_window_endpoint_stale', asof=end)
    start = end - pd.Timedelta(minutes=minutes)
    data = {}
    for name, frame, source in [('spot', spot, 'DOTUSD.trades'), ('perp', perp, 'DOTPERP.trades')]:
        current = execution_window(frame, start, end, source)
        previous = execution_window(frame, start - pd.Timedelta(minutes=minutes), start, source)
        differences = None
        if current['status'] == previous['status'] == 'ok':
            differences = {key: current['data'][key] - previous['data'][key]
                           for key in ('buy_volume_dot', 'sell_volume_dot', 'signed_volume_dot', 'trade_count')}
        data[name] = {'current': current, 'previous': previous,
                      'current_minus_previous': differences}
    return block(data, status='ok' if all(v['current_minus_previous'] is not None for v in data.values()) else 'partial',
                 sources=['DOTUSD.trades', 'DOTPERP.trades'], asof=end,
                 coverage={'window_minutes': minutes, 'adjacent_nonoverlapping_windows': True,
                           'shared_cutoff_utc': iso(end), 'signed_volume_resets_each_window': True})


def volume_profile(frame, reference, minutes=240, bin_width='0.005'):
    end = min(utc(reference), utc(frame.attrs['coverage_end'])) if frame is not None and frame.attrs.get('coverage_end') else utc(reference)
    start = end - pd.Timedelta(minutes=minutes)
    coverage = {'window_start_utc': iso(start), 'window_end_utc': iso(end),
                'bin_width_usd': float(Decimal(bin_width)), 'bin_origin_usd': 0,
                'bin_boundaries': '[lower, upper)', 'trade_source': 'spot', 'complete': False}
    if (utc(reference) - end).total_seconds() > 120:
        return block(reason='trade_source_stale', sources=['DOTUSD.trades'], asof=end, coverage=coverage)
    work, reason = covered_trades(frame, start, end)
    if work is None:
        return block(reason=reason, sources=['DOTUSD.trades'], asof=end, coverage=coverage)
    width = Decimal(bin_width)
    if not width.is_finite() or width <= 0:
        raise ValueError('Profile bin width must be positive')
    bins = {}
    for trade in work.itertuples():
        index = int((Decimal(str(trade.price)) / width).to_integral_value(rounding=ROUND_FLOOR))
        row = bins.setdefault(index, {'price_lower_usd': float(index * width),
            'price_upper_usd': float((index + 1) * width), 'volume_dot': 0.0,
            'buy_volume_dot': 0.0, 'sell_volume_dot': 0.0, 'notional_usd': 0.0, 'trade_count': 0})
        row['volume_dot'] += float(trade.volume)
        row['buy_volume_dot' if trade.side == 'b' else 'sell_volume_dot'] += float(trade.volume)
        row['notional_usd'] += float(trade.price * trade.volume)
        row['trade_count'] += 1
    if len(bins) > 200:
        return block(reason='profile_exceeds_200_bins', sources=['DOTUSD.trades'], asof=end, coverage=coverage)
    coverage.update(complete=True, trade_count=len(work))
    return block([bins[k] for k in sorted(bins)], sources=['DOTUSD.trades'], asof=end, coverage=coverage)
