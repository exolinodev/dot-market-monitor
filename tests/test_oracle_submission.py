"""Real Git routing plus the production publisher, without a network or clock bypass."""
import copy
import json
from pathlib import Path
import subprocess

from jsonschema import ValidationError
import pytest

from oracle_common import validate
from oracle_submission import read_push_submission, publish_submission

ROOT = Path(__file__).parents[1]


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()


def commit(repo):
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'Test input')
    sha = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', sha)
    return sha


@pytest.fixture
def submission(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    # A genuine archived snapshot and original, unmodified browser test forecast.
    # The test clock is historical; no production publication is performed.
    sample = ROOT / 'docs/evaluation/oracle-v3/consumer-smoke'
    f = json.loads((sample / 'browser_forecast.json').read_text())
    import gzip
    s = json.loads(gzip.decompress((sample / 'llm_snapshot.json.gz').read_bytes()))
    path = tmp_path / 'data/llm_snapshot.json'
    path.parent.mkdir()
    path.write_text(json.dumps(s))
    before = commit(tmp_path)
    envelope = {'schema_version': 1, 'snapshot_commit': before, 'forecast': f}
    inbox = tmp_path / 'data/oracle/submissions' / (f['forecast_id'] + '.json')
    inbox.parent.mkdir(parents=True)
    inbox.write_text(json.dumps(envelope))
    after = commit(tmp_path)
    event = {'before': before, 'after': after, 'ref': 'refs/heads/main'}
    return tmp_path, inbox, envelope, event


def test_exact_push_to_immutable_forecast_and_input(submission):
    repo, inbox, expected, event = submission
    # A dirty checkout must not change the bytes consumed from the triggering SHA.
    inbox.write_text('{}')
    actual = read_push_submission(event, event['after'], repo)
    assert actual == expected
    f = actual['forecast']
    path = publish_submission(actual, f['created_at_utc'], repo)
    assert json.loads(path.read_text()) == f
    assert (repo / 'data/oracle/inputs' / (f['snapshot_sha256'] + '.json.gz')).exists()
    with pytest.raises(FileExistsError):
        publish_submission(actual, f['created_at_utc'], repo)


def test_publication_still_rejects_old_timestamp_and_wrong_snapshot(submission):
    repo, _, envelope, _ = submission
    with pytest.raises(ValueError, match='timestamp'):
        publish_submission(envelope, '2026-09-17T00:00:00Z', repo)
    bad = copy.deepcopy(envelope)
    bad['forecast']['measurement_config_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Measurement config mismatch'):
        publish_submission(bad, bad['forecast']['created_at_utc'], repo)


@pytest.mark.parametrize('kind', ['modified', 'deleted', 'renamed', 'multiple', 'mismatched_id',
                                  'snapshot_after_submission', 'symlink', 'extra_field'])
def test_rejects_mutation_and_invalid_routing(submission, kind):
    repo, inbox, envelope, event = submission
    if kind in ('modified', 'deleted', 'renamed'):
        event['before'] = event['after']
    if kind == 'modified':
        inbox.write_text('{}')
    elif kind == 'deleted':
        inbox.unlink()
    elif kind == 'renamed':
        inbox.rename(inbox.with_name('renamed.json'))
    elif kind == 'multiple':
        inbox.with_name('second.json').write_text('{}')
    elif kind == 'mismatched_id':
        inbox.rename(inbox.with_name('20260916T202329Z-f63a9647d907-oracle-v3.json'))
    elif kind == 'snapshot_after_submission':
        envelope['snapshot_commit'] = event['after']
        inbox.write_text(json.dumps(envelope))
    elif kind == 'symlink':
        inbox.unlink()
        inbox.symlink_to('../../llm_snapshot.json')
    else:
        envelope['extra'] = 'not allowed'
        inbox.write_text(json.dumps(envelope))
    event['after'] = commit(repo)
    with pytest.raises((ValueError, subprocess.CalledProcessError, ValidationError)):
        read_push_submission(event, event['after'], repo)


@pytest.mark.parametrize('kind', ['branch', 'sha', 'short_sha', 'new_snapshot'])
def test_rejects_wrong_origin(submission, kind):
    repo, _, _, event = submission
    supplied_sha = event['after']
    if kind == 'branch':
        event['ref'] = 'refs/heads/oracle-v3'
    elif kind == 'sha':
        supplied_sha = event['before']
    elif kind == 'short_sha':
        event['before'] = event['before'][:7]
    else:
        git(repo, 'update-ref', 'refs/remotes/origin/main', event['before'])
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        read_push_submission(event, supplied_sha, repo)


def test_submission_schema_is_strict(submission):
    _, _, envelope, _ = submission
    assert validate(envelope, 'oracle_submission.schema.json')
    envelope['forecast']['self_graded_success'] = True
    with pytest.raises(ValidationError):
        validate(envelope, 'oracle_submission.schema.json')


def test_deleted_submission_id_cannot_be_reused(submission):
    repo, inbox, envelope, event = submission
    inbox.unlink()
    event['before'] = commit(repo)
    inbox.write_text(json.dumps(envelope))
    event['after'] = commit(repo)
    with pytest.raises(ValueError, match='already existed'):
        read_push_submission(event, event['after'], repo)
