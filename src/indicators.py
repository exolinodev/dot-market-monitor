from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


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
    return result.where(session_start == session)


def _confirmed_pivots(df: pd.DataFrame, left: int, right: int) -> tuple[list[dict], list[dict]]:
    highs: list[dict] = []
    lows: list[dict] = []
    n = len(df)
    if n < left + right + 1:
        return highs, lows

    for i in range(left, n - right):
        h = float(df.iloc[i]["high"])
        l = float(df.iloc[i]["low"])
        left_h = df.iloc[i - left : i]["high"]
        right_h = df.iloc[i + 1 : i + right + 1]["high"]
        left_l = df.iloc[i - left : i]["low"]
        right_l = df.iloc[i + 1 : i + right + 1]["low"]

        if h > float(left_h.max()) and h >= float(right_h.max()):
            highs.append({"time": df.index[i].isoformat(), "price": h})
        if l < float(left_l.min()) and l <= float(right_l.min()):
            lows.append({"time": df.index[i].isoformat(), "price": l})

    return highs, lows


def _market_structure(df: pd.DataFrame, left: int, right: int) -> Dict[str, Any]:
    highs, lows = _confirmed_pivots(df, left, right)
    last_highs = highs[-2:]
    last_lows = lows[-2:]

    high_state: Optional[str] = None
    low_state: Optional[str] = None
    if len(last_highs) == 2:
        high_state = "HH" if last_highs[-1]["price"] > last_highs[-2]["price"] else "LH"
    if len(last_lows) == 2:
        low_state = "HL" if last_lows[-1]["price"] > last_lows[-2]["price"] else "LL"

    if high_state == "HH" and low_state == "HL":
        trend = "bullish"
    elif high_state == "LH" and low_state == "LL":
        trend = "bearish"
    else:
        trend = "mixed"

    return {
        "trend": trend,
        "high_state": high_state,
        "low_state": low_state,
        "last_pivot_high": highs[-1] if highs else None,
        "previous_pivot_high": highs[-2] if len(highs) >= 2 else None,
        "last_pivot_low": lows[-1] if lows else None,
        "previous_pivot_low": lows[-2] if len(lows) >= 2 else None,
    }


def _safe_float(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value)


def add_indicators(df: pd.DataFrame, cfg: IndicatorConfig, include_vwap: bool) -> pd.DataFrame:
    out = df.copy()
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
    out["volume_sma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["volume_std20"] = out["volume"].rolling(20, min_periods=20).std(ddof=0)
    out["volume_ratio20"] = out["volume"] / out["volume_sma20"].replace(0, np.nan)
    out["volume_z20"] = (out["volume"] - out["volume_sma20"]) / out["volume_std20"].replace(0, np.nan)
    if include_vwap:
        out["vwap_session_utc"] = anchored_daily_vwap_utc(out)
    else:
        out["vwap_session_utc"] = np.nan
    return out


def snapshot(df: pd.DataFrame, cfg: IndicatorConfig, include_vwap: bool) -> Dict[str, Any]:
    if df.empty:
        raise ValueError("Cannot snapshot empty dataframe")

    enriched = add_indicators(df, cfg, include_vwap)
    row = enriched.iloc[-1]
    structure = _market_structure(enriched, cfg.pivot_left, cfg.pivot_right)

    close = float(row["close"])
    ema20_v = _safe_float(row["ema20"])
    ema50_v = _safe_float(row["ema50"])
    ema200_v = _safe_float(row["ema200"])

    ema_state = {
        "price_vs_ema20_pct": None if ema20_v is None else (close / ema20_v - 1) * 100,
        "price_vs_ema50_pct": None if ema50_v is None else (close / ema50_v - 1) * 100,
        "price_vs_ema200_pct": None if ema200_v is None else (close / ema200_v - 1) * 100,
        "stack": None,
    }
    if ema20_v is not None and ema50_v is not None and ema200_v is not None:
        if close > ema20_v > ema50_v > ema200_v:
            ema_state["stack"] = "bullish"
        elif close < ema20_v < ema50_v < ema200_v:
            ema_state["stack"] = "bearish"
        else:
            ema_state["stack"] = "mixed"

    return {
        "asof_utc": enriched.index[-1].isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": close,
        "vwap": float(row["vwap"]) if "vwap" in row and not pd.isna(row["vwap"]) else None,
        "volume": float(row["volume"]),
        "trade_count": int(row["trade_count"]) if "trade_count" in row and not pd.isna(row["trade_count"]) else None,
        "indicators": {
            "rsi14": _safe_float(row["rsi14"]),
            "stoch_rsi": _safe_float(row["stoch_rsi"]),
            "stoch_rsi_k": _safe_float(row["stoch_rsi_k"]),
            "stoch_rsi_d": _safe_float(row["stoch_rsi_d"]),
            "macd": _safe_float(row["macd"]),
            "macd_signal": _safe_float(row["macd_signal"]),
            "macd_hist": _safe_float(row["macd_hist"]),
            "ema20": ema20_v,
            "ema50": ema50_v,
            "ema200": ema200_v,
            "dema20": _safe_float(row["dema20"]),
            "atr14": _safe_float(row["atr14"]),
            "session_vwap_utc": _safe_float(row["vwap_session_utc"]),
            "volume_sma20": _safe_float(row["volume_sma20"]),
            "volume_ratio20": _safe_float(row["volume_ratio20"]),
            "volume_z20": _safe_float(row["volume_z20"]),
        },
        "ema_state": ema_state,
        "structure": structure,
    }
