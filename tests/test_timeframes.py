import numpy as np
import pandas as pd
import pytest
from test_indicators import sample_df
from timeframes import resample_candles, split_modes, merge_candles, DOT_TIMEFRAMES
from pipeline import Collector


def minute_df(n=12,start='2026-09-12T00:00:00Z'):
    df=sample_df(n)
    df.index=pd.date_range(start,periods=n,freq='min')
    return df


def test_perp_cache_reload_preserves_unknown_optional_measurements(tmp_path):
    from common import read_json
    from timeframes import CandleCache, decode_candles
    path = tmp_path/'ohlc.json.gz'
    key = 'DOTPERP.trade.1'
    old = minute_df(2)
    old['vwap'] = old['trade_count'] = np.nan
    cache = CandleCache(path)
    cache.merge(key, old); cache.save()
    stored = read_json(path)[key]
    assert all(row[5] is None and row[7] is None for row in stored)
    # A real next process reloads JSON null as None, then appends fresh rows.
    fresh = minute_df(2, start='2026-09-12T00:02:00Z')
    fresh['vwap'] = fresh['trade_count'] = np.nan
    reloaded = CandleCache(path)
    reloaded.merge(key, fresh); reloaded.save()
    result = decode_candles(read_json(path)[key])
    assert len(result) == 4
    assert result[['vwap', 'trade_count']].isna().all().all()
    expected = pd.concat([old, fresh])[['open', 'high', 'low', 'close', 'volume']]
    expected.index.name = 'time'
    pd.testing.assert_frame_equal(result[expected.columns], expected, check_freq=False)


@pytest.mark.parametrize('target,base',[(3,1),(120,60),(720,240),(2880,1440),(5760,1440)])
def test_all_resampled_timeframes_match_ohlcv_aggregation(target,base):
    ratio=target//base
    df=sample_df(3*ratio)
    df.index=pd.date_range('1970-01-01',periods=len(df),freq=f'{base}min',tz='UTC')
    now=df.index[-1]+pd.Timedelta(seconds=10)
    result=resample_candles(df,base,target,now)
    first=df.iloc[:ratio]
    assert result.iloc[0].open==first.iloc[0].open
    assert result.iloc[0].high==first.high.max()
    assert result.iloc[0].low==first.low.min()
    assert result.iloc[0].close==first.iloc[-1].close
    assert result.iloc[0].volume==first.volume.sum()
    assert result.iloc[0].trade_count==first.trade_count.sum()
    assert result.iloc[0].vwap==pytest.approx((first.vwap*first.volume).sum()/first.volume.sum())
    assert result.index[0].timestamp()%(target*60)==0


def test_missing_subcandle_does_not_create_a_complete_candle():
    df=minute_df().drop(pd.Timestamp('2026-09-12T00:01:00Z'))
    result=resample_candles(df,1,3,pd.Timestamp('2026-09-12T00:11:30Z'))
    assert pd.Timestamp('2026-09-12T00:00:00Z') not in result.index
    assert len(result)==3


def test_resample_rejects_partial_initial_bucket():
    df=minute_df(start='2026-09-12T00:01:00Z')
    result=resample_candles(df,1,3,pd.Timestamp('2026-09-12T00:12:30Z'))
    assert result.index[0]==pd.Timestamp('2026-09-12T00:03:00Z')


def test_live_and_closed_indicators_are_separate_and_causal():
    df=sample_df()
    now=df.index[-1]+pd.Timedelta(minutes=30)
    first=Collector.timeframe(df,60,60,now.isoformat(),'fixture')
    changed=df.copy()
    changed.loc[df.index[-1],['close','high']]=99
    second=Collector.timeframe(changed,60,60,now.isoformat(),'fixture')
    assert first['last_closed']==second['last_closed']
    assert first['live']['indicators']['rsi14']!=second['live']['indicators']['rsi14']
    assert first['live']['structure']==second['live']['structure']
    assert first['last_closed']['asof_utc']==df.index[-2].isoformat()


def test_exact_boundary_moves_candle_to_closed():
    df=minute_df()
    live,closed=split_modes(df,1,df.index[-1]+pd.Timedelta(minutes=1))
    assert live is None
    assert len(closed)==len(df)


def test_new_source_candle_replaces_cached_open_value():
    old=minute_df()
    new=old.iloc[-2:].copy()
    new.iloc[-1,new.columns.get_loc('volume')]=777
    merged=merge_candles(old,new,limit=8)
    assert len(merged)==8
    assert merged.iloc[-1].volume==777
    assert not merged.index.has_duplicates


def test_timeframe_inventory():
    assert list(DOT_TIMEFRAMES)==['1m','3m','5m','15m','30m','1h','2h','4h','12h','1d','2d','4d','1w']


def test_received_open_bar_never_becomes_closed_during_a_slow_run(tmp_path):
    collector=Collector(tmp_path)
    df=minute_df()
    collector.frames[('DOTUSD',1)]=df
    collector.sources['DOTUSD.ohlc.1']={'fresh':True,'received_at_utc':(df.index[-1]+pd.Timedelta(seconds=30)).isoformat()}
    closed=collector.closed_frame('DOTUSD',1)
    assert closed.index[-1]==df.index[-2]
