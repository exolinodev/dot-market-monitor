"""Closed-candle measurements; no level importance, regimes or forecasts."""
import numpy as np
import pandas as pd
from common import finite
from indicators import atr
from observation_common import block, iso, regular, last_complete_frame, utc


def price_level_observations(frame, minutes, reference, levels, window=24):
    sources = [f'DOTUSD.ohlc.{minutes}']
    frame = last_complete_frame(frame, minutes, reference)
    coverage = {'interval_minutes': minutes, 'requested_window_bars': window,
                'available_bars': 0 if frame is None else len(frame), 'closed_candles_only': True}
    if frame is None or len(frame) < max(window + 1, 14) or not regular(frame, minutes):
        return block(reason='insufficient_or_gapped_closed_candles', sources=sources, coverage=coverage)
    av = atr(frame, 14)
    current_atr = finite(av.iloc[-1])
    work = frame.tail(window)
    end = work.index[-1] + pd.Timedelta(minutes=minutes)
    coverage.update(window_start_utc=iso(work.index[0]), window_end_utc=iso(end), sample_count=len(work))
    results = []
    for level in levels:
        side = np.sign(work.close.to_numpy() - level).astype(int)
        preceding = frame.close.shift(1).loc[work.index].to_numpy()
        crosses = np.flatnonzero(((preceding <= level) & (side > 0)) | ((preceding >= level) & (side < 0)))
        count = 0
        for position in side[::-1]:
            if position != side[-1]:
                break
            count += 1
        last_cross = None
        if len(crosses):
            index = int(crosses[-1])
            closes = work.close.iloc[index:]
            against = (closes.cummax() - closes).max() if side[index] > 0 else (closes - closes.cummin()).max()
            frozen_atr = finite(av.loc[closes.index[0]])
            last_cross = {
                'closed_at_utc': iso(closes.index[0] + pd.Timedelta(minutes=minutes)),
                'close_side': 'above' if side[index] > 0 else 'below',
                'bars_since': len(closes) - 1,
                'max_close_move_against_cross_usd': float(against),
                'atr_at_cross_usd': frozen_atr,
                'max_close_move_against_cross_atr': float(against / frozen_atr) if frozen_atr and frozen_atr > 0 else None,
            }
        close = float(work.close.iloc[-1])
        results.append({
            'level_usd': level, 'level_origin': 'config/observations.json',
            'last_closed_price_usd': close, 'atr14_usd': current_atr,
            'distance_usd': close - level, 'distance_pct': (close / level - 1) * 100,
            'distance_atr': (close - level) / current_atr if current_atr and current_atr > 0 else None,
            'closes_above': int((side > 0).sum()), 'closes_below': int((side < 0).sum()),
            'closes_equal': int((side == 0).sum()),
            'last_close_side': 'above' if side[-1] > 0 else 'below' if side[-1] < 0 else 'equal',
            'consecutive_closes_on_last_side': count, 'consecutive_count_capped': count == window,
            'last_cross_in_window': last_cross,
        })
    return block(results, sources=sources, asof=end, coverage=coverage)


def anchored_vwap(frame, minutes, reference, time_fibs):
    sources = [f'DOTUSD.ohlc.{minutes}']
    frame = last_complete_frame(frame, minutes, reference)
    if time_fibs.get('status') != 'ok':
        return block(reason='confirmed_anchor_set_unavailable', sources=sources)
    rows = []
    for anchor in time_fibs['anchors']:
        start = utc(anchor['time_utc'])
        coverage = {'anchor_id': anchor['id'], 'anchor_set_id': time_fibs['anchor_set_id'],
                    'anchor_time_utc': iso(start), 'anchor_time_semantics': 'pivot_candle_open',
                    'confirmed_at_utc': anchor['confirmed_at_utc'], 'interval_minutes': minutes,
                    'closed_candles_only': True, 'price_method': 'exchange_candle_vwap'}
        if (anchor.get('market_type') != 'spot' or anchor.get('confirmed') is not True
                or anchor.get('candle_state') != 'closed' or utc(anchor['confirmed_at_utc']) > utc(reference)):
            rows.append(block(reason='anchor_not_confirmed_spot', sources=sources, coverage=coverage))
            continue
        work = frame.loc[start:] if frame is not None else None
        if work is None or work.empty or work.index[0] != start or not regular(work, minutes):
            rows.append(block(reason='anchor_start_or_contiguous_history_missing', sources=sources, coverage=coverage))
            continue
        traded = work.volume > 0
        if (not np.isfinite(work.volume).all() or (work.volume < 0).any()
                or not np.isfinite(work.loc[traded, 'vwap']).all() or (work.loc[traded, 'vwap'] <= 0).any()):
            rows.append(block(reason='invalid_exchange_vwap_or_volume', sources=sources, coverage=coverage))
            continue
        volume = work.volume.cumsum()
        prices = (work.vwap * work.volume).where(traded, 0).cumsum() / volume.replace(0, np.nan)
        value = finite(prices.iloc[-1])
        end = work.index[-1] + pd.Timedelta(minutes=minutes)
        coverage.update(window_start_utc=iso(start), window_end_utc=iso(end), sample_count=len(work))
        data = None if value is None else {
            'vwap_usd': value, 'cumulative_volume_dot': float(volume.iloc[-1]),
            'slope_last_bar_usd': finite(prices.diff().iloc[-1]),
            'last_closed_price_usd': float(work.close.iloc[-1]),
            'distance_usd': float(work.close.iloc[-1]) - value,
            'distance_pct': (float(work.close.iloc[-1]) / value - 1) * 100,
        }
        rows.append(block(data, reason='zero_volume' if data is None else None,
                          sources=sources, asof=end, coverage=coverage))
    return block(rows, status='ok' if all(r['status'] == 'ok' for r in rows) else 'partial', sources=sources,
                 asof=reference, coverage={'anchor_set_id': time_fibs['anchor_set_id']})


def historical_context(frame, minutes, reference, baseline=720, minimum=168, movement=24):
    sources = [f'DOTUSD.ohlc.{minutes}']
    frame = last_complete_frame(frame, minutes, reference)
    if frame is None or not regular(frame, minutes):
        return block(reason='missing_or_gapped_closed_candles', sources=sources)
    close = frame.close
    returns = np.log(close / close.shift(1))
    path = close.diff().abs().rolling(movement, min_periods=movement).sum()
    series = {
        'atr14_pct': atr(frame, 14) / close * 100,
        'realized_volatility_pct': np.sqrt(returns.pow(2).rolling(movement, min_periods=movement).sum()) * 100,
        'movement_efficiency': (close - close.shift(movement)).abs() / path.replace(0, np.nan),
        'range_width_pct': (frame.high.rolling(movement).max() - frame.low.rolling(movement).min()) / close * 100,
    }
    result = {}
    for name, values in series.items():
        current = finite(values.iloc[-1])
        population = values.iloc[:-1].tail(baseline).dropna()
        valid = current is not None and len(population) >= minimum
        result[name] = {
            'current_value': current, 'historical_percentile': float(100 * ((population < current).sum() + .5 * (population == current).sum()) / len(population)) if valid else None,
            'sample_count': len(population), 'requested_baseline_bars': baseline, 'minimum_samples': minimum,
            'baseline_complete': len(population) == baseline, 'status': 'ok' if valid else 'unavailable',
            'baseline_start_utc': iso(population.index[0] + pd.Timedelta(minutes=minutes)) if len(population) else None,
            'baseline_end_utc': iso(population.index[-1] + pd.Timedelta(minutes=minutes)) if len(population) else None,
        }
    end = frame.index[-1] + pd.Timedelta(minutes=minutes)
    return block(result, status='ok' if all(v['status'] == 'ok' for v in result.values()) else 'partial',
                 sources=sources, asof=end, coverage={'interval_minutes': minutes, 'movement_window_bars': movement,
                 'percentile_method': '100 * (count_less + 0.5 * count_equal) / sample_count',
                 'current_observation_excluded_from_baseline': True, 'closed_candles_only': True})


def market_relative(dot, btc, reference, horizons=(1, 4, 24), estimation=168):
    sources = ['DOTUSD.ohlc.60', 'BTCUSD.ohlc.60']
    dot, btc = (last_complete_frame(x, 60, reference) for x in (dot, btc))
    if dot is None or btc is None:
        return block(reason='matching_closed_hour_missing', sources=sources)
    frame = pd.concat({'dot': dot.close, 'btc': btc.close}, axis=1).sort_index()
    returns = np.log(frame / frame.shift(1))
    rows = {}
    end = frame.index[-1] + pd.Timedelta(hours=1)
    for hours in horizons:
        needed = frame.tail(estimation + hours + 1)
        good = len(needed) == estimation + hours + 1 and regular(needed, 60) and needed.notna().all().all()
        fit = returns.iloc[-hours-estimation:-hours]
        measured = returns.tail(hours)
        variance = float(fit.btc.var(ddof=1)) if good else 0
        value = None
        if good and variance > 0:
            beta = float(fit['dot'].cov(fit.btc) / variance)
            dot_return, btc_return = measured.sum()
            value = {'beta': beta, 'dot_log_return_pct': float(dot_return * 100),
                     'btc_log_return_pct': float(btc_return * 100),
                     'beta_adjusted_log_return_pp': float((dot_return - beta * btc_return) * 100)}
        rows[f'{hours}h'] = block(value, reason=None if value else 'insufficient_aligned_history_or_variance',
            sources=sources, asof=end, coverage={'return_start_utc': iso(end - pd.Timedelta(hours=hours)),
            'return_end_utc': iso(end), 'estimation_end_utc': iso(end - pd.Timedelta(hours=hours)),
            'estimation_start_utc': iso(end - pd.Timedelta(hours=hours + estimation)),
            'estimation_observations': estimation if good else 0, 'requested_estimation_observations': estimation,
            'estimation_excludes_measured_interval': True})
    return block(rows, status='ok' if all(r['status'] == 'ok' for r in rows.values()) else 'partial',
                 sources=sources, asof=end)
