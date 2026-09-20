from copy import deepcopy
import json
import pytest
from ledger_runtime import advance
from ledger_store import initialize, verify
from test_ledger import config
from test_ledger_market import archive


def test_runtime_advances_closed_evidence_idempotently_without_implicit_account(tmp_path):
    epoch, rows = archive(tmp_path)
    with pytest.raises(FileNotFoundError):
        advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z')
    initialize(tmp_path, config(), epoch)
    summary = advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z')
    assert summary['equity_usd'] == '5000' and summary['asof_boundary_utc'] == '2026-09-20T20:15:00Z'
    before = {p: p.read_bytes() for p in (tmp_path / 'ledger').rglob('*') if p.is_file()}
    assert advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z') == summary
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert verify(tmp_path)['state']['spread']['bps'] == '3'


def test_runtime_refuses_missing_tail_before_mutating_account(tmp_path):
    epoch, rows = archive(tmp_path)
    initialize(tmp_path, config(), epoch)
    before = (tmp_path / 'ledger/state.json').read_bytes()
    with pytest.raises(ValueError, match='final closed market minute'):
        advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:30:00Z')
    assert (tmp_path / 'ledger/state.json').read_bytes() == before


def test_pending_quarter_can_be_enriched_without_self_referential_input_hash(tmp_path):
    epoch, rows = archive(tmp_path)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    path.write_text(json.dumps(rows[0]) + '\n')
    initialize(tmp_path, config(), epoch)
    pending = [('intraday/2026/09/20.jsonl', rows[1])]
    summary = advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z', pending)
    rows[1]['ledger'] = summary
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z') == summary


def test_git_guard_rechecks_market_provenance(tmp_path):
    import sys
    from pathlib import Path
    from test_ledger_guard import git
    sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
    from validate_ledger import check
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'base')
    epoch, rows = archive(tmp_path / 'data')
    initialize(tmp_path / 'data', config(), epoch)
    advance(tmp_path / 'data', tmp_path, 'a' * 40, '2026-09-20T20:15:00Z')
    git(tmp_path, 'add', 'data')
    check(staged=True, root=tmp_path)
    # Journal still replays internally, but altered external evidence no longer
    # proves its spread input. This must fail before protected publication.
    rows[0]['perp_book']['spread_bps'] = 5
    path = tmp_path / 'data/intraday/2026/09/20.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    git(tmp_path, 'add', 'data')
    with pytest.raises(ValueError, match='differs from archived'):
        check(staged=True, root=tmp_path)


def test_runtime_rejects_initial_middle_gap_even_when_tail_exists(tmp_path):
    epoch, rows = archive(tmp_path)
    for kind in ('trade', 'mark'):
        rows[1]['candles'][kind].pop(5)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    initialize(tmp_path, config(), epoch)
    before = (tmp_path / 'ledger/state.json').read_bytes()
    with pytest.raises(ValueError, match='continuous closed'):
        advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:15:00Z')
    assert (tmp_path / 'ledger/state.json').read_bytes() == before


def test_execution_context_binds_account_and_expires_quotes(tmp_path):
    from ledger_runtime import execution_context, finish_quarter
    from ledger import digest
    epoch, rows = archive(tmp_path)
    rows[1]['perp_book'].update(bid=1, ask=1.001)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    path.write_text(json.dumps(rows[0]) + '\n')
    initialize(tmp_path, config(), epoch)
    finish_quarter(tmp_path, rows[1], '2026-09-20T20:15:00Z', tmp_path, 'a' * 40)
    context = execution_context(tmp_path, rows[1], '2026-09-20T20:15:00Z', '2026-09-20T20:15:10Z')
    assert context['status'] == 'ok'
    assert context['ledger_state_sha256'] == digest(context['ledger_state'])
    assert context['quote']['bid'] == '1'
    assert context['estimated_taker_round_trip_bps'] == '109'
    assert execution_context(tmp_path, rows[1], '2026-09-20T20:15:00Z', '2026-09-20T20:31:00Z')['status'] == 'unavailable'


def test_main_light_run_advances_account_and_archives_one_enriched_quarter(tmp_path, monkeypatch):
    import main
    import intraday
    epoch, rows = archive(tmp_path)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    path.write_text(json.dumps(rows[0]) + '\n')
    rows[1]['meta']['status'] = 'ok'
    initialize(tmp_path, config(), epoch)
    calls = []
    def collect(directory, kind, boundary, **kwargs):
        calls.append(kwargs)
        return deepcopy(rows[1]) if intraday.read_cycle(directory, boundary) is None else intraday.read_cycle(directory, boundary)
    monkeypatch.setattr(intraday, 'collect', collect)
    result = main.main(tmp_path, 'light', '2026-09-20T20:15:00Z')
    assert calls == [{'persist_result': False}]
    assert result['ledger']['status'] == 'ok'
    assert verify(tmp_path)['state']['last_candle_utc'] == '2026-09-20T20:14:00Z'
    before = path.read_bytes()
    main.main(tmp_path, 'light', '2026-09-20T20:15:00Z')
    assert path.read_bytes() == before


def test_main_full_exports_verifiable_execution_context_and_consumer(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import main
    import intraday
    import perp_data
    from cycles import utc
    from ledger import digest
    from pipeline import Collector
    from output import validate_snapshot
    from test_main import Offline
    from test_ledger_market import quarter
    epoch, rows = archive(tmp_path)
    for start, end in [('20:15', '20:30'), ('20:30', '20:45'), ('20:45', '21:00')]:
        row = quarter('2026-09-20T' + start + ':00Z', '2026-09-20T' + end + ':00Z', '2026-09-20T' + end + ':08Z', 4)
        rows.append(row)
    current = rows.pop()
    current['meta']['status'] = 'ok'
    current['perp_book'].update(bid=.9998, ask=1.0002)
    path = tmp_path / 'intraday/2026/09/20.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    initialize(tmp_path, config(), epoch)
    monkeypatch.setattr('pipeline.CoinbaseClient', Offline)
    monkeypatch.setattr('pipeline.utcnow', lambda: utc('2026-09-20T21:00:10Z'))
    data = Collector(tmp_path, Offline(), Offline()).collect()
    data['generated_at_utc'] = '2026-09-20T21:00:10Z'
    data['generated_at_unix'] = utc(data['generated_at_utc']).timestamp()
    data['collection_started_at_utc'] = '2026-09-20T21:00:08Z'
    monkeypatch.setattr(main, 'Collector', lambda directory: SimpleNamespace(collect=lambda: data))
    monkeypatch.setattr(intraday, 'collect', lambda *args, **kwargs: current)
    def enrich(data, directory, boundary, pending_quarter=None):
        assert pending_quarter is current
        data['markets']['DOTUSD']['intraday'] = {'quarters': [None, None, None, dict(current)]}
    monkeypatch.setattr(perp_data, 'enrich_hourly', enrich)
    main.main(tmp_path, 'full', '2026-09-20T21:00:00Z')
    snapshot = json.loads((tmp_path / 'llm_snapshot.json').read_text())
    validate_snapshot(snapshot)
    context = snapshot['markets']['DOTUSD']['execution_context']
    assert context['status'] == 'ok' and context['ledger_state_sha256'] == digest(context['ledger_state'])
    assert context['ledger_state']['last_candle_utc'] == '2026-09-20T20:59:00Z'
    manifest = json.loads((tmp_path / 'oracle/consumer/index.json').read_text())
    assert any(p['section'] == 'execution' for p in manifest['parts'])
    context['ledger_state']['cash_usd'] = '50000'
    with pytest.raises(ValueError, match='ledger state hash mismatch'):
        validate_snapshot(snapshot)
