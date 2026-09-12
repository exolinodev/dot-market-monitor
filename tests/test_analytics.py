import numpy as np
import pandas as pd
import pytest
from analytics import closed_returns,realized_volatility,correlation_beta,breadth_statistics
from test_indicators import sample_df


def test_returns_have_exact_closed_reference_timestamps():
    df=sample_df(200)
    now=df.index[-1]+pd.Timedelta(minutes=30)
    x=closed_returns(df,60,now)
    assert x['1h']['value_pct']==pytest.approx((df.close.iloc[-2]/df.close.iloc[-3]-1)*100)
    assert x['7d']['value_pct']==pytest.approx((df.close.iloc[-2]/df.close.iloc[-170]-1)*100)
    assert pd.Timestamp(x['1h']['asof_utc'])==df.index[-1]


def test_volatility_is_nonannualized_sum_of_squared_log_returns():
    minute=sample_df(61)
    minute.index=pd.date_range('2026-09-12',periods=61,freq='min',tz='UTC')
    minute['close']=np.exp(np.arange(61)*.001)
    now=minute.index[-1]+pd.Timedelta(minutes=1)
    x=realized_volatility(minute,None,now)
    assert x['1h']['value_pct']==pytest.approx(np.sqrt(60)*.001*100)
    assert x['24h']['value_pct'] is None
    assert realized_volatility(minute.drop(minute.index[4]),None,now)['1h']['value_pct'] is None


def test_rolling_correlation_beta_known_linear_relation():
    btc=sample_df(220)
    logs=np.cumsum(np.sin(np.arange(220)/5)*.001)
    btc['close']=np.exp(logs)
    dot=btc.copy(); dot['close']=np.exp(2*logs)
    eth=btc.copy(); eth['close']=np.exp(-logs)
    x=correlation_beta(dot,btc,eth,btc.index[-1]+pd.Timedelta(hours=1))
    for period in ['24h','7d']:
        assert x['DOT_BTC'][period]['correlation']==pytest.approx(1)
        assert x['DOT_BTC'][period]['beta']==pytest.approx(2)
        assert x['DOT_ETH'][period]['correlation']==pytest.approx(-1)


def test_breadth_median_score_and_outlier_do_not_follow_extreme():
    values=[-3,-2,-1,0,1,2,3,80]
    coins={str(i):{'returns_pct':dict.fromkeys(['1h','24h','7d'],v)} for i,v in enumerate(values)}
    coins['DOT']={'returns_pct':dict.fromkeys(['1h','24h','7d'],-1)}
    x=breadth_statistics(coins)['windows']['24h']
    assert x['median_pct']==.5
    assert x['outliers']==['7']
    assert x['fraction_positive']==.5
    assert x['breadth_score']==12.5
    assert x['dot_relative_return_vs_alt_median']==-1.5
