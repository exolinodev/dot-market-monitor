"""Real Git/writer/paper CLI integration; no exchange or initialized real account."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ledger import digest
from ledger_market import market_events
from ledger_store import initialize, append_events, verify
from order_executor import materialize, load_published, preview, run_paper
from kraken_execution import ExecutionError
from test_ledger import EPOCH
from test_ledger_market import archive
from test_ledger_publication import git
from test_oracle_v4 import fixture, rebind
from oracle_submission import publish_submission


def published(repo):
    repo.mkdir()
    data = repo / 'data'
    _, rows = archive(data)
    rows[0]['sources']['perp_book']['received_at_utc'] = EPOCH
    path = data / 'intraday/2026/09/20.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    f, snapshot, _, cfg = fixture()
    initialize(data, cfg, EPOCH)
    events = [e for e in market_events(data, EPOCH, '2026-09-20T20:15:00Z') if e['at_utc'] == EPOCH and e['type'] != 'candle']
    state = append_events(data, events)
    f['ledger_state_sha256'] = digest(state)
    snapshot['markets']['DOTUSD']['execution_context']['ledger_state_sha256'] = digest(state)
    rebind(f, snapshot)
    (data / 'llm_snapshot.json').write_text(json.dumps(snapshot))
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'bound evidence', date=EPOCH)
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    git(repo, 'switch', '-c', 'writer')
    publish_submission({'schema_version': 1, 'snapshot_commit': base, 'forecast': f}, EPOCH, repo, archive_submission=True)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'validated plan', date='2026-09-20T20:00:10Z')
    git(repo, 'switch', 'main')
    git(repo, 'merge', '--no-ff', 'writer', '-m', 'publication', date='2026-09-20T20:04:30Z')
    head = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', head)
    return head, f['forecast_id']


def test_paper_candidate_ignores_worktree_changes_and_preserves_source_account(tmp_path):
    repo = tmp_path / 'repo'
    head, ident = published(repo)
    source_state = (repo / 'data/ledger/state.json').read_bytes()
    (repo / 'data/llm_snapshot.json').write_text('{}')
    (repo / 'data/intraday/2026/09/20.jsonl').write_text('invalid local changes')
    report = run_paper(repo, head, ident, '2026-09-20T20:15:00Z', tmp_path / 'candidate')
    assert report['mode'] == 'paper' and report['replay'] == 'identical'
    assert report['exchange_calls'] == 0 and not report['published']
    state = verify(tmp_path / 'candidate/data')['state']
    assert state['position']['opened_at_utc'] == '2026-09-20T20:05:00Z'
    assert (repo / 'data/ledger/state.json').read_bytes() == source_state
    with pytest.raises(ExecutionError, match='new directory'):
        run_paper(repo, head, ident, '2026-09-20T20:15:00Z', tmp_path / 'candidate')


def test_demo_preview_preserves_size_and_marks_time_without_authorizing(tmp_path):
    repo = tmp_path / 'repo'
    head, ident = published(repo)
    data = materialize(repo, head, tmp_path / 'view')
    bound = load_published(data, repo, head, ident)
    result = preview(data, repo, head, ident, '2026-09-20T20:04:59Z')
    assert not result['eligible_time'] and not result['authorizes_execution']
    assert result['entry_request']['size'] == bound['plan']['orders'][0]['size']['quantity']
    assert preview(data, repo, head, ident, '2026-09-20T20:05:00Z')['eligible_time']
    assert preview(data, repo, head, ident, '2026-09-21T00:00:00Z')['entry_expired']


def test_changed_committed_seed_evidence_fails_source_verification(tmp_path):
    repo = tmp_path / 'repo'
    head, ident = published(repo)
    path = repo / 'data/intraday/2026/09/20.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['perp_book']['spread_bps'] = 999
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'invalid rewrite', date='2026-09-20T20:06:00Z')
    wrong = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', wrong)
    with pytest.raises(ValueError, match='differs from archived'):
        run_paper(repo, wrong, ident, '2026-09-20T20:15:00Z', tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()


def test_cli_paper_and_disabled_exchange_modes(tmp_path):
    repo = tmp_path / 'repo'
    head, ident = published(repo)
    script = Path(__file__).parents[1] / 'scripts/execute_orders.py'
    args = [sys.executable, str(script), '--repo', str(repo), '--trusted-head', head, '--forecast-id', ident]
    result = subprocess.run(args + ['--mode', 'paper', '--boundary', '2026-09-20T20:15:00Z', '--output-dir', str(tmp_path/'output')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['replay'] == 'identical'
    for mode in ('demo', 'live'):
        result = subprocess.run(args + ['--mode', mode], capture_output=True, text=True)
        assert result.returncode == 2 and 'unavailable' in result.stderr
