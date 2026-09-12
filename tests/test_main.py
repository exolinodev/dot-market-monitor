import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import main
from indicators import IndicatorConfig
from test_indicators import sample_df


def test_summary_handles_failed_timeframe():
    data = {
        'generated_at_utc': '2026-09-12T12:00:00+00:00',
        'markets': {'DOTUSD': {'timeframes': {'1m': {'error': 'API unavailable'}}}, 'BTCUSD': {}},
        'errors': [{'component': 'dot_ohlc_1m', 'error': 'API unavailable'}],
    }
    summary = main.markdown_summary(data)
    assert 'API unavailable' in summary
    assert '1m' in summary


def test_history_requires_a_point_near_the_requested_age(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'HISTORY', tmp_path / 'history.json')
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    main.HISTORY.write_text(json.dumps([{'unix': now.timestamp() - 3600, 'btc_dominance': 58}]))
    result = main.update_history(now, {'btc_dominance': 59}, None)
    assert result['btc_dominance_change_24h_pp'] is None
    assert result['btc_dominance_change_7d_pp'] is None


def test_history_uses_real_24h_and_7d_points(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'HISTORY', tmp_path / 'history.json')
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    main.HISTORY.write_text(json.dumps([
        {'unix': now.timestamp() - 7 * 86400, 'btc_dominance': 55},
        {'unix': now.timestamp() - 86400, 'btc_dominance': 58},
    ]))
    result = main.update_history(now, {'btc_dominance': 59}, None)
    assert result['btc_dominance_change_24h_pp'] == 1
    assert result['btc_dominance_change_7d_pp'] == 4


def test_json_safe_removes_numpy_nonfinite_values():
    result = main.json_safe({'values': [np.float32('nan'), np.float64('inf'), np.int64(3)]})
    assert json.dumps(result, allow_nan=False) == '{"values": [null, null, 3]}'


def test_closed_snapshot_excludes_open_candle():
    df = sample_df()
    class Client:
        def ohlc(self, pair, interval):
            return df
    result = main.tf_snapshot(Client(), 'DOTUSD', 60, IndicatorConfig())
    assert result['live']['asof_utc'] == df.index[-1].isoformat()
    assert result['last_closed']['asof_utc'] == df.index[-2].isoformat()
    assert result['last_closed']['close'] == df.iloc[-2]['close']


def test_failed_collection_preserves_published_data(tmp_path, monkeypatch):
    for name, filename in [('LATEST', 'latest.json'), ('SUMMARY', 'latest.md'), ('HISTORY', 'history.json')]:
        path = tmp_path / filename
        path.write_text('[]' if name == 'HISTORY' else 'previous valid snapshot')
        monkeypatch.setattr(main, name, path)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    class Unavailable:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError('offline test')
            return fail
    monkeypatch.setattr(main, 'KrakenClient', Unavailable)
    monkeypatch.setattr(main, 'CoinGeckoClient', Unavailable)
    with pytest.raises(RuntimeError, match='Core market data'):
        main.main()
    assert main.LATEST.read_text() == 'previous valid snapshot'
    assert main.SUMMARY.read_text() == 'previous valid snapshot'
    assert main.HISTORY.read_text() == '[]'
