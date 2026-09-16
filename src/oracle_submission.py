"""Create-only GitHub submissions, resolved from the immutable triggering commit."""
import json
from pathlib import Path
import re
import subprocess

from oracle_common import validate
from oracle_forecasts import persist_forecast


def full_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('Full commit SHA required')
    return value


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo)


def ancestor(repo, older, newer):
    git(repo, 'merge-base', '--is-ancestor', full_sha(older), newer)


def read_push_submission(event, event_sha, repo=Path('.')):
    """Only one newly created regular JSON file, never mutable checkout content."""
    before, after = full_sha(event['before']), full_sha(event['after'])
    if event.get('ref') != 'refs/heads/main' or after != full_sha(event_sha):
        raise ValueError('Submission must originate from the exact main push')
    ancestor(repo, before, after)
    ancestor(repo, after, 'origin/main')
    changes = git(repo, 'diff', '--no-renames', '--name-status', '-z', before, after,
                  '--', 'data/oracle/submissions').split(b'\0')
    if len(changes) != 3 or changes[0] != b'A' or changes[-1] != b'':
        raise ValueError('One create-only submission per push required')
    path = changes[1].decode('utf-8')
    match = re.fullmatch(r'data/oracle/submissions/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-oracle-v3)\.json', path)
    if not match:
        raise ValueError('Invalid submission path')
    if git(repo, 'log', '-1', '--format=%H', before, '--', path).strip():
        raise ValueError('Submission ID already existed in history')
    tree = git(repo, 'ls-tree', after, '--', path)
    if not tree.startswith(b'100644 blob '):
        raise ValueError('Submission must be a regular JSON file')
    envelope = json.loads(git(repo, 'show', after + ':' + path))
    validate(envelope, 'oracle_submission.schema.json')
    if envelope['forecast']['forecast_id'] != match[1]:
        raise ValueError('Submission filename must match forecast ID')
    # The model could only consume a snapshot that existed before submission.
    ancestor(repo, envelope['snapshot_commit'], before)
    return envelope


def publish_submission(envelope, now, repo=Path('.')):
    validate(envelope, 'oracle_submission.schema.json')
    commit = full_sha(envelope['snapshot_commit'])
    ancestor(repo, commit, 'origin/main')
    snapshot = json.loads(git(repo, 'show', commit + ':data/llm_snapshot.json'))
    return persist_forecast(envelope['forecast'], snapshot, repo / 'data/oracle', now)
