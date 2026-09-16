"""Oracle tests exercise causal boundaries, immutable publication and outcome ordering."""
import copy
import gzip
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
import pytest
from oracle_common import configuration,digest,validate,value
from oracle_features import feature_inputs,build_features,effort,evidence
from oracle_history import FeatureArchive
from oracle_forecasts import forecast_id,validate_forecast,persist_forecast,create_only
from oracle_evaluator import evaluate_forecast,forward_outcome
from oracle_scorecard import scorecard
from oracle_analogs import market_analogs
from oracle_context import build_context,attach_oracle
from output import compact_snapshot,validate_snapshot
from common import write_json
from timeframes import encode_candles
from observation_common import iso,utc

ROOT=Path(__file__).parents[1]


@pytest.fixture
def actual():
    return json.loads((ROOT/'tests/fixtures/oracle_snapshot.json.gz').read_bytes() and gzip.decompress((ROOT/'tests/fixtures/oracle_snapshot.json.gz').read_bytes()))


@pytest.fixture
def cfg(): return configuration()[0]


def bars(start='2026-09-16T19:00:00Z',count=720,price=100):
    index=pd.date_range(start,periods=count,freq='min')
    return pd.DataFrame({'open':float(price),'high':float(price)+.2,'low':float(price)-.2,
        'close':float(price),'vwap':float(price),'volume':10.,'trade_count':2.},index=index)


def fixture_forecast(snapshot=None,direction='LONG'):
    created='2026-09-16T19:00:00Z'
    sha=digest(snapshot) if snapshot else 'a'*64
    cfg,hash_=configuration()
    f={'schema_version':1,'strategy_version':cfg['strategy_version'],
        'forecast_id':forecast_id(created,sha),'created_at_utc':created,
        'snapshot_generated_at_utc':snapshot['meta']['generated_at_utc'] if snapshot else created,
        'snapshot_sha256':sha,'market':'DOTUSD','primary_market':'SPOT',
        'measurement_config_sha256':snapshot['markets']['DOTUSD']['observations']['config_sha256'] if snapshot else 'b'*64,
        'oracle_feature_version':'3.0.0','oracle_config_sha256':hash_,
        'macro_bias':'BEARISH','swing_bias':'BEARISH','intraday_bias':'BULLISH','execution_bias':'NEUTRAL',
        'regime':'EXHAUSTION_WATCH','forecast_horizons':{h:{'direction':'UP','rationale':'Fixture'} for h in ('1h','4h','12h')},
        'trade_setup':{'direction':direction,'status':'ARMED','failure_scope':'after_trigger',
            'trigger':{'kind':'touch_above','price_usd':100.,'interval_minutes':1,'description':'Fixture touch'},
            'failure':{'kind':'touch_below','price_usd':98.,'interval_minutes':1,'description':'Fixture failure'},
            'targets':[{'id':'T1','price_usd':102.},{'id':'T2','price_usd':104.},{'id':'T3','price_usd':106.}],
            'target_roles':{'T1':'reaction','T2':'mean reversion / reversal zone','T3':'structural decision'},
            'asymmetry':{'assessment':'FAVOURABLE','reward_risk_t1':1.,'reason':'Fixture only'}},
        'paths':{k:{'when':'Fixture trigger','then':'Fixture outcome','why':'Fixture inputs'} for k in ('primary','alternative','squeeze_failure')},
        'evidence':{'supporting_feature_ids':[],'opposing_feature_ids':[],'decisive_timeframes':['1h']},
        'calibration_context':{'status':'uncalibrated','confidence':'LOW','analog_sample_count':0,'scorecard_sample_count':0,'limitations':['Synthetic fixture, not a published forecast']},
        'text_summary':'Synthetic fixture: macro bearish, tactical long conditional.'}
    if direction=='NONE':
        f['regime']='NO_TRADE';f['trade_setup'].update(status='NO_TRADE',trigger=None,failure=None,targets=[])
    if direction=='SHORT':
        f['trade_setup']['trigger']['kind']='touch_below';f['trade_setup']['failure'].update(kind='touch_above',price_usd=102.)
        f['trade_setup']['targets']=[{'id':f'T{i}','price_usd':100.-2*i} for i in (1,2,3)]
    return f


def test_feature_formulas_and_raw_flow_semantics(actual,cfg):
    inp=feature_inputs(actual);r=build_features(inp,cfg=cfg);f=r['features']
    c=inp['timeframes']['1h']['last_closed'];a=c['indicators']['atr14']
    assert value(f,'candle.1h.clv')==pytest.approx((c['close']-c['low'])/(c['high']-c['low']))
    assert value(f,'candle.1h.lower_wick_atr')==pytest.approx((min(c['open'],c['close'])-c['low'])/a)
    w=inp['observations']['components']['flow_windows']['data']['spot']['current']['data']
    fraction=w['signed_volume_dot']/(w['buy_volume_dot']+w['sell_volume_dot'])
    assert value(f,'flow.spot.fraction')==pytest.approx(fraction)
    assert value(f,'flow.spot.impact_bps')==pytest.approx(w['first_to_last_price_change_pct']*100/fraction)
    validate(r,'oracle_features.schema.json')


def test_near_zero_flow_and_zero_range_are_unavailable(actual,cfg):
    w={'data':{'signed_volume_dot':1e-15,'buy_volume_dot':50.,'sell_volume_dot':50.,'first_to_last_price_change_pct':.1}}
    assert effort(w,cfg)['impact_bps'] is None
    inp=feature_inputs(actual);c=inp['timeframes']['1h']['last_closed']
    c.update(open=1.,high=1.,low=1.,close=1.);c['indicators']['atr14']=0
    f=build_features(inp,cfg=cfg)['features']
    assert value(f,'candle.1h.clv') is None
    assert value(f,'candle.1h.body_atr') is None


@pytest.mark.parametrize('source,prefix',[('DOTUSD.ohlc.60','candle.1h.'),('DOTUSD.trades','flow.spot.'),('DOTPERP.ticker','oi.')])
def test_stale_source_cannot_produce_features(actual,cfg,source,prefix):
    inp=feature_inputs(actual);inp['sources'][source]['fresh']=False
    f=build_features(inp,cfg=cfg)['features']
    assert all(x['value'] is None for k,x in f.items() if k.startswith(prefix))


def test_future_source_receipt_is_not_available(actual,cfg):
    inp=feature_inputs(actual);inp['sources']['DOTUSD.trades']['received_at_utc']='2099-01-01T00:00:00Z'
    assert value(build_features(inp,cfg=cfg)['features'],'flow.spot.signed_dot') is None


def test_open_at_receipt_and_future_pivot_do_not_leak(actual,cfg):
    inp=feature_inputs(actual);tf=inp['timeframes']['1h'];c=tf['last_closed']
    tf['calculation_at_utc']=iso(utc(c['asof_utc'])+pd.Timedelta(minutes=59))
    f=build_features(inp,cfg=cfg)['features']
    assert value(f,'candle.1h.clv') is None
    assert value(f,'structure.1h.transition') is None
    inp=feature_inputs(actual);before=build_features(inp,cfg=cfg)
    inp['timeframes']['1h']['last_closed']['structure']['fractal']['pivots'].append(
        {'kind':'low','time':'2099-01-01T00:00:00Z','price':999.,'confirmed_at':'2099-01-01T02:00:00Z','classification':'HL'})
    assert build_features(inp,cfg=cfg)['features']==before['features']


def test_no_history_or_future_history_is_used(actual,cfg):
    inp=feature_inputs(actual);now=build_features(inp,cfg=cfg)
    future=copy.deepcopy(now);future['reference_at_utc']='2099-01-01T00:00:00Z'
    assert build_features(inp,[future],cfg)==now
    other=copy.deepcopy(now);other['reference_at_utc']=iso(utc(now['reference_at_utc'])-pd.Timedelta(hours=1));other['oracle_config_sha256']='c'*64
    assert build_features(inp,[other],cfg)==now


def test_oversold_alone_never_arms_reversal(cfg):
    f={'candle.1h.ema20_distance_atr':{'status':'ok','value':-4},'rsi14':{'status':'ok','value':1}}
    g=evidence(f,cfg)['reversal_gates']['downside']
    assert g['extension_feature_ids'] and not g['abc_ready']
    f['flow.spot.efficiency_loss']={'status':'ok','value':True}
    f['flow.spot.fraction']={'status':'ok','value':-.8}
    assert not evidence(f,cfg)['reversal_gates']['downside']['abc_ready']
    f['candle.1h.clv']={'status':'ok','value':.9}
    assert evidence(f,cfg)['reversal_gates']['downside']['abc_ready']
    assert not evidence(f,cfg)['reversal_gates']['downside']['trigger_candidate']


def test_archive_replay_replacement_and_fingerprints(tmp_path,actual,cfg):
    inp=feature_inputs(actual);record=build_features(inp,cfg=cfg)
    path=tmp_path/'features.json.gz';a=FeatureArchive(path,record['reference_at_utc'])
    a.update(record,inp,cfg);a.update(build_features(inp,[record],cfg),inp,cfg)
    b=FeatureArchive(path,record['reference_at_utc'])
    assert len(b.records)==1 and b.replay()==[record]
    assert digest({**cfg,'extension_atr':3})!=digest(cfg)
    with pytest.raises(ValueError,match='Future'):FeatureArchive(path,'2000-01-01T00:00:00Z')
    bad=copy.deepcopy(inp);bad['reference_at_utc']='2099-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='fingerprint'):a.update(record,bad,cfg)


def test_forecast_schema_and_immutable_publication(tmp_path,actual):
    actual['markets']['DOTUSD']['oracle_context']=build_context(actual,ROOT/'data')
    s=compact_snapshot(actual);f=fixture_forecast(s)
    assert validate_forecast(f,s)
    path=persist_forecast(f,s,tmp_path,f['created_at_utc']);before=path.read_bytes()
    with pytest.raises(FileExistsError):persist_forecast(f,s,tmp_path,f['created_at_utc'])
    assert path.read_bytes()==before
    f['regime']='REVERSAL_ARMED'
    with pytest.raises(ValueError,match='A.B.C'):validate_forecast(f,s)
    f=fixture_forecast(s);f['trade_setup']['targets'][0]['price_usd']=90
    with pytest.raises(ValueError):validate_forecast(f,s)
    with pytest.raises(ValueError):persist_forecast(fixture_forecast(s),s,tmp_path,'2026-09-17T19:00:00Z')


@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_target_before_failure_and_directional_symmetry(direction):
    f=fixture_forecast(direction=direction);frame=bars()
    if direction=='LONG':
        frame.iloc[5,frame.columns.get_loc('high')]=102.5;frame.iloc[10,frame.columns.get_loc('low')]=97.5
    else:
        frame.iloc[5,frame.columns.get_loc('low')]=97.5;frame.iloc[10,frame.columns.get_loc('high')]=102.5
    out=evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert out['triggered'] and out['target_before_failure'] is True
    assert out['status']=='failure' and out['r_multiple']==pytest.approx(-1)
    assert out['time_to_t1_minutes']==5


def test_same_candle_target_failure_is_ambiguous():
    f=fixture_forecast();frame=bars();frame.iloc[4,frame.columns.get_loc('high')]=103.;frame.iloc[4,frame.columns.get_loc('low')]=97.
    out=evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert out['status']=='ambiguous' and out['r_multiple'] is None
    assert out['targets'][0]['before_failure'] is None


def test_trigger_and_target_intrabar_order_is_unknown():
    frame=bars(price=99);frame.iloc[1,frame.columns.get_loc('high')]=103
    out=evaluate_forecast(fixture_forecast(),{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert out['status']=='ambiguous'


def test_close_trigger_does_not_use_pre_entry_barriers():
    frame=bars();frame.iloc[0,frame.columns.get_loc('high')]=103;frame.iloc[0,frame.columns.get_loc('low')]=97
    f=fixture_forecast();f['trade_setup']['trigger']['kind']='close_above'
    out=evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert out['status']=='triggered_unresolved' and out['targets'][0]['time'] is None


def test_coverage_closed_only_and_no_publication_candle_leak():
    frame=bars()
    assert forward_outcome('2026-09-16T19:00:00Z',1,{1:frame},'2026-09-16T19:59:59Z')['status']=='pending'
    assert forward_outcome('2026-09-16T19:00:00Z',1,{1:frame.drop(frame.index[30])},'2026-09-17T07:00:00Z')['status']=='partial'
    out=forward_outcome('2026-09-16T19:00:30Z',1,{1:frame},'2026-09-17T07:00:00Z')
    assert out['anchor_at_utc']=='2026-09-16T19:01:00Z' and out['anchor_delay_seconds']==30
    assert out['forward_return_pct']==0


def test_scorecard_separates_versions_ambiguity_abstentions():
    fs=[];outs=[]
    for i,version in enumerate(['v3-a','v3-b','v3-a']):
        f=fixture_forecast(direction='NONE' if i==2 else 'LONG');f['strategy_version']=version
        f['snapshot_sha256']=str(i)*64;f['forecast_id']=forecast_id(f['created_at_utc'],f['snapshot_sha256'])
        fs.append(f);outs.append(evaluate_forecast(f,{1:bars()},'2026-09-17T07:00:00Z'))
    s=scorecard(fs,outs,'2026-09-17T07:00:00Z',minimum=1)
    assert len(s['groups'])==9
    for g in s['groups']:
        assert g['forecast_count']==1
        if g['direction']=='NONE':
            assert g['target_before_failure_rates']['T1'] is None and g['no_trade_count']==1
    with pytest.raises(ValueError,match='Duplicate'):scorecard(fs,outs+[outs[0]],'2026-09-17T07:00:00Z')


def test_analog_versions_minimum_and_future_labels(actual,cfg):
    current=build_features(feature_inputs(actual),cfg=cfg)
    result=market_analogs(current,[current],{},cfg)
    assert all(r['sample_count']==0 and r['forward_return_pct'] is None for r in result.values())


def test_snapshot_backward_compatibility_and_oracle_reference(actual,tmp_path):
    old=compact_snapshot(actual);assert validate_snapshot(old)
    before=copy.deepcopy(actual['markets']['DOTUSD']['observations'])
    attach_oracle(actual,tmp_path)
    out=compact_snapshot(actual);assert validate_snapshot(out)
    assert actual['markets']['DOTUSD']['observations']==before
    oracle=out['markets']['DOTUSD'].pop('oracle_context');assert out==old
    out['markets']['DOTUSD']['oracle_context']=oracle
    oracle['reference_at_utc']='2099-01-01T00:00:00Z'
    with pytest.raises(ValueError):validate_snapshot(out)


def test_end_to_end_lifecycle(tmp_path,actual):
    # Snapshot -> features -> create-only forecast -> actual closed candle fixture -> outcome -> score -> context.
    attach_oracle(actual,tmp_path,persist=True)
    snapshot=compact_snapshot(actual);f=fixture_forecast(snapshot)
    persist_forecast(f,snapshot,tmp_path/'oracle',f['created_at_utc'])
    future=copy.deepcopy(actual);future['generated_at_utc']='2026-09-17T07:00:00Z'
    frame=bars();frame.iloc[5,frame.columns.get_loc('high')]=107.
    write_json(tmp_path/'raw/ohlc_cache.json.gz',{'DOTUSD.ohlc.1':encode_candles(frame)},compressed=True)
    future['sources']['DOTUSD.ohlc.1'].update(fresh=True,received_at_utc=future['generated_at_utc'])
    c=build_context(future,tmp_path,persist=True)
    assert len(c['model_scorecard']['groups'])==3
    assert all(g['triggered_count']==1 for g in c['model_scorecard']['groups'])
    paths=list((tmp_path/'oracle/outcomes').glob('*/*.json'));assert len(paths)==3
    old={p:p.read_bytes() for p in paths}
    assert build_context(future,tmp_path,persist=True)==c
    assert {p:p.read_bytes() for p in paths}==old
    from oracle_evaluator import verify_outcome
    forged=json.loads(paths[0].read_text());h=next(iter(forged['horizons']));forged['horizons'][h]['r_multiple']=999
    with pytest.raises(ValueError,match='recomputation'):verify_outcome(forged,f,tmp_path)


def test_exit_candle_extrema_are_not_claimed_as_pre_exit_excursions():
    f=fixture_forecast();frame=bars();frame.iloc[3,frame.columns.get_loc('low')]=90
    r=evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert r['status']=='failure' and r['mae_trade_pct'] is None
    assert r['excursion_status']=='partial_entry_or_exit_candle_order'
    assert r['mae_pct']==pytest.approx(-10)  # Market path remains measurable.


def test_setup_failure_cancels_before_later_trigger():
    f=fixture_forecast();f['trade_setup']['failure_scope']='setup_and_trade'
    frame=bars(price=99);frame.iloc[0,frame.columns.get_loc('low')]=97
    frame.iloc[10,frame.columns.get_loc('high')]=107
    r=evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert r['status']=='invalidated_before_trigger' and r['r_multiple'] is None and not r['triggered']


def test_config_failure_is_isolated(actual,tmp_path,monkeypatch):
    monkeypatch.setattr('oracle_context.configuration',lambda:(_ for _ in ()).throw(ValueError('bad configuration')))
    attach_oracle(actual,tmp_path,persist=True)
    assert actual['markets']['DOTUSD']['oracle_context']['status']=='error'
    assert validate_snapshot(compact_snapshot(actual))


def test_archive_guard_rejects_modification_and_deletion(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('guard',ROOT/'scripts/check_oracle_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def git(*args):return subprocess.check_output(['git',*args],cwd=tmp_path,text=True).strip()
    git('init','-q');git('config','user.email','test@example.invalid');git('config','user.name','Test')
    p=tmp_path/'data/oracle/outcomes/test/1h.json';p.parent.mkdir(parents=True);p.write_text('{}')
    git('add','.');git('commit','-qm','base');base=git('rev-parse','HEAD')
    p.write_text('{"modified":true}');git('add','.');git('commit','-qm','modify')
    with pytest.raises(ValueError,match='modified or removed'):module.check(base,repo=tmp_path)


def test_percentile_uses_only_prior_matching_hours(actual,cfg):
    cfg={**cfg,'percentile_min_samples':2}
    inp=feature_inputs(actual);current=build_features(inp,cfg=cfg)
    rows=[]
    for i in (1,2,3):
        r=copy.deepcopy(current);r['reference_at_utc']=iso(utc(current['reference_at_utc'])-pd.Timedelta(hours=i))
        r['features']['flow.spot.signed_dot']['value']=-100.*i;rows.append(r)
    future=copy.deepcopy(rows[0]);future['reference_at_utc']='2099-01-01T00:00:00Z'
    f=build_features(inp,rows+[future],cfg)['features']
    assert value(f,'flow.spot.signed_dot.percentile')==100
    assert f['flow.spot.signed_dot.percentile']['coverage']['sample_count']==3
    assert f['flow.spot.signed_dot.percentile']['coverage']['current_hour_excluded']


def test_analog_nonoverlap_missing_values_and_no_future_labels(actual,cfg):
    cfg={**cfg,'analog_min_samples':2,'analog_k':4}
    template=build_features(feature_inputs(actual),cfg=cfg)
    current=copy.deepcopy(template);current['reference_at_utc']='2026-09-17T07:00:00Z'
    history=[]
    for i in range(12):
        r=copy.deepcopy(template);r['reference_at_utc']=iso(utc('2026-09-16T19:00:00Z')+pd.Timedelta(hours=i));history.append(r)
    result=market_analogs(current,history,{1:bars()},cfg)
    assert result['1h']['sample_count']==4
    assert result['1h']['forward_return_pct']['mean']==0
    assert result['4h']['sample_count']<=3
    assert result['12h']['sample_count']==1 and result['12h']['positive_return_rate'] is None
    incompatible=copy.deepcopy(history);[r.update(oracle_config_sha256='f'*64) for r in incompatible]
    assert market_analogs(current,incompatible,{1:bars()},cfg)['1h']['sample_count']==0


def test_scorecard_rates_use_resolved_triggers_and_sample_threshold():
    fs=[];outs=[]
    for i in range(3):
        f=fixture_forecast();f['snapshot_sha256']=str(i)*64;f['forecast_id']=forecast_id(f['created_at_utc'],f['snapshot_sha256'])
        frame=bars();frame.iloc[2,frame.columns.get_loc('high')]=102.5
        if i==1:frame.iloc[2,frame.columns.get_loc('low')]=97
        if i==2:frame=bars(price=99)
        fs.append(f);outs.append(evaluate_forecast(f,{1:frame},'2026-09-17T07:00:00Z'))
    g=next(g for g in scorecard(fs,outs,'2026-09-17T07:00:00Z',1)['groups'] if g['horizon']=='1h')
    assert g['forecast_count']==3 and g['ambiguous_count']==1 and g['no_trigger_count']==1
    assert g['resolved_triggered_count']==1 and g['target_before_failure_rates']['T1']==1
    g=next(g for g in scorecard(fs,outs,'2026-09-17T07:00:00Z',2)['groups'] if g['horizon']=='1h')
    assert g['target_before_failure_rates']['T1'] is None


def test_forecast_and_outcome_schemas_reject_unknown_fields():
    f=fixture_forecast();f['success_probability']=.9
    with pytest.raises(Exception):validate_forecast(f)
    out=evaluate_forecast(fixture_forecast(),{1:bars()},'2026-09-17T07:00:00Z')
    validate(out,'oracle_outcome.schema.json')
    out['horizons']['1h']['model_grade']='success'
    with pytest.raises(Exception):validate(out,'oracle_outcome.schema.json')


def test_condition_close_requires_full_configured_interval():
    f=fixture_forecast();f['trade_setup']['trigger'].update(kind='close_above',interval_minutes=60)
    r=evaluate_forecast(f,{1:bars()},'2026-09-17T07:00:00Z')['horizons']['1h']
    assert r['triggered'] and r['trigger_time']['candle_close_utc']=='2026-09-16T20:00:00Z'
    assert r['targets'][0]['time'] is None and r['r_multiple']==0


def test_context_feature_values_are_typed(actual):
    r=build_features(feature_inputs(actual));r['features']['candle.1h.clv']['value']='bullish'
    with pytest.raises(Exception):validate(r,'oracle_features.schema.json')


def test_relative_future_or_unaligned_window_is_not_a_current_feature(actual):
    inp=feature_inputs(actual)
    assert value(build_features(inp)['features'],'relative.1h.ratio_return_pct') is not None
    inp['relative_coverage']['BTCUSD']['1h']['asof_utc']='2099-01-01T00:00:00Z'
    assert value(build_features(inp)['features'],'relative.1h.ratio_return_pct') is None


def test_new_confirmed_structure_is_a_response_family(cfg):
    f={k:{'status':'ok','value':v} for k,v in {'candle.1h.ema20_distance_atr':-3,
        'flow.spot.efficiency_loss':True,'flow.spot.fraction':-.8,'structure.1h.new_low':'HL'}.items()}
    g=evidence(f,cfg)['reversal_gates']['downside']
    assert g['abc_ready'] and g['trigger_candidate']
    assert 'structure.1h.new_low' in g['response_feature_ids']


def test_concurrent_publication_has_exactly_one_complete_forecast(tmp_path,actual):
    from concurrent.futures import ThreadPoolExecutor
    actual['markets']['DOTUSD']['oracle_context']=build_context(actual,ROOT/'data')
    snapshot=compact_snapshot(actual);f=fixture_forecast(snapshot)
    def publish():
        try:return persist_forecast(f,snapshot,tmp_path,f['created_at_utc'])
        except FileExistsError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:publish(),range(4)))
    assert sum(r is not None for r in results)==1
    path=next(r for r in results if r is not None)
    assert json.loads(path.read_text())==f


def test_matured_missing_coverage_is_counted_and_retryable(tmp_path,actual):
    attach_oracle(actual,tmp_path,persist=True)
    snapshot=compact_snapshot(actual);f=fixture_forecast(snapshot)
    persist_forecast(f,snapshot,tmp_path/'oracle',f['created_at_utc'])
    future=copy.deepcopy(actual);future['generated_at_utc']='2026-09-17T07:00:00Z'
    c=build_context(future,tmp_path,persist=True)
    assert len(c['model_scorecard']['groups'])==3
    assert all(g['unavailable_partial_count']==1 for g in c['model_scorecard']['groups'])
    assert not list((tmp_path/'oracle/outcomes').glob('*/*.json'))
    frame=bars();write_json(tmp_path/'raw/ohlc_cache.json.gz',{'DOTUSD.ohlc.1':encode_candles(frame)},compressed=True)
    future['sources']['DOTUSD.ohlc.1'].update(fresh=True,received_at_utc=future['generated_at_utc'])
    c=build_context(future,tmp_path,persist=True)
    assert all(g['unavailable_partial_count']==0 for g in c['model_scorecard']['groups'])
    assert len(list((tmp_path/'oracle/outcomes').glob('*/*.json')))==3
