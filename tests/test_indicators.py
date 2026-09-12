import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from indicators import IndicatorConfig, add_indicators, snapshot  # noqa: E402


def sample_df(n=260):
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    base = np.linspace(1.0, 2.0, n)
    wobble = np.sin(np.arange(n) / 7) * 0.03
    close = base + wobble
    return pd.DataFrame(
        {
            "open": close - 0.005,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "vwap": close,
            "volume": np.linspace(1000, 2000, n),
            "trade_count": np.arange(n) + 1,
        },
        index=idx,
    )


def test_indicators_are_finite_after_warmup():
    df = sample_df()
    out = add_indicators(df, IndicatorConfig(), include_vwap=True)
    row = out.iloc[-1]
    for col in ["rsi14", "macd", "macd_signal", "macd_hist", "ema20", "ema50", "ema200", "dema20", "atr14"]:
        assert np.isfinite(row[col])


def test_snapshot_has_expected_sections():
    snap = snapshot(sample_df(), IndicatorConfig(), include_vwap=True)
    assert "indicators" in snap
    assert "structure" in snap
    assert "ema_state" in snap
    assert snap["indicators"]["rsi14"] is not None


def test_wilder_rsi_matches_reference_values():
    from indicators import rsi
    # Wilder worksheet: first average gain/loss use the initial 14 changes.
    close = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                       45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
                       46.03, 46.41, 46.22, 45.64, 46.21])
    result = rsi(close)
    np.testing.assert_allclose(result.iloc[14:17], [70.464135, 66.249619, 66.480942], atol=0.000001)
    assert result.iloc[:14].isna().all()


def test_session_vwap_uses_exchange_volume_weighted_price():
    from indicators import anchored_daily_vwap_utc
    df = sample_df(3)
    df['vwap'] = [10, 20, 30]
    df['volume'] = [1, 3, 2]
    result = anchored_daily_vwap_utc(df)
    np.testing.assert_allclose(result, [10, 17.5, 130 / 6])


def test_session_vwap_waits_for_complete_session_and_resets_at_midnight():
    from indicators import anchored_daily_vwap_utc
    df = sample_df(26).iloc[1:]
    df['vwap'] = 10.0
    result = anchored_daily_vwap_utc(df)
    assert result.iloc[:23].isna().all()
    assert result.iloc[23] == 10.0
