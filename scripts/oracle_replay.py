#!/usr/bin/env python3
"""Walk actual git snapshots once in time order; never populate the live archive.

The frozen current feature method is applied to original inputs, NOT claimed to
have run historically. Later cached candles are used solely as outcome labels.
"""
import argparse
import collections
import json
from pathlib import Path
import subprocess
import tempfile
import hashlib
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_common import configuration, digest, canonical, validate
from oracle_analogs import market_analogs
from oracle_scorecard import scorecard
from output import compact_snapshot,validate_snapshot
from oracle_features import feature_inputs,build_features
from oracle_context import outcome_frames
from oracle_evaluator import forward_outcome
from observation_common import utc
from common import write_json


def historical_snapshots(repo,source_ref):
    shas=subprocess.check_output(['git','log','--format=%H','--reverse',source_ref,'--','data/latest.json'],cwd=repo,text=True).splitlines()
    unique={}
    for sha in shas:
        raw=subprocess.check_output(['git','show',f'{sha}:data/latest.json'],cwd=repo)
        d=json.loads(raw)
        if d.get('schema_version')!=2: continue
        ref=d['generated_at_utc']
        unique[ref]=(sha,d)
    # Production's current-hour replacement convention, based on true snapshot time.
    hourly={}
    for ref,item in sorted(unique.items(),key=lambda x:utc(x[0])): hourly[utc(ref).floor('h')]=item
    return [hourly[k] for k in sorted(hourly)]


def replay(repo,output,export_snapshots=None,source_ref='origin/main'):
    cfg,_=configuration()
    snapshots=historical_snapshots(repo,source_ref)
    latest=snapshots[-1][1]
    cache_bytes=subprocess.check_output(['git','show',snapshots[-1][0]+':data/raw/ohlc_cache.json.gz'],cwd=repo)
    with tempfile.TemporaryDirectory(prefix='oracle-labels-') as temp:
        raw=Path(temp)/'raw';raw.mkdir()
        (raw/'ohlc_cache.json.gz').write_bytes(cache_bytes)
        frames=outcome_frames(latest,temp)
    cutoff=latest['generated_at_utc']
    rows=[];history=[];availability=collections.Counter();counts=collections.Counter();max_size=0
    for sha,data in snapshots:
        inp=feature_inputs(data)
        record=build_features(inp,history,cfg)
        # Same input and same prior history must be bit-identical.
        assert record==build_features(inp,history,cfg)
        if export_snapshots is not None:
            import copy
            exported=copy.deepcopy(data)
            exported['markets']['DOTUSD']['oracle_context']={
                'schema_version':1,'feature_version':cfg['feature_version'],'strategy_version':cfg['strategy_version'],
                'oracle_config_sha256':digest(cfg),'reference_at_utc':record['reference_at_utc'],
                'status':record['status'],'reason':None,'current_features':record,
                'market_analogs':market_analogs(record,history,frames,cfg),
                'model_scorecard':{**scorecard([],[],record['reference_at_utc']),'pending_or_unavailable_count':0},
                'recent_forecasts':[],'recent_matured_outcomes':[]}
            compact=compact_snapshot(exported)
            validate_snapshot(compact)
            export_snapshots.mkdir(parents=True,exist_ok=True)
            write_json(export_snapshots/(record['reference_at_utc'].replace(':','')+'.json'),compact)
        history.append(record)
        for key,f in record['features'].items():
            if f['status']=='ok': availability[key]+=1
        gates=record['evidence']['reversal_gates']
        for side,g in gates.items():
            for stage in ('extension_feature_ids','efficiency_loss_feature_ids','abc_ready','trigger_candidate'):
                if g[stage]: counts[side+'.'+stage]+=1
        labels={f'{h}h':forward_outcome(record['reference_at_utc'],h,frames,cutoff) for h in (1,4,12)}
        max_size=max(max_size,len(canonical(record).encode()))
        candle=data['markets']['DOTUSD']['timeframes']['1h'].get('last_closed') or {}
        rows.append({'git_commit':sha,'record':record,'labels':labels,
                     'closed_1h':{k:candle.get(k) for k in ('asof_utc','open','high','low','close')},
                     'rsi14':(candle.get('indicators') or {}).get('rsi14')})
    # Forensic case selection only; this literal never enters production calculations.
    washout=[r for r in rows if r['closed_1h']['low'] is not None and abs(r['closed_1h']['low']-.9342)<.00001]
    case=[]
    if washout:
        center=utc(washout[0]['record']['reference_at_utc'])
        case=[r for r in rows if abs((utc(r['record']['reference_at_utc'])-center).total_seconds())<=12*3600]
    label_counts={h:dict(collections.Counter(r['labels'][h]['status'] for r in rows)) for h in ('1h','4h','12h')}
    warning_checks={}
    for side in ('downside','upside'):
        armed=[r for r in rows if r['record']['evidence']['reversal_gates'][side]['abc_ready']]
        known=[r for r in armed if r['labels']['4h']['status']=='ok']
        warning_checks[side]={'abc_count':len(armed),'matured_4h_count':len(known),
            'adverse_4h_return_count':sum(r['labels']['4h']['forward_return_pct']*(1 if side=='downside' else -1)<0 for r in known),
            'interpretation':'diagnostic only; no threshold fitting, no LLM forecast, no execution backtest'}
    report={'source_ref_sha':subprocess.check_output(['git','rev-parse',source_ref],cwd=repo,text=True).strip(),
        'label_cache_sha256':hashlib.sha256(cache_bytes).hexdigest(),'methodology':'git-snapshot-walk-forward-v1','feature_version':cfg['feature_version'],
        'config_sha256':digest(cfg),'actual_snapshot_count':len(rows),
        'first_reference':rows[0]['record']['reference_at_utc'],'last_reference':rows[-1]['record']['reference_at_utc'],
        'availability_count':dict(availability),'evidence_gate_counts':dict(counts),'outcome_coverage':label_counts,
        'warning_checks':warning_checks,'maximum_feature_record_bytes':max_size,'washout_snapshot_count':len(washout),
        'forensic_case':[{'git_commit':r['git_commit'],'reference_at_utc':r['record']['reference_at_utc'],
            'closed_1h':r['closed_1h'],'rsi14':r['rsi14'],
            'selected_features':{k:r['record']['features'][k] for k in (
                'oi.1h.change_pct','oi.4h.change_pct','flow.spot.signed_dot','flow.perp.signed_dot',
                'flow.spot.efficiency_loss','flow.perp.efficiency_loss','flow.spot.divergence','flow.perp.divergence',
                'candle.1h.ema20_distance_atr','candle.4h.ema20_distance_atr','candle.1h.clv',
                'structure.1h.transition','structure.1h.new_low')},
            'reversal_gates':r['record']['evidence']['reversal_gates'],'labels':r['labels']} for r in case],'threshold_tuning':False,'holdout_status':'No tuning or claimed validated profitability; full sample diagnostic replay',
        'model_prompt_backtest':'not_run_no_historical_model_invocations','synthetic_backfill':False,
        'limitations':['Candle-cache labels may lack earlier minute coverage; unavailable labels stay missing.',
            'Original snapshots before observations rollout do not contain Oracle flow-window inputs.',
            'Features are recomputed by current methodology, not historical published forecasts.']}
    output.mkdir(parents=True,exist_ok=True)
    write_json(output/'replay_summary.json',report)
    write_json(output/'replay_records.json.gz',{'report_type':'retrospective_features_from_actual_snapshots','rows':rows},compressed=True)
    print(json.dumps({k:v for k,v in report.items() if k not in ('availability_count','forensic_case')},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path('.'))
    p.add_argument('--output',type=Path,default=Path('docs/evaluation/oracle-v3'))
    p.add_argument('--export-snapshots',type=Path)
    p.add_argument('--ref',default='origin/main',help='Immutable original source revision or branch; recorded in report')
    a=p.parse_args();replay(a.repo,a.output,a.export_snapshots,a.ref)
