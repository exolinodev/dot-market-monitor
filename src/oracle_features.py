"""Causal features from actual measurement snapshots, never outcome labels.

All candle asof/confirmed_at fields in the existing contract are candle OPEN
 timestamps. Add the interval before treating a candle or pivot as confirmed.
"""
import copy
import pandas as pd
from common import freshness
from oracle_common import FEATURE_VERSION, configuration, digest, number, value, compatible
from observation_common import utc, iso


def feature_inputs(data):
    """Retain the precise source-bound inputs needed for reproducible feature replay."""
    dot = data['markets']['DOTUSD']
    tfs = {}
    for tf in ('1h', '4h'):
        item = dot.get('timeframes', {}).get(tf, {})
        closed = item.get('last_closed')
        if closed:
            closed = {k: copy.deepcopy(closed.get(k)) for k in
                      ('asof_utc', 'open', 'high', 'low', 'close', 'indicators', 'structure')}
            # Accept compact snapshots too; pivots are decoded using their declared columns.
            if closed['structure'] is None:
                closed['structure'] = copy.deepcopy(item.get('structure'))
        tfs[tf] = {k: copy.deepcopy(item.get(k)) for k in
                   ('source_ids', 'calculation_at_utc', 'interval_minutes', 'gap_count')}
        tfs[tf]['last_closed'] = closed
    obs = copy.deepcopy(dot.get('observations') or {})
    obs['components'] = {k: v for k, v in obs.get('components', {}).items() if k in
        ('flow_windows', 'spot_perp_history', 'price_levels', 'anchored_vwap', 'input_lineage')}
    return {'reference_at_utc': iso(data.get('generated_at_utc') or data['meta']['generated_at_utc']),
        'sources': copy.deepcopy(data.get('sources', {})), 'timeframes': tfs,
        'observations': obs, 'relative': copy.deepcopy(data.get('relative_strength_dot_btc') or {}),
        'relative_coverage': {m:copy.deepcopy(data['markets'].get(m,{}).get('returns') or {}) for m in ('DOTUSD','BTCUSD')},
        'breadth': copy.deepcopy(data.get('breadth')), 'pivot_columns': data.get('meta', {}).get('pivot_columns')}


def effort(window, cfg):
    """Native tape return / signed-volume fraction. No near-zero division."""
    d = window.get('data') or {}
    signed = d.get('signed_volume_dot')
    buy, sell = d.get('buy_volume_dot'), d.get('sell_volume_dot')
    total = buy + sell if number(buy) and number(sell) else None
    fraction = signed / total if number(signed) and total and total >= cfg['min_volume_dot'] else None
    ret = d.get('first_to_last_price_change_pct')
    ret = ret * 100 if number(ret) else None
    impact = ret / fraction if ret is not None and fraction is not None and abs(fraction) >= cfg['min_abs_flow_fraction'] else None
    return {'signed_dot': signed, 'absolute_dot': total, 'fraction': fraction,
            'return_bps': ret, 'impact_bps': impact,
            'alignment': None if impact is None else 'aligned' if impact > 0 else 'opposed' if impact < 0 else 'flat'}


def build_features(inputs, history=(), cfg=None):
    cfg = cfg or configuration()[0]
    ref = utc(inputs['reference_at_utc'])
    obs = inputs.get('observations') or {}
    record = {'schema_version': 1, 'feature_version': FEATURE_VERSION,
        'oracle_config_sha256': digest(cfg), 'measurement_config_sha256': obs.get('config_sha256'),
        'reference_at_utc': iso(ref), 'input_sha256': digest(inputs), 'features': {}, 'evidence': {}}
    past = [r for r in history if compatible(record, r)
            and ref-pd.Timedelta(days=cfg['history_days']) <= utc(r['reference_at_utc'])
            and utc(r['reference_at_utc']).floor('h') < ref.floor('h')]
    past.sort(key=lambda r: utc(r['reference_at_utc']))
    f = record['features']
    sources = inputs.get('sources', {})

    def fresh(ids):
        return bool(ids) and all(sources.get(s, {}).get('fresh') is True and
            sources[s].get('received_at_utc') is not None and utc(sources[s]['received_at_utc']) <= ref and
            freshness(sources[s].get('source_timestamp_utc'), ref, sources[s].get('max_age_seconds', 120))['fresh']
            for s in ids)

    def add(key, val, unit, ids, start=None, end=None, reason=None, extra=None):
        good = val is not None and fresh(ids) and end is not None and utc(end) <= ref
        f[key] = {'value': val if good else None, 'unit': unit,
            'status': 'ok' if good else 'unavailable',
            'reason': None if good else reason or 'missing_stale_or_future_input',
            'source_ids': list(ids), 'coverage': {'start_utc': iso(start) if start else None,
                'end_utc': iso(end) if end else None, **(extra or {})}, 'methodology': FEATURE_VERSION}
        return value(f, key)

    def prior(key, hours=1):
        target = ref-pd.Timedelta(hours=hours)
        rows = [r for r in past if abs((utc(r['reference_at_utc'])-target).total_seconds()) <= cfg['history_tolerance_minutes']*60]
        row = min(rows, key=lambda r: (abs((utc(r['reference_at_utc'])-target).total_seconds()), r['reference_at_utc'])) if rows else None
        return row, value(row['features'], key) if row else None

    def percentile(key):
        current = value(f, key)
        rows = [r for r in past if utc(r['reference_at_utc']) >= ref-pd.Timedelta(days=cfg['percentile_lookback_days'])
                and number(value(r['features'], key))]
        vals = [value(r['features'], key) for r in rows]
        p = 100*(sum(x < current for x in vals)+.5*sum(x == current for x in vals))/len(vals) if current is not None and len(vals) >= cfg['percentile_min_samples'] else None
        source = f[key]
        add(key+'.percentile', p, 'percentile_0_100', source['source_ids'],
            rows[0]['reference_at_utc'] if rows else None, ref, 'insufficient_prior_samples',
            {'sample_count': len(vals), 'minimum_samples': cfg['percentile_min_samples'], 'current_hour_excluded': True})

    components = obs.get('components', {}) if obs.get('reference_at_utc') and utc(obs['reference_at_utc']) == ref else {}
    changes = (components.get('spot_perp_history', {}).get('data') or {}).get('changes', {})
    for h in (1, 4, 24):
        b = changes.get(f'{h}h', {})
        d = b.get('data') or {}
        oi = d.get('open_interest_dot', {})
        price = d.get('spot_midprice_usd', {})
        old, cur = oi.get('reference_value'), oi.get('current_value')
        oi_start, oi_end = oi.get('reference_source_timestamp_utc'), oi.get('source_timestamp_utc')
        aligned = (oi_start and oi_end and abs((utc(oi_end)-utc(oi_start)).total_seconds()/60-h*60) <= cfg['history_tolerance_minutes'])
        pct = (cur/old-1)*100 if oi.get('status') == 'ok' and number(cur) and number(old) and old > 0 and aligned else None
        key = f'oi.{h}h.change_pct'
        add(key, pct, 'percent', ['DOTPERP.ticker', 'DOTPERP.instrument'], oi_start, oi_end)
        percentile(key)
        p = price.get('absolute_change') if price.get('status') == 'ok' else None
        joint = f"price_{'down' if p < 0 else 'up' if p > 0 else 'flat'}_oi_{'down' if pct < 0 else 'up' if pct > 0 else 'flat'}" if p is not None and pct is not None else None
        add(f'oi.{h}h.price_relation', joint, 'category', ['DOTUSD.spread', 'DOTPERP.ticker', 'DOTPERP.instrument'], oi_start, oi_end)

    flows = (components.get('flow_windows', {}).get('data') or {})
    for venue in ('spot', 'perp'):
        source = ['DOTUSD.trades' if venue == 'spot' else 'DOTPERP.trades']
        pair = flows.get(venue, {})
        w = pair.get('current') or {}
        previous = pair.get('previous') or {}
        cov = w.get('coverage') or {}
        start, end = cov.get('window_start_utc'), cov.get('window_end_utc')
        good = (w.get('status') == 'ok' and cov.get('complete') is True and start and end
                and utc(start) < utc(end) <= ref and (ref-utc(end)).total_seconds() <= 180)
        current_metrics = effort(w, cfg) if good else {}
        for name, unit in [('signed_dot','DOT'), ('absolute_dot','DOT'), ('fraction','signed_fraction'),
                           ('return_bps','bps'), ('impact_bps','bps_per_signed_fraction'), ('alignment','category')]:
            add(f'flow.{venue}.{name}', current_metrics.get(name), unit, source, start, end,
                extra={'return_semantics': 'first_to_last_executed_trade_in_window'})
        percentile(f'flow.{venue}.signed_dot')
        prev_cov = previous.get('coverage') or {}
        paired = (good and previous.get('status') == 'ok' and prev_cov.get('complete') is True
                  and prev_cov.get('window_end_utc') == start and prev_cov.get('window_start_utc')
                  and utc(end)-utc(start) == utc(start)-utc(prev_cov['window_start_utc']))
        prev = effort(previous, cfg) if paired else {}
        curr_i, prev_i = current_metrics.get('impact_bps'), prev.get('impact_bps')
        curr_s, prev_s = current_metrics.get('signed_dot'), prev.get('signed_dot')
        comparable = (curr_i is not None and prev_i is not None and curr_s*prev_s > 0)
        reduction = (prev_i > 0 and curr_i <= prev_i*(1-cfg['impact_reduction_fraction']) and abs(curr_s) >= abs(prev_s)) if comparable else None
        add(f'flow.{venue}.impact_change_bps', curr_i-prev_i if comparable else None,
            'bps_per_signed_fraction', source, prev_cov.get('window_start_utc'), end)
        add(f'flow.{venue}.efficiency_loss', reduction, 'boolean', source, prev_cov.get('window_start_utc'), end)
        frac, ret = current_metrics.get('fraction'), current_metrics.get('return_bps')
        divergence = ('negative_flow_flat_up' if frac <= -cfg['min_abs_flow_fraction'] and ret >= -cfg['flat_return_bps']
                      else 'positive_flow_flat_down' if frac >= cfg['min_abs_flow_fraction'] and ret <= cfg['flat_return_bps'] else 'none') if frac is not None and ret is not None else None
        add(f'flow.{venue}.divergence', divergence, 'category', source, start, end)

    for tf in ('1h', '4h'):
        item = inputs['timeframes'].get(tf, {})
        m = 60 if tf == '1h' else 240
        c = item.get('last_closed') or {}
        ids = item.get('source_ids') or [f'DOTUSD.ohlc.{m}']
        start = c.get('asof_utc')
        end = utc(start)+pd.Timedelta(minutes=m) if start else None
        receipt = item.get('calculation_at_utc')
        usable = (end is not None and receipt is not None and end <= utc(receipt) <= ref
                  and end == ref.floor(f'{m}min') and item.get('gap_count') == 0)
        if not usable: c = {}
        ind = c.get('indicators') or {}
        a = ind.get('atr14')
        a = a if number(a) and a > 0 else None
        o, high, low, close = (c.get(k) for k in ('open','high','low','close'))
        valid = all(number(x) for x in (o, high, low, close)) and 0 < low <= min(o, close) <= max(o, close) <= high
        rng = high-low if valid else None
        metrics = {'clv': ((close-low)/rng if rng else None, 'fraction_0_1'),
            'lower_wick_atr': ((min(o,close)-low)/a if valid and a else None,'ATR'),
            'upper_wick_atr': ((high-max(o,close))/a if valid and a else None,'ATR'),
            'body_atr': (abs(close-o)/a if valid and a else None,'ATR'),
            'ema20_distance_atr': ((close-ind['ema20'])/a if valid and a and number(ind.get('ema20')) else None,'ATR')}
        for name, (v, unit) in metrics.items():
            add(f'candle.{tf}.{name}', v, unit, ids, start, end, 'missing_candle_or_zero_range_atr')
        structure = (c.get('structure') or {}).get('fractal') or {}
        pivots = []
        for p in structure.get('pivots', []):
            if isinstance(p, list):
                p = dict(zip(inputs.get('pivot_columns') or [], p))
                p = {**p, 'time': p.get('time_utc'), 'confirmed_at': p.get('confirmed_at_utc')}
            if p.get('confirmed_at') and end is not None and utc(p['confirmed_at'])+pd.Timedelta(minutes=m) <= min(end, ref):
                pivots.append(p)
        lows = [p for p in pivots if p['kind'] == 'low']
        highs = [p for p in pivots if p['kind'] == 'high']
        transition = None
        if len(lows) >= 2 and len(highs) >= 2:
            transition = lows[-2]['classification']+'/'+highs[-2]['classification']+'->'+lows[-1]['classification']+'/'+highs[-1]['classification'] if all(p.get('classification') for p in [lows[-2],highs[-2],lows[-1],highs[-1]]) else None
        add(f'structure.{tf}.transition', transition, 'confirmed_pivot_category', ids, start, end)
        for side, ps in [('low', lows), ('high', highs)]:
            p = ps[-1] if ps else None
            add(f'structure.{tf}.{side}_distance_atr', (close-p['price'])/a if p and valid and a else None,'ATR', ids,start,end)
            recent = p.get('classification') if p and end-pd.Timedelta(minutes=m) < utc(p['confirmed_at'])+pd.Timedelta(minutes=m) <= end else None
            add(f'structure.{tf}.new_{side}', recent, 'confirmed_pivot_category', ids,start,end, 'no_new_confirmed_pivot')
        level_block = (components.get('price_levels', {}).get('data') or {}).get(tf, {})
        levels = [x['level_usd'] for x in (level_block.get('data') or []) if 'level_usd' in x] if level_block.get('status') == 'ok' else []
        levels += [p['price'] for p in pivots[-4:] if start and utc(p['confirmed_at'])+pd.Timedelta(minutes=m) <= utc(start)]
        rows = []
        if valid:
            for level in sorted(set(levels)):
                rows.append({'level_usd': level, 'distance_atr': (close-level)/a if a else None,
                    'failed_breakdown': low < level < close, 'failed_breakout': high > level > close})
        add(f'levels.{tf}.reactions', rows if valid and levels else None, 'USD_and_ATR_and_boolean',ids,start,end)
        old_row, old_reactions = prior(f'levels.{tf}.reactions', m//60)
        for side in ('breakdown','breakout'):
            persisted = None
            if old_reactions is not None and valid:
                old_end = old_row['features'][f'levels.{tf}.reactions']['coverage']['end_utc']
                if utc(old_end) == utc(start):
                    persisted = [x['level_usd'] for x in old_reactions if x['failed_'+side] and
                        (close > x['level_usd'] if side == 'breakdown' else close < x['level_usd'])]
            add(f'levels.{tf}.{side}_persistence', persisted, 'USD_levels', ids, old_row['reference_at_utc'] if old_row else None,end)

    av = components.get('anchored_vwap', {})
    lineage = (components.get('input_lineage', {}).get('data') or {})
    distances = []
    atr_f = inputs['timeframes'].get('1h', {}).get('last_closed') or {}
    a = (atr_f.get('indicators') or {}).get('atr14') if value(f,'candle.1h.ema20_distance_atr') is not None else None
    if lineage.get('selection_record_available_asof_snapshot') and lineage.get('selection_recorded_at_utc') and utc(lineage['selection_recorded_at_utc']) <= ref:
        for row in av.get('data') or []:
            if row.get('status') == 'ok' and row.get('asof_utc') and utc(row['asof_utc']) <= ref and a:
                distances.append({'anchor_id': row['coverage']['anchor_id'], 'distance_atr': row['data']['distance_usd']/a})
    add('extension.anchored_vwap', distances or None, 'ATR', ['DOTUSD.ohlc.60'], end=ref,
        reason='anchor_selection_unavailable_asof_or_missing_inputs')

    for h in (1,4,24):
        d = inputs.get('relative', {}).get(f'{h}h', {})
        key = f'relative.{h}h.ratio_return_pct'
        ids = ['DOTUSD.ohlc.60','BTCUSD.ohlc.60']
        ends=[inputs.get('relative_coverage',{}).get(m,{}).get(f'{h}h',{}).get('asof_utc') for m in ('DOTUSD','BTCUSD')]
        aligned=all(t and utc(t)==ref.floor('h') for t in ends)
        add(key, d.get('ratio_return_pct') if d.get('status') == 'ok' and aligned else None,'percent',ids,ref.floor('h')-pd.Timedelta(hours=h), ref.floor('h'))
        old, prev = prior(key, h)
        cur = value(f,key)
        add(f'relative.{h}h.acceleration_pp',cur-prev if cur is not None and prev is not None else None,'percentage_points',ids,
            old['reference_at_utc'] if old else None,ref)
    breadth = inputs.get('breadth') or {}
    peers = sorted(k for k in breadth.get('coins', {}) if k != 'DOT')
    window = breadth.get('windows', {}).get('1h', {})
    b = window.get('fraction_positive') if window.get('status') == 'ok' and len(peers)==8 else None
    add('breadth.1h.positive_fraction',b,'fraction_0_1',['coingecko.breadth'],end=ref,extra={'peers':peers})
    old, prev = prior('breadth.1h.positive_fraction')
    same = old and old['features']['breadth.1h.positive_fraction']['coverage'].get('peers') == peers
    add('breadth.1h.turn_pp',(b-prev)*100 if same and b is not None and prev is not None else None,'percentage_points',
        ['coingecko.breadth'],old['reference_at_utc'] if old else None,ref)
    record['evidence'] = evidence(f, cfg)
    record['status'] = 'ok' if all(x['status']=='ok' for x in f.values()) else 'partial'
    return record


def evidence(f, cfg):
    """Transparent necessary gates, not a probability or a trading regime."""
    families = {name: [k for k in f if k.startswith(prefixes)] for name, prefixes in {
        'price_momentum': ('candle.', 'extension.'), 'derivatives_deleveraging': ('oi.',),
        'executed_flow': ('flow.',), 'level_reaction': ('levels.',),
        'relative_market': ('relative.', 'breadth.'), 'confirmed_structure': ('structure.',)}.items()}
    gates = {}
    for direction, sign, div, kind, struct in [('downside',-1,'negative_flow_flat_up','breakdown','HL'),
                                              ('upside',1,'positive_flow_flat_down','breakout','LH')]:
        ext = [k for k in f if k.endswith('ema20_distance_atr') and number(value(f,k)) and value(f,k)*sign >= cfg['extension_atr']]
        loss, response = [], []
        for venue in ('spot','perp'):
            frac = value(f,f'flow.{venue}.fraction')
            if frac is not None and frac*sign > 0 and value(f,f'flow.{venue}.efficiency_loss') is True:
                loss.append(f'flow.{venue}.efficiency_loss')
            if value(f,f'flow.{venue}.divergence') == div:
                response.append(f'flow.{venue}.divergence')
        clv = value(f,'candle.1h.clv')
        if clv is not None and (clv >= cfg['strong_close_location'] if sign<0 else clv <= 1-cfg['strong_close_location']):
            response.append('candle.1h.clv')
        if any(x['failed_'+kind] for x in value(f,'levels.1h.reactions') or []): response.append('levels.1h.reactions')
        acceleration = value(f,'relative.1h.acceleration_pp')
        if acceleration is not None and acceleration*sign < 0: response.append('relative.1h.acceleration_pp')
        new = value(f,'structure.1h.new_low' if sign<0 else 'structure.1h.new_high')
        confirmed = new == struct
        if confirmed: response.append('structure.1h.new_low' if sign<0 else 'structure.1h.new_high')
        gates[direction] = {'extension_feature_ids':ext, 'efficiency_loss_feature_ids':loss,
            'response_feature_ids':response, 'abc_ready':bool(ext and loss and response),
            'new_structure_confirmation':confirmed,
            'trigger_candidate': bool(ext and loss and response and confirmed)}
    return {'families': families, 'reversal_gates': gates,
            'independence_warning': 'price_momentum, level_reaction and confirmed_structure share OHLC; executed-flow metrics share trades; do not count fields as independent votes'}
