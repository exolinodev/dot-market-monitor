"""Confirmed, causal pivots and mathematical helpers; no wave counts."""
from decimal import Decimal
import numpy as np
import pandas as pd
from common import finite


def classify(pivots):
    previous = {}
    for pivot in pivots:
        old = previous.get(pivot['kind'])
        delta = None if old is None else pivot['price'] - old['price']
        pivot['classification'] = None if delta is None else (
            ('HH' if delta > 0 else 'LH' if delta < 0 else 'EH') if pivot['kind'] == 'high'
            else ('HL' if delta > 0 else 'LL' if delta < 0 else 'EL'))
        previous[pivot['kind']] = pivot
    return pivots


def fractal_pivots(df, left=2, right=2):
    pivots = []
    h, l = df.high.to_numpy(), df.low.to_numpy()
    for i in range(left, len(df) - right):
        for kind, values, extreme, test in [('high', h, np.max, lambda a,b: a>b), ('low', l, np.min, lambda a,b: a<b)]:
            # Strict on both sides: equal-high/low plateaus have no fractal pivot.
            if test(values[i], extreme(values[i-left:i])) and test(values[i], extreme(values[i+1:i+right+1])):
                pivots.append({'kind':kind, 'bar':i, 'time':df.index[i].isoformat(), 'price':float(values[i]),
                               'confirmed_at':df.index[i+right].isoformat(), 'confirmation_bar':i+right})
    return classify(pivots)


def zigzag_pivots(df, atr_values, multiple=2.0):
    """Close extrema confirmed by reversal >= 2 ATR at the candidate extreme.

    Uses closes to avoid inventing an intrabar high/low ordering. No terminal
    unconfirmed pivot is returned. ATR is frozen when the extreme is formed.
    """
    pivots = []
    valid = np.flatnonzero(atr_values.notna().to_numpy() & (atr_values.to_numpy() > 0))
    if not len(valid):
        return pivots
    start = int(valid[0])
    high = low = start
    direction = 0
    values = df.close.to_numpy()
    def emit(kind, i, confirm):
        pivots.append({'kind':kind,'bar':i,'time':df.index[i].isoformat(),'price':float(values[i]),
                       'confirmed_at':df.index[confirm].isoformat(),'confirmation_bar':confirm,
                       'reversal_threshold':float(atr_values.iloc[i] * multiple)})
    for i in range(start + 1, len(df)):
        if not np.isfinite(atr_values.iloc[i]) or atr_values.iloc[i] <= 0:
            continue
        if direction == 0:
            if values[i] > values[high]: high = i
            if values[i] < values[low]: low = i
            if values[i] - values[low] >= multiple * atr_values.iloc[low]:
                emit('low', low, i)
                direction, high = 1, i
            elif values[high] - values[i] >= multiple * atr_values.iloc[high]:
                emit('high', high, i)
                direction, low = -1, i
        elif direction == 1:
            if values[i] > values[high]: high = i
            elif values[high] - values[i] >= multiple * atr_values.iloc[high]:
                emit('high', high, i)
                direction, low = -1, i
        else:
            if values[i] < values[low]: low = i
            elif values[i] - values[low] >= multiple * atr_values.iloc[low]:
                emit('low', low, i)
                direction, high = 1, i
    return classify(pivots)


def pivot_summary(pivots):
    highs = [p for p in pivots if p['kind']=='high']
    lows = [p for p in pivots if p['kind']=='low']
    return {'last_pivot_high':highs[-1] if highs else None, 'last_pivot_low':lows[-1] if lows else None,
            'previous_pivot_high':highs[-2] if len(highs)>1 else None,
            'previous_pivot_low':lows[-2] if len(lows)>1 else None,
            'high_state':highs[-1]['classification'] if highs else None,
            'low_state':lows[-1]['classification'] if lows else None, 'pivots':pivots[-12:]}


def divergences(df, pivots, atr_values):
    result = {}
    for kind, direction in [('low','bullish'), ('high','bearish')]:
        candidates = [p for p in pivots if p['kind']==kind]
        for indicator, threshold in [('rsi14',2.0), ('macd',None)]:
            key = f'{direction}_{indicator}'
            out = {'flag':False,'status':'insufficient_pivots','points':None}
            result[key] = out
            if len(candidates)<2: continue
            a,b = candidates[-2:]
            ai, bi = a['bar'], b['bar']
            av,bv = finite(df[indicator].iloc[ai]),finite(df[indicator].iloc[bi])
            atr_v = finite(atr_values.iloc[bi])
            out['points'] = [{'time':p['time'],'confirmed_at':p['confirmed_at'],'price':p['price'],'indicator':v}
                             for p,v in [(a,av),(b,bv)]]
            out.update({'bars_between':bi-ai, 'status':'ok'})
            if av is None or bv is None or atr_v is None:
                out['status']='warmup'
                continue
            price_threshold = max(0.001 * abs(a['price']), 0.1 * atr_v)
            indicator_threshold = threshold if threshold is not None else 0.02 * atr_v
            price_change, indicator_change = b['price']-a['price'], bv-av
            out.update({'price_threshold':price_threshold,'indicator_threshold':indicator_threshold,
                        'price_change':price_change,'indicator_change':indicator_change})
            out['flag'] = bool(3 <= bi-ai <= 60 and (
                price_change <= -price_threshold and indicator_change >= indicator_threshold if kind=='low'
                else price_change >= price_threshold and indicator_change <= -indicator_threshold))
    return result


def fib_levels(start, end):
    a,b = Decimal(str(start)), Decimal(str(end))
    span = b-a
    return {'start':float(a),'end':float(b),
            'retracements':{r:float(b-span*Decimal(r)) for r in ['0.236','0.382','0.5','0.618','0.786']},
            'extensions':{r:float(a+span*Decimal(r)) for r in ['1.0','1.272','1.618','2.0','2.618']}}


def level_distances(price, levels):
    return {str(level):{'distance':None if price is None else float(price)-float(level),
                        'distance_pct':None if price is None else (float(price)/float(level)-1)*100}
            for level in levels}


def wave_rule_flags(pivots):
    # Conditional geometry only: p0..p4 are NOT assigned an Elliott count.
    p = pivots[-5:]
    if len(p)<5 or any(a['kind']==b['kind'] for a,b in zip(p,p[1:])):
        return {'status':'insufficient_alternating_pivots','hypothetical_1_4_price_overlap':None,'anchors':None}
    first = sorted([p[0]['price'],p[1]['price']])
    fourth = sorted([p[3]['price'],p[4]['price']])
    return {'status':'conditional_geometry_only',
            'hypothetical_1_4_price_overlap':max(first[0],fourth[0]) <= min(first[1],fourth[1]),
            'anchors':[{'label':f'p{i}','time':x['time'],'price':x['price']} for i,x in enumerate(p)]}
