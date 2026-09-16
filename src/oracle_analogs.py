"""Fixed-scale, missing-aware nearest neighbours; no outcome-fitted normalisation."""
import math
import pandas as pd
from oracle_common import compatible, value, number
from oracle_evaluator import forward_outcome
from oracle_scorecard import distribution
from observation_common import utc


def market_analogs(current, history, frames, cfg):
    result={}
    ref=utc(current['reference_at_utc'])
    for hours in (1,4,12):
        candidates=[]
        for past in history:
            ts=utc(past['reference_at_utc'])
            if not compatible(current,past) or ts.floor('h')>=ref.floor('h'): continue
            # Labels must have matured and been observable BEFORE the current forecast.
            if ts.ceil('min')+pd.Timedelta(hours=hours)>ref: continue
            a,b=current['features'],past['features']
            # Match the sign of extension when both known, without subjective regimes.
            ea,eb=value(a,'candle.1h.ema20_distance_atr'),value(b,'candle.1h.ema20_distance_atr')
            if ea is not None and eb is not None and ea*eb<0: continue
            shared=[k for k in cfg['analog_feature_scales'] if number(value(a,k)) and number(value(b,k))]
            if len(shared)<cfg['analog_min_shared_features']: continue
            distance=math.sqrt(sum(((value(a,k)-value(b,k))/cfg['analog_feature_scales'][k])**2 for k in shared)/len(shared))
            # Missing coordinates cost distance; sparse neighbours cannot look artificially perfect.
            distance+=(len(cfg['analog_feature_scales'])-len(shared))/len(cfg['analog_feature_scales'])
            if distance>cfg['analog_max_distance']: continue
            out=forward_outcome(ts,hours,frames,ref)
            if out['status']=='ok': candidates.append((distance,past['reference_at_utc'],out))
        # Non-overlapping outcomes avoid counting one move as many independent analogs.
        chosen=[]
        for row in sorted(candidates,key=lambda x:(x[0],x[1])):
            if all(abs((utc(row[1])-utc(c[1])).total_seconds())>=hours*3600 for c in chosen): chosen.append(row)
            if len(chosen)>=cfg['analog_k']: break
        sufficient=len(chosen)>=cfg['analog_min_samples']
        result[f'{hours}h']={'methodology':'fixed-scale-neighbours-v1',
            'calibration_status':'descriptive_only' if sufficient else 'insufficient_samples',
            'sample_count':len(chosen),'minimum_samples':cfg['analog_min_samples'],'eligible_count':len(candidates),
            'neighbour_ids':[r[1] for r in chosen],'distance':distribution([r[0] for r in chosen]),
            'forward_return_pct':distribution([r[2]['forward_return_pct'] for r in chosen]) if sufficient else None,
            'positive_return_rate':sum(r[2]['forward_return_pct']>0 for r in chosen)/len(chosen) if sufficient else None,
            'mfe_pct':distribution([r[2]['mfe_pct'] for r in chosen]) if sufficient else None,
            'mae_pct':distribution([r[2]['mae_pct'] for r in chosen]) if sufficient else None}
    return result
