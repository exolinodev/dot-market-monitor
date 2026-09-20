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
