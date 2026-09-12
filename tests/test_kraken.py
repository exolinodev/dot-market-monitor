from datetime import datetime, timezone

import pandas as pd

from kraken import trade_flow


def test_trade_flow_uses_current_time_and_reports_truncation():
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"time": pd.Timestamp(now) - pd.Timedelta(minutes=10), "side": "s", "volume": 9, "price": 2},
        {"time": pd.Timestamp(now) - pd.Timedelta(minutes=2), "side": "b", "volume": 3, "price": 2},
    ])
    result = trade_flow(df, now_utc=now)
    assert result["5m"]["delta"] == 3
    assert result["5m"]["window_complete"] is True
    assert result["15m"]["delta"] == -6
    assert result["15m"]["window_complete"] is False


def test_old_trades_do_not_look_like_current_flow():
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"time": pd.Timestamp(now) - pd.Timedelta(hours=2), "side": "b", "volume": 30, "price": 2},
    ])
    result = trade_flow(df, now_utc=now)
    assert result["5m"]["trade_count"] == 0
    assert result["60m"]["delta"] == 0
