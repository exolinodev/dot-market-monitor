"""Git candidate evidence must remain append-only and match deterministic replay."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from validate_ledger import check
from ledger_store import append_events
from test_ledger import candle
from test_ledger_store import setup_account


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


def repository(tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Ledger Test')
    git(tmp_path, 'config', 'user.email', 'ledger@example.invalid')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'base')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    _, _, events, _ = setup_account(tmp_path / 'data')
    append_events(tmp_path / 'data', events[2:] + [candle(1)])
    git(tmp_path, 'add', 'data')
    return base


def test_guard_replays_index_not_unstaged_worktree(tmp_path):
    base = repository(tmp_path)
    path = tmp_path / 'data/ledger/state.json'
    good = path.read_bytes()
    path.write_text('{}')
    check(staged=True, root=tmp_path)  # Good index, broken worktree.
    git(tmp_path, 'add', 'data')
    path.write_bytes(good)
    with pytest.raises(ValueError, match='state differs'):
        check(staged=True, root=tmp_path)  # Broken index, repaired worktree.


def test_guard_rejects_immutable_change_and_journal_rewrite(tmp_path):
    base = repository(tmp_path)
    git(tmp_path, 'commit', '-m', 'ledger')
    check(base, root=tmp_path)
    genesis = tmp_path / 'data/ledger/genesis.json'
    original = genesis.read_bytes()
    genesis.write_bytes(original + b'\n')
    git(tmp_path, 'add', 'data')
    with pytest.raises(ValueError, match='Immutable ledger'):
        check(staged=True, root=tmp_path)
    genesis.write_bytes(original)
    journal = tmp_path / 'data/ledger/events/2026/09/20.jsonl'
    journal.write_bytes(b' ' + journal.read_bytes())
    git(tmp_path, 'add', 'data')
    with pytest.raises(ValueError, match='prefix changed'):
        check(staged=True, root=tmp_path)


def test_guard_accepts_append_and_rejects_deletion(tmp_path):
    base = repository(tmp_path)
    git(tmp_path, 'commit', '-m', 'ledger')
    append_events(tmp_path / 'data', [candle(2, high='1.061')])
    git(tmp_path, 'add', 'data')
    check(staged=True, root=tmp_path)
    git(tmp_path, 'commit', '-m', 'close trade')
    check(base, root=tmp_path)
    (tmp_path / 'data/ledger/state.json').unlink()
    git(tmp_path, 'add', 'data')
    with pytest.raises(ValueError, match='removed'):
        check(staged=True, root=tmp_path)
