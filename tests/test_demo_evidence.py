"""Exercise acquisition/replay with synthetic authenticated-client substitutes."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from demo_evidence import capture_bundle, verify_bundle
from kraken_execution import READS, ExecutionError
from test_exchange_history import ACCOUNT, OTHER, START, END, DATE, body

KEY = hashlib.sha256(b'synthetic-key').hexdigest()


class Client:
    def __init__(self, account=ACCOUNT, continuation=None):
        self.account, self.continuation, self.calls = account, continuation, []

    def request(self, endpoint, params=None):
        assert endpoint in READS
        self.calls.append(endpoint)
        return 200, json.dumps({'result': 'success', 'serverTime': END,
                               READS[endpoint]: {} if endpoint == 'accounts' else []}).encode() + b' \n'

    def history(self, endpoint, params):
        assert endpoint in ('executions', 'orders', 'triggers')
        self.calls.append(endpoint)
        return 200, json.dumps(body([], token=self.continuation, account=self.account)).encode(), {'Date': DATE}


def test_private_capture_uses_only_reads_and_replays(tmp_path):
    root = tmp_path/'bundle'; client = Client()
    result = capture_bundle(root, client, ACCOUNT, KEY, START, END)
    manifest = verify_bundle(root, ACCOUNT, KEY, result['bundle_sha256'])
    assert client.calls == [*READS, 'executions', 'orders', 'triggers']
    assert manifest['history_coverage_complete'] and not manifest['atomic_snapshot']
    assert not manifest['authorizes_execution']
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o077 == 0 for p in root.rglob('*'))
    assert (root/'readback/accounts.raw').read_bytes().endswith(b' \n')
    with pytest.raises(FileExistsError):
        capture_bundle(root, client, ACCOUNT, KEY, START, END)
    assert len(client.calls) == 7


@pytest.mark.parametrize('change', ['raw', 'extra', 'symlink', 'manifest'])
def test_modified_bundle_rejected(tmp_path, change):
    root = tmp_path/'bundle'
    result = capture_bundle(root, Client(), ACCOUNT, KEY, START, END)
    if change == 'raw': (root/'readback/accounts.raw').write_bytes(b'{}')
    elif change == 'extra': (root/'extra').write_text('unbound')
    elif change == 'symlink': (root/'link').symlink_to(root/'readback/accounts.raw')
    else: (root/'bundle.json').write_text('{}')
    with pytest.raises(ExecutionError): verify_bundle(root, ACCOUNT, KEY, result['bundle_sha256'])


def test_account_and_key_cannot_be_rebound(tmp_path):
    root = tmp_path/'bundle'
    result = capture_bundle(root, Client(), ACCOUNT, KEY, START, END)
    for account, key in [(OTHER, KEY), (ACCOUNT, 'a'*64)]:
        with pytest.raises(ExecutionError, match='identity'):
            verify_bundle(root, account, key, result['bundle_sha256'])
    with pytest.raises(ExecutionError, match='different account'):
        capture_bundle(tmp_path/'wrong', Client(OTHER), ACCOUNT, KEY, START, END)
    assert not (tmp_path/'wrong/bundle.json').exists()
    assert (tmp_path/'wrong/executions/0000.raw').exists()


def test_incomplete_history_is_retained_without_coverage_claim(tmp_path):
    root = tmp_path/'bundle'
    result = capture_bundle(root, Client(continuation='next'), ACCOUNT, KEY, START, END, max_pages=1)
    assert not result['history_coverage_complete']
    assert not verify_bundle(root, ACCOUNT, KEY, result['bundle_sha256'])['history_coverage_complete']


def test_umask_restored_on_failure(tmp_path):
    prior = os.umask(0o027)
    try:
        with pytest.raises(ExecutionError): capture_bundle(tmp_path/'bad', Client(OTHER), ACCOUNT, KEY, START, END)
        observed = os.umask(0o027)
        assert observed == 0o027
    finally: os.umask(prior)


def test_cli_refuses_repository_output_before_credentials_or_network(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(repo/'scripts/capture_demo_evidence.py'),
        '--account-uid', ACCOUNT, '--since', START, '--through', END,
        '--output-dir', str(repo/'private-demo-test')], capture_output=True, text=True, env={})
    assert result.returncode != 0 and 'outside a Git worktree' in result.stderr
    assert not (repo/'private-demo-test').exists()
