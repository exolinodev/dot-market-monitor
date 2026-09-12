from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from common import finite
from structure import fractal_pivots, zigzag_pivots, pivot_summary, divergences, fib_levels, wave_rule_flags


@dataclass(frozen=True)
class IndicatorConfig:
    rsi_period: int = 14
    stoch_period: int = 14
    stoch_k: int = 3
    stoch_d: int = 3
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    pivot_left: int = 2
    pivot_right: int = 2


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing seeded with the mean of the first period observations."""
    valid = series.dropna()
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(valid) < period:
        return result
    seeded = valid.iloc[period - 1:].copy()
    seeded.iloc[0] = valid.iloc[:period].mean()
    result.loc[seeded.index] = seeded.ewm(alpha=1 / period, adjust=False).mean()
    return result


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def dema(series: pd.Series, span: int) -> pd.Series:
    first = ema(series, span)
    second = ema(first, span)
    return 2 * first - second


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    avg_gain = _rma(up, period)
    avg_loss = _rma(down, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.where(avg_loss != 0, 100.0)
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    out = out.where(~both_zero, 50.0)
    return out


def stoch_rsi(
    series: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    r = rsi(series, rsi_period)
    low = r.rolling(stoch_period, min_periods=stoch_period).min()
    high = r.rolling(stoch_period, min_periods=stoch_period).max()
    denominator = (high - low).replace(0, np.nan)
    raw = 100 * (r - low) / denominator
    k = raw.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(smooth_d, min_periods=smooth_d).mean()
    return raw, k, d


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = series.ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = series.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = fast_ema - slow_ema
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = line - signal_line
    return line, signal_line, hist


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    parts = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return parts.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _rma(true_range(df), period)


def anchored_daily_vwap_utc(df: pd.DataFrame) -> pd.Series:
    """Daily session VWAP anchored at 00:00 UTC. Intended for intraday candles."""
    idx = pd.to_datetime(df.index, utc=True)
    session = idx.floor("D")
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    price = df["vwap"] if "vwap" in df else typical
    pv = price * df["volume"]
    pv_cum = pv.groupby(session).cumsum()
    vol_cum = df["volume"].groupby(session).cumsum()
    result = pv_cum / vol_cum.replace(0, np.nan)
    # Kraken returns at most 720 candles; the first session may be truncated.
    session_start = pd.Series(idx, index=df.index).groupby(session).transform("min")
    intervals = idx.to_series().diff().dropna()
    if len(intervals):
        step = intervals.min()
        expected = ((idx - session) / step).astype(int) + 1
        actual = pd.Series(1, index=df.index).groupby(session).cumsum()
        result = result.where(actual == expected)
    return result.where(session_start == session)


def adx_dmi(df: pd.DataFrame, period: int = 14):
    up, down = df.high.diff(), -df.low.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    plus.iloc[0] = minus.iloc[0] = np.nan
    tr = true_range(df)
    tr.iloc[0] = np.nan
    denominator = _rma(tr, period).replace(0, np.nan)
    pdi, mdi = 100 * _rma(plus, period) / denominator, 100 * _rma(minus, period) / denominator
    total = pdi + mdi
    dx = (100 * (pdi-mdi).abs()/total.replace(0,np.nan)).where(total != 0, 0.0)
    return _rma(dx,period), pdi, mdi


def bollinger(series, period=20, deviations=2):
    middle=series.rolling(period,min_periods=period).mean()
    sd=series.rolling(period,min_periods=period).std(ddof=0)
    lower,upper=middle-deviations*sd,middle+deviations*sd
    return lower,middle,upper,100*(upper-lower)/middle.replace(0,np.nan)


def obv(df):
    direction=np.sign(df.close.diff()).fillna(0)
    return (direction*df.volume).cumsum()


def mfi(df,period=14):
    typical=(df.high+df.low+df.close)/3
    flow=typical*df.volume
    pos=flow.where(typical.diff()>0,0.0)
    neg=flow.where(typical.diff()<0,0.0)
    pos.iloc[0]=neg.iloc[0]=np.nan
    p=pos.rolling(period).sum()
    n=neg.rolling(period).sum()
    value=100-100/(1+p/n.replace(0,np.nan))
    return value.where(n!=0,100.0).where(~((p==0)&(n==0)),50.0)


def cmf(df,period=20):
    spread=df.high-df.low
    multiplier=((2*df.close-df.high-df.low)/spread.replace(0,np.nan)).where(spread!=0,0.0)
    return (multiplier*df.volume).rolling(period).sum()/df.volume.rolling(period).sum().replace(0,np.nan)


def cross_event(left, right):
    difference=left-right
    previous=difference.shift(1)
    up=(difference>0)&(previous<=0)
    down=(difference<0)&(previous>=0)
    where=np.flatnonzero((up|down).to_numpy())
    if not len(where):
        return {'direction':None,'bars_since':None,'at_utc':None}
    i=int(where[-1])
    return {'direction':'up' if bool(up.iloc[i]) else 'down','bars_since':len(left)-1-i,
            'at_utc':left.index[i].isoformat()}


def add_indicators(df: pd.DataFrame, cfg: IndicatorConfig, include_vwap: bool) -> pd.DataFrame:
    out = df.copy()
    for period in (9, 21, 100):
        out[f"ema{period}"] = ema(out["close"], period)
    for period in (50, 200):
        out[f"sma{period}"] = out["close"].rolling(period, min_periods=period).mean()
    out["ema20"] = ema(out["close"], cfg.ema_fast)
    out["ema50"] = ema(out["close"], cfg.ema_mid)
    out["ema200"] = ema(out["close"], cfg.ema_slow)
    out["dema20"] = dema(out["close"], cfg.ema_fast)
    out["rsi14"] = rsi(out["close"], cfg.rsi_period)
    raw, k, d = stoch_rsi(
        out["close"],
        cfg.rsi_period,
        cfg.stoch_period,
        cfg.stoch_k,
        cfg.stoch_d,
    )
    out["stoch_rsi"] = raw
    out["stoch_rsi_k"] = k
    out["stoch_rsi_d"] = d
    m, s, h = macd(out["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    out["macd"] = m
    out["macd_signal"] = s
    out["macd_hist"] = h
    out["atr14"] = atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr14"] / out["close"] * 100
    out["bb_lower"],out["bb_middle"],out["bb_upper"],out["bb_bandwidth_pct"] = bollinger(out.close)
    out["adx14"],out["plus_di14"],out["minus_di14"] = adx_dmi(out)
    out["obv"] = obv(out)
    out["mfi14"] = mfi(out)
    out["cmf20"] = cmf(out)
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["volume_std20"] = out["volume"].rolling(20, min_periods=20).std(ddof=0)
    out["volume_ratio20"] = out["volume"] / out["volume_sma20"].replace(0, np.nan)
    out["volume_z20"] = (out["volume"] - out["volume_sma20"]) / out["volume_std20"].replace(0, np.nan)
    if include_vwap:
        out["vwap_session_utc"] = anchored_daily_vwap_utc(out)
    else:
        out["vwap_session_utc"] = np.nan
    return out



def snapshot(df: pd.DataFrame, cfg: IndicatorConfig, include_vwap: bool, closed_only=None) -> Dict[str, Any]:
    if df.empty:
        raise ValueError("Cannot snapshot empty dataframe")
    enriched = add_indicators(df, cfg, include_vwap)
    row = enriched.iloc[-1]
    names = [name for name in enriched.columns if name not in df.columns and name != 'volume_std20']
    indicators = {('session_vwap_utc' if name == 'vwap_session_utc' else name):finite(row[name]) for name in names}
    slopes = {('session_vwap_utc' if name == 'vwap_session_utc' else name):
              (finite(enriched[name].iloc[-1]-enriched[name].iloc[-2]) if len(df)>1 else None) for name in names}
    crosses = {}
    for period in (9,20,21,50,100,200):
        crosses[f'price_ema{period}'] = cross_event(enriched.close,enriched[f'ema{period}'])
    for fast,slow in [(9,21),(20,50),(50,200)]:
        crosses[f'ema{fast}_ema{slow}'] = cross_event(enriched[f'ema{fast}'],enriched[f'ema{slow}'])
    for left,right in [('macd','macd_signal'),('stoch_rsi_k','stoch_rsi_d'),('plus_di14','minus_di14'),
                       ('close','bb_upper'),('close','bb_lower'),('close','sma50'),('close','sma200'),('close','vwap_session_utc')]:
        crosses[f'{left}_{right}'] = cross_event(enriched[left],enriched[right])
    for name, levels in {'rsi14':[30,50,70],'stoch_rsi_k':[20,80],'macd':[0], 'macd_hist':[0],
                         'adx14':[25],'mfi14':[20,80],'cmf20':[0]}.items():
        for level in levels:
            crosses[f'{name}_{level}'] = cross_event(enriched[name],level)
    # Pivots must only use completed bars even in the live indicator mode.
    structural = enriched if closed_only is None else enriched.loc[enriched.index.isin(closed_only.index)]
    fractals = fractal_pivots(structural,cfg.pivot_left,cfg.pivot_right)
    zz = zigzag_pivots(structural,structural.atr14)
    structure = {'fractal':pivot_summary(fractals),'atr_zigzag':pivot_summary(zz),
                 'divergences':divergences(structural,fractals,structural.atr14),
                 'wave_rule_flags':wave_rule_flags(zz),
                 'main_pivot_fibs':fib_levels(zz[-2]['price'],zz[-1]['price']) if len(zz)>1 else None}
    return {'asof_utc':df.index[-1].isoformat(), 'open':float(row.open),'high':float(row.high),
            'low':float(row.low),'close':float(row.close),'volume':float(row.volume),
            'trade_count':finite(row.get('trade_count')), 'indicators':indicators,'slopes':slopes,
            'cross_events':crosses, 'structure':structure,
            'ema_state':{f'price_vs_ema{p}_pct':None if indicators[f'ema{p}'] is None else (float(row.close)/indicators[f'ema{p}']-1)*100 for p in [20,50,200]},
            'warmup_unavailable':[k for k,v in indicators.items() if v is None],
            'source_rows':len(df), 'obv_origin_utc':df.index[0].isoformat()}
