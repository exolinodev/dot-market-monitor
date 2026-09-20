"""Observed forecast performance; strict version partitions and abstention separation."""
from collections import defaultdict
import numpy as np
from oracle_common import EVALUATOR_VERSION, digest, number
from observation_common import utc, iso
from oracle_forecasts import regime_direction


def distribution(values):
    a = [x for x in values if number(x)]
    return {'mean':float(np.mean(a)) if a else None,'median':float(np.median(a)) if a else None,
            'p10':float(np.percentile(a,10)) if a else None,'p90':float(np.percentile(a,90)) if a else None}


def scorecard(forecasts, outcomes, reference, minimum=30):
    fs = {f['forecast_id']:f for f in forecasts if utc(f['created_at_utc']) <= utc(reference)}
    groups = defaultdict(list)
    seen = set()
    for out in outcomes:
        f=fs.get(out['forecast_id'])
        if f is None or out['forecast_sha256']!=digest(f): raise ValueError('Outcome is not bound to an actual forecast')
        if utc(out['evaluated_at_utc'])>utc(reference): continue
        for h,r in out['horizons'].items():
            if r['status']=='pending' or utc(r['end_at_utc'])>utc(reference): continue
            identity=(out['forecast_id'],h)
            if identity in seen: raise ValueError('Duplicate forecast outcome horizon')
            seen.add(identity)
            key=(f['strategy_version'],f['schema_version'],f['oracle_feature_version'],f['oracle_config_sha256'],
                 f['measurement_config_sha256'],out['evaluator_version'],h,regime_direction(f)[1],regime_direction(f)[0])
            groups[key].append((f,r))
    rows=[]
    names=('strategy_version','forecast_schema_version','oracle_feature_version','oracle_config_sha256',
           'measurement_config_sha256','evaluator_version','horizon','direction','regime')
    for key,values in sorted(groups.items()):
        rs=[r for _,r in values]
        complete=[r for r in rs if r['status'] not in ('pending','partial','unavailable','ambiguous','abstained')]
        triggered=[r for r in complete if r['triggered']]
        directional=[]
        abstentions=0
        for f,r in values:
            d=f['forecast_horizons'][key[6]]['direction']
            if d=='ABSTAIN': abstentions+=1; continue
            ret=r['forward_return_pct']
            if ret is not None and d!='FLAT': directional.append(ret>0 if d=='UP' else ret<0)
        target_rates={t:sum(next((x['before_failure'] is True for x in r['targets'] if x['id']==t),False) for r in triggered)/len(triggered) if len(triggered)>=minimum else None for t in ('T1','T2','T3')}
        rows.append({**dict(zip(names,key)), 'forecast_count':len(rs),'triggered_count':sum(r['triggered'] for r in rs),
            'invalidated_before_trigger_count':sum(r['status']=='invalidated_before_trigger' for r in rs),
            'no_trigger_count':sum(r['status']=='no_trigger' for r in rs),'ambiguous_count':sum(r['status']=='ambiguous' for r in rs),
            'unavailable_partial_count':sum(r['status'] in ('partial','unavailable') for r in rs),
            'no_trade_count':sum(r['status']=='abstained' for r in rs),'directional_abstention_count':abstentions,
            'resolved_triggered_count':len(triggered),'target_before_failure_rates':target_rates,
            'mfe_pct':distribution([r['mfe_trade_pct'] for r in triggered]),'mae_pct':distribution([r['mae_trade_pct'] for r in triggered]),
            'r_multiple':distribution([r['r_multiple'] for r in triggered]),
            'directional_sample_count':len(directional),'directional_correct_count':sum(directional),
            'directional_accuracy':sum(directional)/len(directional) if len(directional)>=minimum else None,
            'calibration_status':'descriptive_only' if len(triggered)>=minimum else 'insufficient_samples',
            'minimum_samples':minimum})
    return {'schema_version':1,'methodology':'version-partitioned-scorecard-v1','reference_at_utc':iso(reference),
        'probabilistic_calibration':'disabled_no_probabilistic_forecasts','groups':rows}
