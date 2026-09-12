import numpy as np
import pandas as pd
import pytest
from indicators import _rma,ema,dema,rsi,stoch_rsi,macd,atr,adx_dmi,bollinger,obv,mfi,cmf,cross_event,add_indicators,IndicatorConfig
from test_indicators import sample_df


def reference_ema(values,period):
    state=None
    out=[]
    count=0
    for v in values:
        if np.isnan(v):
            out.append(np.nan)
            continue
        count+=1
        state=v if state is None else (2*v+(period-1)*state)/(period+1)
        out.append(state if count>=period else np.nan)
    return np.array(out)


@pytest.mark.parametrize('period',[9,20,21,50,100,200])
def test_each_ema_against_scalar_recurrence(period):
    values=sample_df(500).close
    np.testing.assert_allclose(ema(values,period),reference_ema(values,period),atol=1e-12)


@pytest.mark.parametrize('period',[50,200])
def test_sma_reference(period):
    df=sample_df(260)
    result=add_indicators(df,IndicatorConfig(),False)
    assert result[f'sma{period}'].iloc[-1]==pytest.approx(sum(df.close.iloc[-period:])/period)
    assert result[f'sma{period}'].iloc[:period-1].isna().all()


def test_rma_seed_and_update():
    values=pd.Series([np.nan,1.,2.,3.,8.])
    np.testing.assert_allclose(_rma(values,3),[np.nan,np.nan,np.nan,2.,4.])


def test_dema20_and_macd_reference():
    values=sample_df(300).close
    first=reference_ema(values,20)
    np.testing.assert_allclose(dema(values,20),2*first-reference_ema(first,20),atol=1e-12)
    line,signal,hist=macd(values)
    expected=reference_ema(values,12)-reference_ema(values,26)
    expected_signal=reference_ema(expected,9)
    np.testing.assert_allclose(line,expected,atol=1e-12)
    np.testing.assert_allclose(signal,expected_signal,atol=1e-12)
    np.testing.assert_allclose(hist,expected-expected_signal,atol=1e-12)


def test_stoch_rsi_smoothing_with_explicit_rsi_fixture(monkeypatch):
    import indicators
    r=pd.Series([10.,20.,30.,15.,35.,25.,40.,20.])
    monkeypatch.setattr(indicators,'rsi',lambda *args:r)
    raw,k,d=stoch_rsi(r,14,3,3,3)
    expected_raw=pd.Series([np.nan,np.nan,100.,0.,100.,50.,100.,0.])
    expected_k=[np.nan,np.nan,np.nan,np.nan,200/3,50.,250/3,50.]
    expected_d=[np.nan]*6+[(200/3+50+250/3)/3,(50+250/3+50)/3]
    np.testing.assert_allclose(raw,expected_raw)
    np.testing.assert_allclose(k,expected_k)
    np.testing.assert_allclose(d,expected_d)


def test_atr_wilder_and_atr_percent():
    df=pd.DataFrame({'high':[12,14,13,15],'low':[10,11,9,13],'close':[11,12,10,14]})
    np.testing.assert_allclose(atr(df,3),[np.nan,np.nan,3.,11/3])
    actual=add_indicators(sample_df(),IndicatorConfig(),False)
    assert actual.atr_pct.iloc[-1]==pytest.approx(actual.atr14.iloc[-1]/actual.close.iloc[-1]*100)


def test_adx_dmi_monotonic_market():
    close=pd.Series(np.arange(1.,61.))
    df=pd.DataFrame({'high':close+1,'low':close-1,'close':close})
    adx,pdi,mdi=adx_dmi(df)
    assert adx.iloc[:27].isna().all()
    assert adx.iloc[27]==pytest.approx(100)
    assert pdi.iloc[-1]==pytest.approx(50)
    assert mdi.iloc[-1]==0


def test_bollinger_population_std_and_bandwidth():
    lower,middle,upper,width=bollinger(pd.Series(np.arange(1.,21.)))
    sd=np.sqrt(33.25)
    assert middle.iloc[-1]==10.5
    assert lower.iloc[-1]==pytest.approx(10.5-2*sd)
    assert upper.iloc[-1]==pytest.approx(10.5+2*sd)
    assert width.iloc[-1]==pytest.approx(4*sd/10.5*100)


def test_obv_mfi_cmf_hand_calculations():
    df=pd.DataFrame({'close':[10.,12.,11.,13.],'high':[11.,13.,12.,14.],
                     'low':[9.,11.,10.,12.],'volume':[1.,2.,3.,4.]})
    np.testing.assert_allclose(obv(df),[0,2,-1,3])
    assert mfi(df,3).iloc[-1]==pytest.approx(100*76/(76+33))
    assert cmf(df,3).iloc[-1]==0
    df['close']=df['high']
    assert cmf(df,3).iloc[-1]==1


def test_volume_statistics_reference():
    result=add_indicators(sample_df(),IndicatorConfig(),True)
    v=result.volume.tail(20)
    assert result.volume_ratio20.iloc[-1]==pytest.approx(v.iloc[-1]/v.mean())
    assert result.volume_z20.iloc[-1]==pytest.approx((v.iloc[-1]-v.mean())/v.std(ddof=0))


def test_cross_count_and_missing_history():
    index=pd.date_range('2026-01-01',periods=5,freq='h',tz='UTC')
    series=pd.Series([-2,-1,2,3,4],index=index)
    event=cross_event(series,0)
    assert event=={'direction':'up','bars_since':2,'at_utc':index[2].isoformat()}
    assert cross_event(series,10)['bars_since'] is None


def test_flat_rsi_is_50_and_zero_volume_is_not_fabricated():
    assert rsi(pd.Series([10.]*40)).iloc[-1]==50
    df=sample_df()
    df['volume']=0
    result=add_indicators(df,IndicatorConfig(),True)
    assert pd.isna(result.cmf20.iloc[-1])
    assert pd.isna(result.volume_ratio20.iloc[-1])
