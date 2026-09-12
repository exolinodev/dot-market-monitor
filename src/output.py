"""Compact consumer view and hard structural/numerical validation."""
import copy
import json
import math
from pathlib import Path
import jsonschema
from common import json_safe
from timeframes import DOT_TIMEFRAMES, DOTBTC_TIMEFRAMES, BTC_TIMEFRAMES

SCHEMA=Path(__file__).resolve().parents[1]/'schema'/'llm_snapshot.schema.json'
INDICATORS=['ema9','ema20','ema21','ema50','ema100','ema200','sma50','sma200','dema20','rsi14','stoch_rsi',
            'stoch_rsi_k','stoch_rsi_d','macd','macd_signal','macd_hist','atr14','atr_pct','bb_lower','bb_middle',
            'bb_upper','bb_bandwidth_pct','adx14','plus_di14','minus_di14','obv','mfi14','cmf20','volume_sma20',
            'volume_ratio20','volume_z20','session_vwap_utc']


CORE_INDICATORS=['rsi14','macd','macd_signal','macd_hist','ema20','ema50','ema200','atr14','atr_pct','volume_sma20','volume_ratio20','volume_z20']

def compact_snapshot(data):
    out=copy.deepcopy(data)
    out['meta']={key:out.pop(key) for key in ['schema_version','generated_at_utc','generated_at_unix',
                'collection_started_at_utc','run_duration_seconds','formula_version','status','history_points','raw_data','definitions']}
    out['meta']['consumer_max_age_seconds']=5400
    out['meta']['fresh']=True
    out['meta']['numeric_export_significant_digits']=12
    out['meta']['cross_event_columns']=['direction','bars_since']
    out['meta']['pivot_columns']=['kind','time_utc','price','confirmed_at_utc','classification','reversal_threshold']
    out['meta']['history_delta_columns']=['absolute','relative_pct']
    out['meta']['core_indicator_names']=CORE_INDICATORS
    for name,market in out['markets'].items():
        spot=market.get('spot')
        if spot: spot.pop('ticker',None)
        for item in market.get('timeframes',{}).values():
            closed=item.get('last_closed')
            item['structure']=closed['structure'] if closed else None
            for mode in ['live','last_closed']:
                snap=item.get(mode)
                if not snap: continue
                snap.pop('structure',None)
                # A null cross is represented by a null, not 3 repeated keys.
                snap['cross_events']={k:[v['direction'],v['bars_since']] if v['bars_since'] is not None else None for k,v in snap['cross_events'].items()}
                snap.pop('ema_state',None)
                if name!='DOTUSD':
                    snap['indicators']={k:v for k,v in snap['indicators'].items() if k in CORE_INDICATORS}
                    snap['slopes']={k:v for k,v in snap['slopes'].items() if k in CORE_INDICATORS}
                    snap['warmup_unavailable']=[k for k in snap['warmup_unavailable'] if k in CORE_INDICATORS]
                    snap['cross_events']={k:v for k,v in snap['cross_events'].items() if k in ['rsi14_30','rsi14_50','rsi14_70','macd_macd_signal','macd_0','macd_hist_0','ema20_ema50','ema50_ema200','price_ema20','price_ema50','price_ema200']}
            if item['structure']:
                for method in ['fractal','atr_zigzag']:
                    item['structure'][method]={k:v for k,v in item['structure'][method].items() if k in ['high_state','low_state','pivots']}
                    item['structure'][method]['pivots']=[[p['kind'],p['time'],p['price'],p['confirmed_at'],p['classification'],p.get('reversal_threshold')] for p in item['structure'][method]['pivots'][-8:]]
                item['structure']['divergences']={key:flag if flag['flag'] else {'flag':False,'status':flag['status']} for key,flag in item['structure']['divergences'].items()}
                flags=item['structure']['wave_rule_flags']
                flags.pop('anchors',None)
                flags['anchor_reference']='last five atr_zigzag pivots, hypothetical p0..p4'
        perp=market.get('perp')
        if perp and perp.get('ticker'):
            perp['ticker'].pop('raw',None)
            perp['ticker'].pop('observed_api_fields',None)
    if out.get('history_changes'):
        for block in out['history_changes'].values():
            block['status']='ok' if all(v['status']=='ok' for v in block['values'].values()) else 'partial'
            block['values']={k:[v['absolute'],v['relative_pct']] if v['absolute'] is not None else None for k,v in block['values'].items()}
    def round_values(value):
        if isinstance(value,dict): return {k:round_values(v) for k,v in value.items()}
        if isinstance(value,list): return [round_values(v) for v in value]
        if isinstance(value,str) and value.endswith('+00:00'): return value[:-6]+'Z'
        if isinstance(value,float): return float(f'{value:.12g}')
        return value
    return round_values(json_safe(out))


def validate_snapshot(data):
    schema=json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(data)
    def visit(value,path=''):
        if isinstance(value,dict):
            for key,item in value.items(): visit(item,path+'.'+key)
        elif isinstance(value,list):
            for i,item in enumerate(value): visit(item,f'{path}[{i}]')
        elif isinstance(value,float) and not math.isfinite(value):
            raise ValueError('Nonfinite value: '+path)
    visit(data)
    for name,required in [('DOTUSD',DOT_TIMEFRAMES),('DOTBTC',DOTBTC_TIMEFRAMES),('BTCUSD',BTC_TIMEFRAMES)]:
        if set(data['markets'][name]['timeframes'])!=set(required): raise ValueError(name+' timeframe inventory mismatch')
    for name,market in data['markets'].items():
        for tf,record in market.get('timeframes',{}).items():
            if any(s not in data['sources'] for s in record['source_ids']): raise ValueError('Unknown OHLC source reference')
            for mode in ['live','last_closed']:
                item=record.get(mode)
                if item:
                    expected=INDICATORS if name=='DOTUSD' else CORE_INDICATORS
                    if set(item['indicators'])!=set(expected): raise ValueError('Indicator schema mismatch')
                    for key in ['rsi14','stoch_rsi','stoch_rsi_k','stoch_rsi_d','adx14','plus_di14','minus_di14','mfi14']:
                        v=item['indicators'].get(key)
                        if v is not None and not -1e-8<=v<=100+1e-8: raise ValueError(f'{name}.{tf}.{mode}.{key} outside [0,100]')
                    if item['close']<=0 or item['volume']<0: raise ValueError('Implausible candle')
            live,closed=record.get('live'),record.get('last_closed')
            if live and closed and live['asof_utc']<=closed['asof_utc']: raise ValueError('Open/closed candle ordering')
    return True


def markdown_summary(data):
    lines=['# DOT Market Snapshot','',f"Generated UTC: {data['generated_at_utc']}",f"Status: {data['status']}",'',
           'Primary consumer: [llm_snapshot.json](llm_snapshot.json)','',
           '| Instrument | Verified price | Status |','|---|---:|---|']
    for name in ['DOTUSD','BTCUSD','DOTBTC','ETHBTC']:
        spot=data['markets'][name]['spot']
        lines.append(f"| {name} | {spot['verified_price'] if spot['verified_price'] is not None else 'n/a'} | {spot['status']} |")
    lines+=['','| DOT TF | Live close | Closed RSI14 | Closed ATR% | Status |','|---|---:|---:|---:|---|']
    for tf,item in data['markets']['DOTUSD']['timeframes'].items():
        live=item.get('live') or {}
        closed=(item.get('last_closed') or {}).get('indicators',{})
        lines.append(f"| {tf} | {live.get('close','n/a')} | {closed.get('rsi14','n/a')} | {closed.get('atr_pct','n/a')} | {item['status']} |")
    if data['errors']:
        lines+=['','## Source errors','']+[f"- {e['source_id']}: {e['error']}" for e in data['errors']]
    lines+=['','Formula definitions: [FORMULAS.md](../docs/FORMULAS.md)','']
    return '\n'.join(lines)
