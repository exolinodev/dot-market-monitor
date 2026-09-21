"""Create-only GitHub submissions, resolved from the immutable triggering commit."""
from datetime import timedelta
import json
from pathlib import Path
import re
import subprocess

from observation_common import utc
from oracle_common import validate, digest
from oracle_forecasts import persist_forecast, validate_forecast, create_only


def strict_json(payload):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(payload, object_pairs_hook=unique)


def parse_submission(payload):
    """Reject partial or over-closed envelopes with a diagnosable message; never repair them."""
    raw = payload.encode() if isinstance(payload, str) else bytes(payload)
    try:
        return strict_json(raw)
    except ValueError as error:
        depth = raw.count(b'{') - raw.count(b'}')
        kind = 'truncated' if depth > 0 else 'over-closed' if depth < 0 else 'malformed'
        tail = raw[-16:].decode('utf-8', 'replace')
        raise ValueError(f'Submission JSON is {kind} ({len(raw)} bytes, brace depth {depth:+d}, '
                         f'ends with {tail!r}): {error}') from None


def accepted_at(opened, clock, max_queue_seconds=1800):
    """GitHub stamps when the draft was opened. That independent time anchors the
    creation-time check (`MAX_ACCEPTANCE_DELAY_SECONDS`), so runner queueing and setup cannot fail an honest
    submission while the consumer's own delay before opening the PR still counts."""
    opened, clock = utc(opened), utc(clock)
    if opened > clock + timedelta(seconds=5):
        raise ValueError('Draft opening time is ahead of the writer clock')
    if clock - opened > timedelta(seconds=max_queue_seconds):
        raise ValueError(f'Draft was opened {(clock-opened).total_seconds():.0f}s before the writer ran; stale queue')
    return opened


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
    match = re.fullmatch(r'data/oracle/submissions/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-oracle-v[34])\.json', path)
    if not match:
        raise ValueError('Invalid submission path')
    if git(repo, 'log', '-1', '--format=%H', before, '--', path).strip():
        raise ValueError('Submission ID already existed in history')
    tree = git(repo, 'ls-tree', after, '--', path)
    if not tree.startswith(b'100644 blob '):
        raise ValueError('Submission must be a regular JSON file')
    envelope = parse_submission(git(repo, 'show', after + ':' + path))
    validate(envelope, 'oracle_submission.schema.json')
    if envelope['forecast']['forecast_id'] != match[1]:
        raise ValueError('Submission filename must match forecast ID')
    # The model could only consume a snapshot that existed before submission.
    ancestor(repo, envelope['snapshot_commit'], before)
    return envelope


def read_pr_submission(event, repository, repo=Path('.')):
    """Read data only from a single-commit, same-repository draft; never run head code."""
    pr = event['pull_request']
    if (event.get('action') != 'opened' or not pr.get('draft') or
            pr['base']['ref'] != 'main' or
            pr['base']['repo']['full_name'] != repository or
            pr['head']['repo']['full_name'] != repository or
            not re.fullmatch(r'oracle-submission/[0-9]{8}T[0-9]{6}Z', pr['head']['ref'])):
        raise ValueError('Only a new same-repository Oracle draft PR is accepted')
    head = full_sha(pr['head']['sha'])
    parents = git(repo, 'rev-list', '--parents', '-n', '1', head).decode().split()
    if len(parents) != 2:
        raise ValueError('Submission must be a single-parent commit')
    parent = parents[1]
    ancestor(repo, parent, 'origin/main')
    ancestor(repo, full_sha(pr['base']['sha']), 'origin/main')
    # Inspect every changed path, including files outside the inbox. No head checkout.
    changes = git(repo, 'diff', '--no-renames', '--name-status', '-z', parent, head).split(b'\0')
    if len(changes) != 3 or changes[0] != b'A' or changes[-1] != b'':
        raise ValueError('Draft must add exactly one submission and no other files')
    path = changes[1].decode()
    match = re.fullmatch(r'data/oracle/submissions/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-oracle-v[34])\.json', path)
    if not match or not git(repo, 'ls-tree', head, '--', path).startswith(b'100644 blob '):
        raise ValueError('Draft must contain one regular submission JSON file')
    if git(repo, 'log', '-1', '--format=%H', 'origin/main', '--', path).strip():
        raise ValueError('Submission ID already existed in main history')
    envelope = parse_submission(git(repo, 'show', head + ':' + path))
    validate(envelope, 'oracle_submission.schema.json')
    if envelope['forecast']['forecast_id'] != match[1]:
        raise ValueError('Submission filename must match forecast ID')
    ancestor(repo, envelope['snapshot_commit'], parent)
    return envelope


def publish_submission(envelope, now, repo=Path('.'), archive_submission=False):
    validate(envelope, 'oracle_submission.schema.json')
    commit = full_sha(envelope['snapshot_commit'])
    ancestor(repo, commit, 'origin/main')
    snapshot = json.loads(git(repo, 'show', commit + ':data/llm_snapshot.json'))
    validate_forecast(envelope['forecast'], snapshot)
    ledger_args = {}
    if envelope['forecast'].get('schema_version') == 2:
        state = json.loads(git(repo, 'show', commit + ':data/ledger/state.json'))
        genesis = json.loads(git(repo, 'show', commit + ':data/ledger/genesis.json'))
        ledger_args = {'ledger_state': state, 'ledger_config': genesis['config']}
    path = persist_forecast(envelope['forecast'], snapshot, repo / 'data/oracle', now, **ledger_args)
    if archive_submission:
        submission_path = repo / 'data/oracle/submissions' / (envelope['forecast']['forecast_id'] + '.json')
        if submission_path.exists():
            if strict_json(submission_path.read_bytes()) != envelope:
                raise ValueError('Existing submission differs from validated envelope')
        else:
            create_only(submission_path, envelope)
    # A small readable receipt attests actual server-side decompression/hash verification.
    from oracle_receipts import make_receipt
    receipt = make_receipt(envelope, repo / 'data')
    create_only(repo / 'data/oracle/receipts' / (envelope['forecast']['forecast_id'] + '.json'), receipt)
    return path
