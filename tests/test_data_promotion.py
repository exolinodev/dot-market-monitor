"""Regression checks for protected producer publication and failure boundaries."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('promote_data', ROOT / 'scripts/promote_data.py')
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


@pytest.mark.parametrize('kind,path', [
    ('oracle', 'src/main.py'), ('oracle', '.github/workflows/tests.yml'),
    ('oracle', 'data/llm_snapshot.json'), ('collector', 'data/oracle/forecasts/x.json'),
    ('collector', 'data/oracle/consumer/../../src/main.py'),
])
def test_producer_cannot_promote_code_or_another_producers_files(kind, path):
    assert not promotion.allowed(kind, path)


def test_git_tree_rejects_a_symlink_disguised_as_data(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('commit', '--allow-empty', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    path = tmp_path / 'data/oracle/receipts/bad.json'
    path.parent.mkdir(parents=True)
    path.symlink_to('../../../src/main.py')
    git('add', '.')
    git('commit', '-qm', 'symlink')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='regular'):
        promotion.validate_paths('oracle', base)


@pytest.fixture
def producer(monkeypatch):
    """Simulated GitHub transport; exercise orchestration without a real PR."""
    calls = []
    base, head = 'a' * 40, 'b' * 40
    state = {'committed': False, 'changed': True, 'remote_base': base, 'late_base': base,
             'pr_head': head, 'verified': False, 'merged': False, 'pr_created': False,
             'pr_state': 'open', 'check_conclusion': None, 'create_denied': False,
             'merge_denied': False, 'merge_response_lost': False, 'close_denied': False}
    env = {'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
           'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['git', 'commit']:
            state['committed'] = True
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return head if state['committed'] else base
        if args[:3] == ['git', 'rev-parse', 'origin/main']:
            return state['remote_base']
        if args[:3] == ['git', 'diff', '--cached']:
            return 'data/oracle/receipts/example.json' if state['changed'] else ''
        return ''
    def api(repo, path, method='GET', payload=None):
        calls.append((path, method, payload))
        if method != 'GET':
            assert state['verified'], 'External write before real test completion'
        if path == 'git/ref/heads/main':
            return {'object': {'sha': state['late_base']}}
        if path == 'pulls' and method == 'POST':
            if state['create_denied']:
                raise subprocess.CalledProcessError(1, ['gh', 'api', 'pulls'], stderr='HTTP 403')
            state['pr_created'] = True
        if path == 'check-runs' or path == 'check-runs/37':
            state['check_conclusion'] = payload['conclusion']
            return {'id': 37}
        if path == 'pulls/7' and method == 'PATCH':
            if state['close_denied']:
                raise subprocess.CalledProcessError(1, ['gh', 'api', 'pulls/7'])
            state['pr_state'] = payload['state']
        if path.endswith('/merge'):
            if state['merge_denied']:
                return {'merged': False}
            state['merged'] = True
            if state['merge_response_lost']:
                raise subprocess.CalledProcessError(1, ['gh', 'api', path])
            return {'merged': True, 'sha': 'c' * 40}
        pr = {'number': 7, 'head': {'sha': state['pr_head'], 'ref': 'automation/oracle-123-1'},
              'base': {'ref': 'main'}, 'html_url': 'https://github.com/owner/repo/pull/7',
              'mergeable': True, 'merged': state['merged'], 'merge_commit_sha': 'c' * 40}
        if path.startswith('pulls?'):
            return [pr] if state['pr_created'] else []
        return pr
    monkeypatch.setattr(promotion, 'run', run)
    monkeypatch.setattr(promotion, 'api', api)
    monkeypatch.setattr(promotion, 'validate_paths', lambda *args: None)
    monkeypatch.setattr(promotion.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(promotion, 'verify', lambda base: state.update(verified=True))
    return env, state, calls


def test_success_is_bound_to_the_tested_sha_and_normal_merge(producer):
    env, state, calls = producer
    assert promotion.promote('oracle', env)['merged']
    check = next(c[2] for c in calls if isinstance(c, tuple) and c[0] == 'check-runs')
    merge = next(c[2] for c in calls if isinstance(c, tuple) and c[0].endswith('/merge'))
    assert check['head_sha'] == merge['sha'] == 'b' * 40
    assert check['conclusion'] == 'success'
    assert [c for c in calls if isinstance(c, list) and c[:2] == ['git', 'push']] == [
        ['git', 'push', 'origin', 'HEAD:refs/heads/automation/oracle-123-1'],
        ['git', 'push', 'origin', '--force-with-lease=refs/heads/automation/oracle-123-1:' + 'b' * 40,
         ':refs/heads/automation/oracle-123-1']]
    assert state['merged'] and state['check_conclusion'] == 'success'
    assert calls[-2][0] == 'pulls/7'  # Merge readback precedes ref cleanup.


def test_failed_validation_never_publishes_success_or_merges(producer, monkeypatch):
    env, state, calls = producer
    def fail(base):
        raise subprocess.CalledProcessError(1, ['pytest'])
    monkeypatch.setattr(promotion, 'verify', fail)
    with pytest.raises(subprocess.CalledProcessError):
        promotion.promote('oracle', env)
    assert not any(isinstance(c, tuple) for c in calls)
    assert not any(c[:2] == ['git', 'push'] for c in calls)


def test_advancing_main_is_not_rebased_over(producer):
    env, state, calls = producer
    state['remote_base'] = 'd' * 40
    with pytest.raises(ValueError, match='Main advanced'):
        promotion.promote('oracle', env)
    assert not state['verified']
    assert not any(isinstance(c, tuple) for c in calls)


def test_pr_head_tampering_cannot_receive_test_attestation(producer):
    env, state, calls = producer
    state['pr_head'] = 'e' * 40
    with pytest.raises(ValueError, match='tested data commit'):
        promotion.promote('oracle', env)
    assert not any(isinstance(c, tuple) and c[0] == 'check-runs' for c in calls)


def test_no_changes_has_no_publication_effect(producer):
    env, state, calls = producer
    state['changed'] = False
    assert promotion.promote('collector', env) is None
    assert calls == [['git', 'rev-parse', 'HEAD'], ['git', 'diff', '--cached', '--name-only']]


def test_untrusted_branch_never_executes_producer(producer):
    env, state, calls = producer
    env['GITHUB_REF'] = 'refs/pull/7/merge'
    with pytest.raises(ValueError, match='trusted main'):
        promotion.promote('oracle', env)
    assert calls == []


def test_real_validation_command_failure_stops_following_checks(monkeypatch):
    calls = []
    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(promotion.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        promotion.verify('trusted-base')
    assert calls == [[sys.executable, 'scripts/validate_intraday.py', '--base', 'trusted-base']]


def test_main_advance_after_attestation_revokes_check_and_closes_pr(producer):
    env, state, calls = producer
    state['late_base'] = 'd' * 40
    with pytest.raises(ValueError, match='Main advanced after testing'):
        promotion.promote('oracle', env)
    assert state['check_conclusion'] == 'failure'
    assert state['pr_state'] == 'closed'
    assert not state['merged']
    assert calls[-1][-1] == ':refs/heads/automation/oracle-123-1'


def test_pr_creation_denied_removes_pushed_branch_without_attestation(producer):
    env, state, calls = producer
    state['create_denied'] = True
    with pytest.raises(subprocess.CalledProcessError):
        promotion.promote('oracle', env)
    assert state['check_conclusion'] is None and not state['merged']
    assert calls[-1][-1] == ':refs/heads/automation/oracle-123-1'


def test_denied_merge_does_not_leave_a_green_open_pr(producer):
    env, state, calls = producer
    state['merge_denied'] = True
    with pytest.raises(ValueError, match='merge did not succeed'):
        promotion.promote('oracle', env)
    assert state['check_conclusion'] == 'failure' and state['pr_state'] == 'closed'


def test_cleanup_continues_after_close_fails_and_preserves_original_error(producer, capsys):
    env, state, calls = producer
    state['late_base'] = 'd' * 40
    state['close_denied'] = True
    with pytest.raises(ValueError, match='Main advanced after testing'):
        promotion.promote('oracle', env)
    assert state['check_conclusion'] == 'failure'
    assert calls[-1][-1] == ':refs/heads/automation/oracle-123-1'
    assert 'cleanup requires attention' in capsys.readouterr().err


def test_lost_merge_response_does_not_revoke_a_successful_publication(producer):
    env, state, calls = producer
    state['merge_response_lost'] = True
    with pytest.raises(subprocess.CalledProcessError):
        promotion.promote('oracle', env)
    assert state['merged'] and state['check_conclusion'] == 'success'
    assert not any(isinstance(c, tuple) and c[1] == 'PATCH' for c in calls)
    assert calls[-1][-1] == ':refs/heads/automation/oracle-123-1'


def test_cleanup_outage_does_not_turn_a_confirmed_merge_into_a_failed_forecast(producer, monkeypatch, capsys):
    env, state, calls = producer
    def fail(*args):
        raise subprocess.CalledProcessError(1, ['git', 'push'])
    monkeypatch.setattr(promotion, 'delete_branch', fail)
    assert promotion.promote('oracle', env)['merged']
    assert state['check_conclusion'] == 'success'
    assert 'cleanup requires attention' in capsys.readouterr().err


@pytest.mark.parametrize('branch', ['main', 'oracle-submission/20260917T180158Z', 'automation/other-1-1'])
def test_cleanup_never_deletes_main_or_submission_evidence(branch):
    with pytest.raises(ValueError, match='restricted'):
        promotion.delete_branch(branch, 'b' * 40)


def test_leased_cleanup_refuses_new_work_and_deletes_only_the_expected_ref(tmp_path, monkeypatch):
    """Real Git remote: a concurrently updated ref cannot be accidentally erased."""
    remote, local = tmp_path / 'remote.git', tmp_path / 'local'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(local)], check=True)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=local, text=True).strip()
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('remote', 'add', 'origin', str(remote))
    git('commit', '--allow-empty', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    git('push', '-q', 'origin', 'main')
    branch = 'automation/collector-123-1'
    git('switch', '-qc', branch)
    git('commit', '--allow-empty', '-qm', 'tested data')
    tested = git('rev-parse', 'HEAD')
    git('push', '-q', 'origin', branch)
    git('commit', '--allow-empty', '-qm', 'concurrent change')
    changed = git('rev-parse', 'HEAD')
    git('push', '-q', 'origin', branch)
    monkeypatch.chdir(local)
    with pytest.raises(subprocess.CalledProcessError):
        promotion.delete_branch(branch, tested)
    assert git('ls-remote', 'origin', 'refs/heads/' + branch).startswith(changed)
    promotion.delete_branch(branch, changed)
    assert not git('ls-remote', 'origin', 'refs/heads/' + branch)
    assert git('ls-remote', 'origin', 'refs/heads/main').startswith(base)


def test_light_allowlist_excludes_gzip_snapshot_and_funding():
    assert promotion.allowed('light', 'data/intraday/2026/09/20.jsonl')
    assert promotion.allowed('light', 'data/intraday/latest.json')
    for path in ('data/raw/latest.json.gz', 'data/llm_snapshot.json', 'data/funding/2026/09.jsonl',
                 'data/intraday/../../src/main.py', 'data/intraday/script.py'):
        assert not promotion.allowed('light', path)


def test_light_requires_real_ledger_replay_and_market_evidence_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(promotion.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    promotion.verify_light('trusted-base')
    assert [sys.executable, 'scripts/validate_ledger.py', '--base', 'trusted-base'] in calls
    assert any('tests/test_ledger_runtime.py' in args for args in calls)
    for kind in ('collector', 'light'):
        for path in ('state.json', 'performance.json', 'events/2026/09/20.jsonl', 'trades/forecast-a.json'):
            assert promotion.allowed(kind, 'data/ledger/' + path)
        for path in ('genesis.json', 'plans/forecast-a.json', 'states/' + 'a' * 64 + '.json'):
            assert not promotion.allowed(kind, 'data/ledger/' + path)
