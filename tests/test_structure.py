import pandas as pd
import pytest
from structure import fractal_pivots,zigzag_pivots,fib_levels,level_distances,wave_rule_flags,divergences


def frame(values):
    return pd.DataFrame({'close':values,'high':values,'low':values},index=pd.date_range('2026-01-01',periods=len(values),freq='h',tz='UTC'))


def test_fractals_confirm_after_two_bars_and_reject_ties():
    df=frame([1,2,5,3,2,1,2,4,2,1])
    pivots=fractal_pivots(df)
    assert pivots[0]['price']==5
    assert pivots[0]['confirmed_at']==df.index[4].isoformat()
    assert pivots[1]['kind']=='low'
    assert pivots[-1]['classification']=='LH'
    assert fractal_pivots(frame([1,2,5,5,1]))==[]


def test_zigzag_requires_atr_reversal_and_excludes_terminal_extreme():
    df=frame([10,11,13,12,11,8,9,10,14])
    pivots=zigzag_pivots(df,pd.Series(1.,index=df.index),2)
    assert [(p['kind'],p['price']) for p in pivots]==[('low',10.),('high',13.),('low',8.)]
    assert pivots[-1]['confirmed_at']==df.index[7].isoformat()


def test_fixed_fibs_and_level_distances():
    fib=fib_levels('0.7324','1.2848')
    assert fib['retracements']['0.5']==1.0086
    assert fib['retracements']['0.236']==1.1544336
    assert fib['extensions']['1.618']==1.6261832
    assert level_distances(1.1,[1.0])['1.0']['distance_pct']==pytest.approx(10)
    assert level_distances(None,[1.0])['1.0']['distance'] is None


def test_wave_flags_are_conditional_geometry_not_a_count():
    p=[{'kind':k,'price':v,'time':str(i)} for i,(k,v) in enumerate(zip(['low','high','low','high','low'],[1,2,1.5,3,1.9]))]
    assert wave_rule_flags(p)['hypothetical_1_4_price_overlap'] is True
    p[-1]['price']=2.1
    assert wave_rule_flags(p)['hypothetical_1_4_price_overlap'] is False
    assert wave_rule_flags(p[:4])['hypothetical_1_4_price_overlap'] is None


def test_divergence_requires_price_indicator_and_separation_thresholds():
    df=frame([10,9,10,11,10,8,9,10])
    df['rsi14']=[50.,25.,40.,50.,40.,30.,40.,50.]
    df['macd']=[0,-1,0,1,0,-.5,0,1]
    pivots=[{'kind':'low','price':9,'bar':1,'time':df.index[1].isoformat(),'confirmed_at':df.index[3].isoformat()},
            {'kind':'low','price':8,'bar':5,'time':df.index[5].isoformat(),'confirmed_at':df.index[7].isoformat()}]
    atr=pd.Series(1.,index=df.index)
    result=divergences(df,pivots,atr)
    assert result['bullish_rsi14']['flag'] is True
    assert result['bullish_macd']['flag'] is True
    df.loc[df.index[5],'rsi14']=25.5
    assert divergences(df,pivots,atr)['bullish_rsi14']['flag'] is False
