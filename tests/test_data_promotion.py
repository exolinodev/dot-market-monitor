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
    state = {'committed': False, 'changed': True, 'remote_base': base,
             'pr_head': head, 'verified': False}
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
            return {'object': {'sha': state['remote_base']}}
        if path.endswith('/merge'):
            return {'merged': True, 'sha': 'c' * 40}
        return {'number': 7, 'head': {'sha': state['pr_head']}, 'base': {'ref': 'main'},
                'html_url': 'https://github.com/owner/repo/pull/7', 'mergeable': True,
                'merged': True, 'merge_commit_sha': 'c' * 40}
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
        ['git', 'push', 'origin', 'HEAD:refs/heads/automation/oracle-123-1']]


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
    assert calls == [[sys.executable, 'scripts/check_oracle_archive.py', '--base', 'trusted-base']]
