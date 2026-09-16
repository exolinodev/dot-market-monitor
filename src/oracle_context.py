"""Fault-isolated deterministic Oracle lifecycle; no LLM dependency in collection."""
import gzip
import json
from pathlib import Path
from common import read_json, write_json
from oracle_common import configuration, FEATURE_VERSION, STRATEGY_VERSION, EVALUATOR_VERSION, canonical, validate, digest
from oracle_features import feature_inputs, build_features
from oracle_history import FeatureArchive
from oracle_forecasts import validate_forecast, create_only
from oracle_evaluator import evaluate_forecast, verify_outcome
from oracle_scorecard import scorecard
from oracle_analogs import market_analogs
from oracle_market_history import market_outcome_history
from observation_common import utc, iso
from timeframes import decode_candles


def outcome_frames(data, directory):
    """Only candles closed by their actual source receipt cutoff are reliable."""
    cache=read_json(Path(directory)/'raw/ohlc_cache.json.gz',{})
    frames={}
    ref=utc(data['generated_at_utc'])
    for minutes in (1,5,15,30,60,240):
        source=f'DOTUSD.ohlc.{minutes}'
        meta=data['sources'].get(source,{})
        if source not in cache or not meta.get('fresh') or not meta.get('received_at_utc'): continue
        if utc(meta['received_at_utc']) > ref: continue
        cutoff=min(ref,utc(meta['received_at_utc']))
        frame=decode_candles(cache[source])
        import pandas as pd
        frames[minutes]=frame[frame.index+pd.Timedelta(minutes=minutes)<=cutoff]
    return frames


def load_forecasts(directory, reference):
    fs=[]
    for path in sorted((Path(directory)/'oracle/forecasts').glob('*/*/*/*.json')):
        f=json.loads(path.read_text())
        validate_forecast(f)
        expected=utc(f['created_at_utc']).strftime('%Y/%m/%d')+'/'+f['forecast_id']+'.json'
        if path.relative_to(Path(directory)/'oracle/forecasts').as_posix()!=expected:
            raise ValueError('Forecast path does not match creation date and ID')
        if utc(f['created_at_utc'])>utc(reference): continue
        evidence=Path(directory)/'oracle/inputs'/(f['snapshot_sha256']+'.json.gz')
        snapshot=json.loads(gzip.decompress(evidence.read_bytes()))
        validate_forecast(f,snapshot)
        fs.append(f)
    if len({f['forecast_id'] for f in fs})!=len(fs): raise ValueError('Duplicate forecast ID')
    return fs


def build_context(data, directory, persist=False, cfg=None):
    cfg=cfg or configuration()[0]
    ref=data['generated_at_utc']
    root=Path(directory)
    archive=FeatureArchive(root/'raw/oracle_feature_history.json.gz',ref)
    past=[r['record'] for r in archive.records]
    inputs=feature_inputs(data)
    current=build_features(inputs,past,cfg)
    validate(current,'oracle_features.schema.json')
    frames=outcome_frames(data,root)
    labels=market_outcome_history(root/'raw/oracle_market_outcomes.json.gz',
        past+[current],frames,ref,cfg['history_days'],persist=False)
    fs=load_forecasts(root,ref)
    outcomes=[]
    pending=[]
    for f in fs:
        evaluated=None
        for h in ('1h','4h','12h'):
            path=root/'oracle/outcomes'/f['forecast_id']/(EVALUATOR_VERSION+'-'+h+'.json')
            if path.exists():
                out=json.loads(path.read_text())
                verify_outcome(out,f,root)
                if out['forecast_sha256']!=digest(f): raise ValueError('Immutable forecast was modified')
                if utc(out['evaluated_at_utc'])<=utc(ref): outcomes.append(out)
                continue
            if evaluated is None: evaluated=evaluate_forecast(f,frames,ref)
            result=evaluated['horizons'][h]
            if result['status'] in ('pending','partial','unavailable'):
                pending.append({'forecast_id':f['forecast_id'],'horizon':h,'status':result['status']})
                if result['status']!='pending':
                    out={**evaluated,'horizons':{h:result}}
                    validate(out,'oracle_outcome.schema.json')
                    outcomes.append(out)  # Count missing coverage, but retry rather than finalise.
                continue
            out={**evaluated,'horizons':{h:result}}
            validate(out,'oracle_outcome.schema.json')
            outcomes.append(out)
            if persist:
                # Store the exact canonical candle rows used for audit, separate from context.
                from oracle_evaluator import covered_window
                from timeframes import encode_candles
                work,minutes,_=covered_window(frames,result['anchor_at_utc'],result['end_at_utc'],ref)
                evidence_path=root/'oracle/outcome_inputs'/(result['candle_sha256']+'.json')
                payload={'source_id':result['source_id'],'interval_minutes':minutes,'candles':encode_candles(work)}
                if evidence_path.exists():
                    if json.loads(evidence_path.read_text())!=payload: raise ValueError('Outcome evidence mismatch')
                else: create_only(evidence_path,payload)
                create_only(path,out)
    scores=scorecard(fs,outcomes,ref,cfg['scorecard_min_samples'])
    validate(scores,'oracle_scorecard.schema.json')
    # Current method only in consumer context; full version-separated database is retained.
    relevant=[g for g in scores['groups'] if g['strategy_version']==cfg['strategy_version'] and
        g['oracle_feature_version']==FEATURE_VERSION and g['oracle_config_sha256']==digest(cfg) and
        g['measurement_config_sha256']==current['measurement_config_sha256']]
    relevant.sort(key=lambda g:(-g['forecast_count'],g['horizon'],g['direction'],g['regime']))
    omitted_groups=max(0,len(relevant)-cfg['context_scorecard_groups'])
    relevant=relevant[:cfg['context_scorecard_groups']]
    limit=min(cfg['recent_limit'],10)
    recent=sorted(fs,key=lambda f:f['created_at_utc'])[-limit:]
    matured=sorted(outcomes,key=lambda o:(o['evaluated_at_utc'],o['forecast_id']))[-limit:]
    context={'schema_version':1,'feature_version':FEATURE_VERSION,'strategy_version':cfg['strategy_version'],
        'oracle_config_sha256':digest(cfg),'reference_at_utc':iso(ref),'status':current['status'],'reason':None,
        'current_features':current,'market_analogs':market_analogs(current,past,frames,cfg,labels),
        'model_scorecard':{**scores,'groups':relevant, 'pending_or_unavailable_count':len(pending), 'omitted_compatible_groups':omitted_groups},
        'recent_forecasts':[{'forecast_id':f['forecast_id'],'created_at_utc':f['created_at_utc'],
            'strategy_version':f['strategy_version'],'regime':f['regime'],'direction':f['trade_setup']['direction']} for f in recent],
        'recent_matured_outcomes':[{'forecast_id':o['forecast_id'],'strategy_version':o['strategy_version'],
            'evaluated_at_utc':o['evaluated_at_utc'], 'horizons':{h:{k:r[k] for k in
                ('status','target_before_failure','forward_return_pct','r_multiple')} for h,r in o['horizons'].items()}} for o in matured]}
    validate(context,'oracle_context.schema.json')
    if len(canonical(context).encode())>cfg['max_context_bytes']: raise ValueError('Oracle context exceeds configured byte budget')
    if persist:
        archive.update(current,inputs,cfg)
        write_json(root/'raw/oracle_market_outcomes.json.gz',
            {'schema_version':1,'methodology':'retained-market-labels-v1',
             'records':sorted(labels.values(),key=lambda r:(r['identity']['reference_at_utc'],r['state_id']))},compressed=True)
        write_json(root/'oracle_scorecard.json',scores)
        write_json(root/'oracle/pending_outcomes.json',{'reference_at_utc':iso(ref),'items':pending})
    return context


def attach_oracle(data, directory, persist=False):
    try:
        context=build_context(data,directory,persist)
    except Exception as exc:
        # Oracle failures must not prevent the measurements-only collector from publishing.
        try:
            _,sha=configuration()
        except Exception:
            sha=None
        context={'schema_version':1,'feature_version':FEATURE_VERSION,'strategy_version':STRATEGY_VERSION,
            'oracle_config_sha256':sha,'reference_at_utc':iso(data['generated_at_utc']),
            'status':'error','reason':str(exc),'current_features':None,'market_analogs':{},
            'model_scorecard':{},'recent_forecasts':[],'recent_matured_outcomes':[]}
        data['errors'].append({'source_id':'oracle','error':str(exc)})
        data['status']='partial'
    data['markets']['DOTUSD']['oracle_context']=context
    return context
