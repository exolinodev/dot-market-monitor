from copy import deepcopy
from datetime import timedelta
import importlib.util
from pathlib import Path
import pytest

from cycles import utc
from intraday import read_cycle
from intraday_recovery import historical_quarter, recover_missing_quarters
from ledger_runtime import advance
from ledger_store import initialize, verify
from test_intraday import fixture_fetch, NOW, BOUNDARY
from test_ledger import config
from test_ledger_market import archive


def setup(tmp_path):
    epoch, _ = archive(tmp_path)
    initialize(tmp_path, config(), epoch)
    advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z')


def test_recovery_is_explicit_historical_evidence_without_invented_quotes(tmp_path):
    spec = importlib.util.spec_from_file_location('validate_intraday', Path(__file__).parents[1] / 'scripts/validate_intraday.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    row = historical_quarter(BOUNDARY, fixture_fetch, lambda: NOW + timedelta(hours=9))
    guard.validate_record(row)
    assert row['meta']['fresh'] is False and row['meta']['late'] is True
    assert row['meta']['status'] == 'partial' and row['meta']['request_count'] == 2
    assert row['meta']['recovery'] == 'historical_candles_only'
    assert row['perp_book'] is None and row['tape'] == {'spot': None, 'perp': None}
    assert row['sources']['perp_book']['status'] == 'unavailable'
    assert len(row['candles']['trade']) == len(row['candles']['mark']) == 15
    assert row['sources']['trade']['received_at_utc'] == '2026-09-21T05:30:08Z'


def test_missed_quarter_recovers_and_ledger_replays_without_rewriting_history(tmp_path):
    setup(tmp_path)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    before = path.read_bytes()
    ledger_before = (tmp_path / 'ledger/state.json').read_bytes()
    count = recover_missing_quarters(tmp_path, '2026-09-20T20:45:00Z', fixture_fetch, lambda: NOW)
    assert count == 1 and path.read_bytes().startswith(before)
    assert (tmp_path / 'ledger/state.json').read_bytes() == ledger_before
    assert read_cycle(tmp_path, BOUNDARY)['meta']['fresh'] is False
    summary = advance(tmp_path, tmp_path, 'a' * 40, BOUNDARY)
    assert summary['status'] == 'ok'
    assert verify(tmp_path)['state']['last_candle_utc'] == '2026-09-20T20:29:00Z'
    recovered = path.read_bytes()
    assert recover_missing_quarters(tmp_path, '2026-09-20T20:45:00Z',
                                    lambda *_: pytest.fail('duplicate fetch'), lambda: NOW) == 0
    assert path.read_bytes() == recovered


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'pagination', 'network'])
def test_incomplete_historical_evidence_never_mutates_archive_or_ledger(tmp_path, failure):
    setup(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def fetch(url, params):
        if failure == 'network':
            raise RuntimeError('offline')
        value = deepcopy(fixture_fetch(url, params))
        if '/mark/' in url:
            if failure == 'missing':
                stamp = int((utc(BOUNDARY) - timedelta(minutes=5)).timestamp()) * 1000
                value['candles'] = [r for r in value['candles'] if r['time'] != stamp]
            elif failure == 'duplicate':
                value['candles'].append(value['candles'][5])
            else:
                value['more_candles'] = True
        return value
    with pytest.raises((ValueError, RuntimeError)):
        recover_missing_quarters(tmp_path, '2026-09-20T20:45:00Z', fetch, lambda: NOW)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_recovery_is_bounded_and_rejects_open_quarters(tmp_path):
    setup(tmp_path)
    with pytest.raises(ValueError, match='48-hour'):
        recover_missing_quarters(tmp_path, '2026-09-23T20:45:00Z', lambda *_: pytest.fail('unbounded fetch'))
    with pytest.raises(ValueError, match='closed quarter'):
        historical_quarter(BOUNDARY, lambda *_: pytest.fail('future fetch'), lambda: utc(BOUNDARY) - timedelta(seconds=30))


def test_multi_quarter_failure_does_not_publish_partial_recovery(tmp_path):
    setup(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        # Fixture covers only the first missing quarter; the second fails.
        recover_missing_quarters(tmp_path, '2026-09-20T21:00:00Z', fixture_fetch, lambda: NOW + timedelta(hours=1))
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
