import numpy as np
import pandas as pd
import pytest
from orderflow import orderbook_metrics,tape_metrics,slippage,absorption,wall_persistence


def book():
    return {'bids':[{'price':99.,'volume':20},{'price':98.,'volume':30}],
            'asks':[{'price':101.,'volume':10},{'price':102.,'volume':30}]}


def tape():
    now=pd.Timestamp('2026-09-12T12:00:00Z')
    df=pd.DataFrame({'time':[now-pd.Timedelta(seconds=i) for i in [50,40,30]],
                     'price':[100.,101.,102.],'volume':[2.,1.,3.],'side':['b','s','b'],'trade_id':['a','b','c']})
    df.attrs={'coverage_start':(now-pd.Timedelta(hours=4)).isoformat(),'coverage_end':now.isoformat()}
    return df,now


def test_spread_microprice_depth_notional_and_imbalance():
    x=orderbook_metrics(book())
    assert x['midprice']==100
    assert x['spread_absolute']==2
    assert x['spread_bps']==200
    assert x['microprice']==pytest.approx((101*20+99*10)/30)
    assert x['depth']['100']['imbalance']==pytest.approx(1/3)
    assert x['depth']['100']['bid_notional']==1980
    assert x['depth']['100']['ask_notional']==1010
    assert set(x['depth'])=={'5','10','25','50','100','200'}


def test_slippage_consumes_book_in_correct_order_and_marks_insufficient():
    x=slippage(book()['asks'],100,'buy',2020,'USD')
    assert x['fill_complete']
    assert x['vwap']==pytest.approx(2020/(10+1010/102))
    x=slippage(book()['bids'],100,'sell',25,'DOT')
    assert x['vwap']==pytest.approx((20*99+5*98)/25)
    assert x['slippage_bps']>0
    x=slippage(book()['asks'],100,'buy',5000,'DOT')
    assert not x['fill_complete']
    assert x['vwap'] is None
    assert x['unfilled']==4960


def test_cvd_notional_delta_counts_and_percentiles():
    df,now=tape()
    x=tape_metrics(df,now)
    one=x['windows']['1m']
    assert one['buy_volume']==5
    assert one['sell_volume']==1
    assert one['buy_notional']==506
    assert one['sell_notional']==101
    assert one['trade_count']==3
    assert one['average_trade_size']==2
    assert one['delta']==one['cvd']==4
    assert one['cvd_min']==0 and one['cvd_max']==4
    assert one['window_complete']
    assert x['large_trades']['size_p95']==pytest.approx(2.9)
    df.attrs['coverage_start']=(now-pd.Timedelta(seconds=55)).isoformat()
    assert not tape_metrics(df,now)['windows']['1m']['window_complete']


def test_future_trades_excluded_and_old_trades_are_not_current_flow():
    df,now=tape()
    assert tape_metrics(df,now-pd.Timedelta(hours=1))['status']=='unavailable'
    assert tape_metrics(df,now+pd.Timedelta(hours=1))['windows']['1m']['delta']==0


def test_crossed_book_rejected():
    x=book(); x['bids'][0]['price']=105
    with pytest.raises(ValueError):orderbook_metrics(x)


def test_absorption_requires_large_delta_and_little_price_progress():
    now=pd.Timestamp('2026-09-12T12:00:00Z')
    rows=[]
    for i in range(48):
        for offset in [10,20]:
            rows.append({'time':now-pd.Timedelta(minutes=i*5,seconds=offset),
                         'price':100.,'volume':100 if i==0 else (i%3+1),'side':'s' if i%2==0 else 'b'})
    df=pd.DataFrame(rows).sort_values('time')
    df.attrs={'coverage_start':(now-pd.Timedelta(hours=4)).isoformat(),'coverage_end':now.isoformat()}
    x=absorption(df,now,1)
    assert x['buy_side_candidate'] and x['absorption_candidate']
    assert x['delta_z_score']<=-2
    assert x['price_change_atr']==0
    df.loc[df.index[-1],'price']=90
    assert not absorption(df,now,1)['absorption_candidate']
    df.attrs['coverage_start']=(now-pd.Timedelta(hours=1)).isoformat()
    assert absorption(df,now,1)['status']=='unavailable'


def test_wall_new_stable_and_removed_with_incomplete_tape():
    df,now=tape()
    current=orderbook_metrics(book())
    first=wall_persistence(current,[],df,None,now)
    assert all(w['state']=='new' for w in first['walls'])
    second=wall_persistence(current,first['walls'],df,now-pd.Timedelta(hours=1),now)
    assert all(w['state']=='stable' and w['snapshots']==2 for w in second['walls'])
    removed={'side':'bid','price':97,'volume':10,'distance_bps':300}
    df.attrs['coverage_start']=(now-pd.Timedelta(minutes=1)).isoformat()
    result=wall_persistence(current,[removed],df,(now-pd.Timedelta(hours=1)).isoformat(),now)
    old=result['walls'][-1]
    assert old['wall_removed_without_observed_trade'] is None
    assert old['identity_proven'] is False
