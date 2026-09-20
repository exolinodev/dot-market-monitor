import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import pytest
from intraday import collect, persist, history, book_summary
from perp_data import parse_chart, append_funding, chart_request

FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 9, 20, 20, 30, 8, tzinfo=timezone.utc)
BOUNDARY = '2026-09-20T20:30:00Z'


def payload(name):
    return json.loads((FIXTURES / name).read_text())


def fixture_fetch(url, params):
    if '/trade/' in url or '/mark/' in url:
        kind = 'trade' if '/trade/' in url else 'mark'
        return payload(f'v4/kraken_{kind}_1m.json')
    if url.endswith('/OHLC'):
        m = params['interval']
        start = int(NOW.timestamp()) - 8 - 900
        return {'error': [], 'result': {'DOTUSD': [[t, '1', '1.1', '.9', '1', '1', '10', 3]
                for t in range(start, start + 900, m*60)], 'last': start+900}}
    if url.endswith('/Depth'):
        return payload('spot_depth.json')
    if url.endswith('/Trades'):
        return payload('spot_trades.json')
    if '/tickers/' in url:
        p = payload('futures_ticker.json'); p['serverTime'] = NOW.isoformat(); return p
    if url.endswith('/orderbook'):
        return payload('futures_book.json')
    if url.endswith('/history'):
        return payload('futures_trades.json')
    if '/coinbase.' in url:
        p = payload('coinbase_book.json'); p['time'] = NOW.isoformat(); return p
    raise AssertionError(url)


def test_light_is_small_create_once_and_never_rewrites_gzip(tmp_path):
    raw = tmp_path / 'raw'; raw.mkdir(); sentinel = raw / 'ohlc_cache.json.gz'; sentinel.write_bytes(b'unchanged')
    calls = []
    def fetch(url, params):
        calls.append(url); return fixture_fetch(url, params)
    row = collect(tmp_path, 'light', BOUNDARY, fetch, lambda: NOW)
    assert len(calls) == row['meta']['request_count'] == 12
    assert row['meta']['boundary_lag_seconds'] == 8
    assert len(row['candles']['trade']) == len(row['candles']['mark']) == 15
    assert sentinel.read_bytes() == b'unchanged'
    archive = tmp_path / 'intraday/2026/09/20.jsonl'
    assert archive.stat().st_size <= 25000
    before = archive.read_bytes()
    assert collect(tmp_path, 'light', BOUNDARY, lambda *_: pytest.fail('Duplicate fetch'), lambda: NOW) == row
    assert archive.read_bytes() == before
    changed = copy.deepcopy(row); changed['basis_bps'] = 42
    with pytest.raises(ValueError, match='already archived'): persist(tmp_path, changed)
    assert len(history(tmp_path, BOUNDARY)['missing_boundaries']) == 3
    assert row['tape']['perp']['complete'] is False or row['tape']['perp']['trades'] == 0


def test_failed_required_source_does_not_poison_immutable_quarter(tmp_path):
    def fail(url, params):
        if '/mark/' in url: raise RuntimeError('offline')
        return fixture_fetch(url, params)
    with pytest.raises(ValueError, match='not committed'):
        collect(tmp_path, 'light', BOUNDARY, fail, lambda: NOW)
    assert not (tmp_path / 'intraday/latest.json').exists()


def test_charts_overlap_and_close_at_reception_not_later():
    _, params = chart_request('trade', 1, '2026-09-20T20:15:32Z', BOUNDARY)
    assert params['from'] == int(datetime(2026, 9, 20, 20, 14, tzinfo=timezone.utc).timestamp())
    p = payload('v4/kraken_trade_1m.json')
    frame, more = parse_chart(p, 1, '2026-09-20T20:29:59Z', '2026-09-20T20:15:00Z', BOUNDARY)
    assert not more and len(frame) == 14
    assert frame.index[-1].minute == 28


def test_funding_appends_only_and_rejects_corrections(tmp_path):
    p = payload('v4/kraken_funding.json')
    summary = append_funding(tmp_path, p)
    assert summary['new_records'] == 8838 and len(summary['gaps']) == 6
    before = {p: p.read_bytes() for p in (tmp_path / 'funding').glob('*/*.jsonl')}
    assert append_funding(tmp_path, p)['new_records'] == 0
    assert all(path.read_bytes() == data for path, data in before.items())
    p['rates'][-1]['fundingRate'] = 42
    with pytest.raises(ValueError, match='correction'): append_funding(tmp_path, p)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_append_guard_rejects_prior_line_changes(tmp_path):
    spec = importlib.util.spec_from_file_location('validate_intraday', Path(__file__).parents[1] / 'scripts/validate_intraday.py')
    guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
    collect(tmp_path / 'data', 'light', BOUNDARY, fixture_fetch, lambda: NOW)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path).decode().strip()
    git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
    git('add', '.'); git('commit', '-qm', 'base'); base = git('rev-parse', 'HEAD')
    guard.check(base, root=tmp_path)
    path = tmp_path / 'data/intraday/2026/09/20.jsonl'
    path.write_text(path.read_text().replace('"schema_version":1', '"schema_version":2'))
    with pytest.raises(ValueError, match='Append-only'): guard.check(base, root=tmp_path)


def test_crossed_book_is_not_a_quote():
    with pytest.raises(ValueError): book_summary([[2, 10]], [[1, 10]])


def test_full_perp_cache_reloads_with_null_missing_fields(tmp_path, monkeypatch):
    import intraday
    from perp_data import enrich_hourly
    from common import read_json
    def fetch(url, params):
        if 'historicalfundingrates' in url: return payload('v4/kraken_funding.json')
        kind, resolution = url.split('/')[-3], url.split('/')[-1]
        return payload(f'v4/kraken_{kind}_{resolution}.json')
    monkeypatch.setattr(intraday, 'fetch', fetch)
    for _ in range(2):
        data = {'markets': {'DOTUSD': {'perp': {}}}, 'errors': [], 'status': 'ok'}
        enrich_hourly(data, tmp_path, BOUNDARY)
        assert not data['errors']
    cache = read_json(tmp_path / 'raw/ohlc_cache.json.gz')
    assert cache['DOTPERP.trade.1']
    assert cache['DOTPERP.trade.1'][0][5] is None  # VWAP is absent, not invented.
    assert data['markets']['DOTUSD']['perp']['historical_data']['funding']['new_records'] == 0
