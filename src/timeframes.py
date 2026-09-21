"""UTC candles, bounded native cache, and strict open/closed separation."""
import numpy as np
import pandas as pd
from common import read_json, write_json

DOT_TIMEFRAMES = {'1m':1,'3m':3,'5m':5,'15m':15,'30m':30,'1h':60,'2h':120,'4h':240,
                  '12h':720,'1d':1440,'2d':2880,'4d':5760,'1w':10080}
DOTBTC_TIMEFRAMES = {k:v for k,v in DOT_TIMEFRAMES.items() if k in ['1m','5m','15m','30m','1h','4h','1d','1w']}
BTC_TIMEFRAMES = {'15m':15,'1h':60,'4h':240,'1d':1440,'1w':10080}
NATIVE = {1,5,15,30,60,240,1440,10080}
BASE = {3:1,120:60,720:240,2880:1440,5760:1440}
COLUMNS = ['open','high','low','close','vwap','volume','trade_count']


def validate_candles(df):
    if df.empty or not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError('Missing UTC candles')
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise ValueError('Unordered or duplicate candles')
    values = df[['open','high','low','close','volume']].astype(float)
    if not np.isfinite(values).all().all(): raise ValueError('Nonfinite OHLCV')
    if (values[['open','high','low','close']]<=0).any().any() or (values.volume<0).any():
        raise ValueError('Nonpositive price or negative volume')
    if ((values.high<values[['open','close','low']].max(axis=1)) |
        (values.low>values[['open','close','high']].min(axis=1))).any():
        raise ValueError('Inconsistent OHLC range')
    return df


def merge_candles(old, new, limit=4096):
    frames = [x for x in [old,new] if x is not None and not x.empty]
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep='last')].sort_index().tail(limit)
    return validate_candles(df)


def encode_candles(df):
    return [[int(ts.timestamp()), *[float(row[c]) for c in COLUMNS]] for ts,row in df.iterrows()]


def decode_candles(rows):
    df = pd.DataFrame(rows,columns=['time',*COLUMNS])
    df.index = pd.to_datetime(df.pop('time'),unit='s',utc=True)
    return df


def resample_candles(df, base_minutes, target_minutes, now):
    if target_minutes % base_minutes: raise ValueError('Nonintegral resampling ratio')
    validate_candles(df)
    now = pd.Timestamp(now)
    rule = f'{target_minutes}min'
    work = df[df.index<=now].copy()
    work['pv'] = work.vwap * work.volume
    grouped = work.resample(rule, origin='epoch', closed='left', label='left')
    result = grouped.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum',
                          'trade_count':'sum','pv':'sum'})
    result['vwap'] = result.pop('pv') / result.volume.replace(0,np.nan)
    # Discard buckets missing their beginning or any elapsed base candle.
    starts = pd.Series(work.index,index=work.index).resample(rule,origin='epoch').min()
    counts = grouped['close'].count()
    expected = np.minimum(target_minutes//base_minutes,
                          np.floor((now-result.index).total_seconds()/(base_minutes*60)).astype(int)+1)
    result = result[(starts==result.index) & (counts==expected)]
    return result[COLUMNS].dropna(subset=['close'])


def split_modes(df, minutes, now):
    now = pd.Timestamp(now)
    end = df.index + pd.Timedelta(minutes=minutes)
    closed = df[end<=now]
    current = df[(df.index<=now) & (end>now)]
    live = df.loc[:current.index[-1]] if not current.empty else None
    return live, closed


class CandleCache:
    def __init__(self, path):
        self.path=path
        self.rows=read_json(path,{})

    def merge(self, key, new, limit=4096):
        old = decode_candles(self.rows[key]) if key in self.rows else None
        merged = merge_candles(old,new,limit=limit)
        self.rows[key]=encode_candles(merged)
        return merged

    def save(self):
        write_json(self.path,self.rows,compressed=True)
