"""Ex-post deterministic spot labels and barrier evaluation, never model grading.

A timestamp inside a minute anchors at the NEXT full minute. The skipped seconds
are explicit. No partial publication candle is used. A barrier time is a candle
interval, not a fabricated trade timestamp. No executable fill is claimed.
"""
import pandas as pd
from oracle_common import EVALUATOR_VERSION, digest
from oracle_forecasts import validate_forecast
from observation_common import utc, iso, regular
from timeframes import validate_candles, encode_candles


def covered_window(frames, start, end, now):
    """Prefer the smallest *fully covering* reliable closed-candle interval."""
    start, end, now = utc(start), utc(end), utc(now)
    if end > now: return None, None, 'pending'
    saw = False
    for minutes, frame in sorted(frames.items()):
        if frame is None or frame.empty: continue
        work = frame[(frame.index >= start) & (frame.index+pd.Timedelta(minutes=minutes) <= min(end, now))]
        saw |= not work.empty
        if work.empty: continue
        try: validate_candles(work)
        except ValueError: continue
        if (work.index[0] == start and work.index[-1]+pd.Timedelta(minutes=minutes) == end
                and regular(work, minutes) and len(work)*minutes*60 == (end-start).total_seconds()):
            return work, minutes, 'ok'
    return None, None, 'partial' if saw else 'unavailable'


def forward_outcome(reference, hours, frames, now):
    start = utc(reference).ceil('min')
    end = start+pd.Timedelta(hours=hours)
    work, minutes, status = covered_window(frames, start, end, now)
    out = {'status':status, 'anchor_at_utc':iso(start), 'end_at_utc':iso(end),
        'anchor_delay_seconds':(start-utc(reference)).total_seconds(), 'interval_minutes':minutes,
        'forward_return_pct':None, 'mfe_pct':None, 'mae_pct':None,
        'candle_sha256':None, 'source_id':None}
    if work is not None:
        p = float(work.iloc[0].open)
        out.update(forward_return_pct=(float(work.iloc[-1].close)/p-1)*100,
            mfe_pct=max(0.,(float(work.high.max())/p-1)*100),
            mae_pct=min(0.,(float(work.low.min())/p-1)*100),
            candle_sha256=digest(encode_candles(work)), source_id=f'DOTUSD.ohlc.{minutes}')
    return out


def touched(bar, condition):
    return bar.high >= condition['price_usd'] if condition['kind'].endswith('above') else bar.low <= condition['price_usd']


def event_time(stamp, minutes):
    return {'candle_open_utc':iso(stamp), 'candle_close_utc':iso(stamp+pd.Timedelta(minutes=minutes))}


def evaluate_horizon(f, hours, frames, now):
    result = forward_outcome(f['created_at_utc'], hours, frames, now)
    result.update(triggered=False, trigger_time=None, failure_time=None, targets=[],
        target_before_failure=None, entry_price_usd=None, mfe_trade_pct=None, mae_trade_pct=None,
        r_multiple=None, time_to_t1_minutes=None, timing_status='not_evaluated',
        excursion_status='unavailable')
    if result['status'] != 'ok': return result
    setup = f['trade_setup']
    if setup['direction']=='NONE':
        result.update(status='abstained', timing_status='abstained')
        return result
    start, end = utc(result['anchor_at_utc']), utc(result['end_at_utc'])
    work, minutes, _ = covered_window(frames,start,end,now)
    trigger, failure = setup['trigger'], setup['failure']
    targets = setup['targets']
    sign = 1 if setup['direction']=='LONG' else -1
    hits = {t['id']:{'id':t['id'],'time':None,'before_failure':False} for t in targets}
    entry = entry_stamp = None
    favourable, adverse = [], []
    excursion_complete = True
    final_status = 'no_trigger'
    exit_price = None
    t3_hit = False
    for stamp, bar in work.iterrows():
        just_triggered = False
        close_trigger = trigger['kind'].startswith('close_')
        if entry is None:
            if setup['failure_scope']=='setup_and_trade' and touched(bar,failure):
                possible_trigger = touched(bar,trigger)
                result['failure_time']=event_time(stamp,minutes)
                final_status='ambiguous' if possible_trigger and not close_trigger else 'invalidated_before_trigger'
                result['timing_status']='setup_failure_before_activation' if final_status!='ambiguous' else 'barrier_order_unknown_within_smallest_candle'
                break
            if close_trigger:
                required = trigger['interval_minutes']
                close_at = stamp+pd.Timedelta(minutes=minutes)
                # A close trigger needs a complete configured candle after publication.
                if close_at != close_at.floor(f'{required}min') or close_at-pd.Timedelta(minutes=required) < start:
                    continue
                sub, _, st = covered_window(frames,close_at-pd.Timedelta(minutes=required),close_at,now)
                if st != 'ok': continue
                condition = float(sub.iloc[-1].close) >= trigger['price_usd'] if sign>0 else float(sub.iloc[-1].close) <= trigger['price_usd']
                if not condition: continue
                entry = float(sub.iloc[-1].close)
                entry_stamp = close_at
            else:
                if not touched(bar,trigger): continue
                at_open = bar.open >= trigger['price_usd'] if sign>0 else bar.open <= trigger['price_usd']
                entry = float(bar.open) if at_open else trigger['price_usd']
                entry_stamp = stamp
                excursion_complete = bool(at_open)
            result.update(triggered=True, trigger_time=event_time(stamp,minutes), entry_price_usd=entry)
            if sign*(entry-failure['price_usd']) <= 0 or sign*(targets[0]['price_usd']-entry) <= 0:
                final_status = 'ambiguous'
                result['timing_status'] = 'entry_gap_invalidates_declared_asymmetry'
                break
            just_triggered = True
            final_status = 'triggered_unresolved'
            if close_trigger:
                # This candle's high/low happened before the known close entry.
                continue
        failure_hit = touched(bar,failure)
        touched_targets = [t for t in targets if hits[t['id']]['time'] is None and
            (bar.high>=t['price_usd'] if sign>0 else bar.low<=t['price_usd'])]
        ambiguous_entry = just_triggered and not close_trigger and not excursion_complete and (failure_hit or touched_targets)
        if ambiguous_entry or (failure_hit and touched_targets):
            final_status = 'ambiguous'
            result['timing_status'] = 'barrier_order_unknown_within_smallest_candle'
            for t in touched_targets:
                hits[t['id']].update(time=event_time(stamp,minutes),before_failure=None)
            if failure_hit: result['failure_time']=event_time(stamp,minutes)
            break
        if not just_triggered or excursion_complete:
            favourable.append((float(bar.high)/entry-1)*100 if sign>0 else (1-float(bar.low)/entry)*100)
            adverse.append((float(bar.low)/entry-1)*100 if sign>0 else (1-float(bar.high)/entry)*100)
        if failure_hit:
            excursion_complete=False  # Exit-candle extrema may occur after the stop.
            result['failure_time']=event_time(stamp,minutes)
            # Gaps through stops are marked at the observed open, never an ideal fill.
            exit_price = min(float(bar.open),failure['price_usd']) if sign>0 else max(float(bar.open),failure['price_usd'])
            final_status='failure'
            break
        for target in touched_targets:
            hits[target['id']].update(time=event_time(stamp,minutes),before_failure=True)
            if target['id']=='T1': result['time_to_t1_minutes']=(stamp-entry_stamp).total_seconds()/60
            if target['id']=='T3':
                exit_price=target['price_usd'];t3_hit=True
        if t3_hit:
            excursion_complete=False  # Full-target exit also has unknown intrabar extrema.
            final_status='targets_complete';break
    result['targets']=list(hits.values())
    result['status']=final_status
    result['target_before_failure']=None if final_status=='ambiguous' else hits['T1']['before_failure'] if entry is not None else None
    if result['timing_status']=='not_evaluated':
        result['timing_status']='candle_interval_only' if entry is not None else 'no_trigger_within_horizon'
    if entry is not None and final_status!='ambiguous':
        risk=abs(entry-failure['price_usd'])
        exit_price=float(work.iloc[-1].close) if exit_price is None else exit_price
        result['r_multiple']=sign*(exit_price-entry)/risk
        if excursion_complete and favourable:
            result.update(mfe_trade_pct=max(0.,max(favourable)),mae_trade_pct=min(0.,min(adverse)),excursion_status='ok')
        else: result['excursion_status']='partial_entry_or_exit_candle_order'
    return result


def evaluate_forecast(f, frames, now):
    validate_forecast(f)
    return {'schema_version':1, 'evaluator_version':EVALUATOR_VERSION,'forecast_id':f['forecast_id'],
        'forecast_sha256':digest(f),'strategy_version':f['strategy_version'],
        'forecast_schema_version':f['schema_version'],'oracle_feature_version':f['oracle_feature_version'],
        'oracle_config_sha256':f['oracle_config_sha256'],'measurement_config_sha256':f['measurement_config_sha256'],
        'regime':f['regime'],'direction':f['trade_setup']['direction'], 'evaluated_at_utc':iso(now),
        'horizons':{f'{h}h':evaluate_horizon(f,h,frames,now) for h in (1,4,12)}}


def verify_outcome(out, forecast, directory):
    """Recompute archived grades from bound candles before accepting them as feedback."""
    import json
    from pathlib import Path
    from oracle_common import validate
    from timeframes import decode_candles
    validate(out,'oracle_outcome.schema.json')
    if out['forecast_sha256'] != digest(forecast): raise ValueError('Outcome forecast fingerprint mismatch')
    for horizon, result in out['horizons'].items():
        if result['status'] in ('partial','unavailable','pending'):
            raise ValueError('Only fully covered outcomes may be finalised')
        path=Path(directory)/'oracle/outcome_inputs'/(result['candle_sha256']+'.json')
        evidence=json.loads(path.read_text())
        if digest(evidence['candles'])!=result['candle_sha256']:
            raise ValueError('Outcome candle fingerprint mismatch')
        if evidence['source_id']!=result['source_id'] or evidence['interval_minutes']!=result['interval_minutes']:
            raise ValueError('Outcome candle lineage mismatch')
        frames={evidence['interval_minutes']:decode_candles(evidence['candles'])}
        expected=evaluate_forecast(forecast,frames,out['evaluated_at_utc'])
        expected['horizons']={horizon:expected['horizons'][horizon]}
        single={**out,'horizons':{horizon:result}}
        if expected!=single: raise ValueError('Archived outcome differs from deterministic recomputation')
    return True
