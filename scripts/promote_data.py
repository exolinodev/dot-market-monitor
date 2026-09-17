#!/usr/bin/env python3
"""Test trusted producer data, then merge a SHA-bound PR under normal main rules.

GITHUB_TOKEN PRs do not trigger pull_request CI. This producer therefore executes
the same tests itself and records their real result on the exact data commit.
It never publishes a successful check before validation, or pushes main directly.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


COLLECTOR_FILES = {
    'data/latest.json', 'data/latest.md', 'data/llm_snapshot.json',
    'data/history.json', 'data/raw/latest.json.gz', 'data/raw/ohlc_cache.json.gz',
    'data/raw/observation_history.json.gz', 'data/raw/oracle_feature_history.json.gz',
    'data/raw/oracle_market_outcomes.json.gz', 'data/oracle_scorecard.json',
    'data/oracle/pending_outcomes.json',
}
PREFIXES = {
    'collector': ('data/oracle/consumer/', 'data/oracle/outcomes/', 'data/oracle/outcome_inputs/'),
    'oracle': tuple('data/oracle/' + p + '/' for p in
                    ('forecasts', 'inputs', 'forecast_keys', 'submissions', 'receipts')),
}


def run(args, *, payload=None):
    result = subprocess.run(args, input=None if payload is None else json.dumps(payload),
                            text=True, stdout=subprocess.PIPE, check=True)
    return result.stdout.strip()


def api(repo, path, method='GET', payload=None):
    args = ['gh', 'api', '--method', method, f'repos/{repo}/{path}']
    if payload is not None:
        args += ['--input', '-']
    output = run(args, payload=payload)
    return json.loads(output) if output else None


def allowed(kind, path):
    return (kind == 'collector' and path in COLLECTOR_FILES) or any(
        path.startswith(prefix) and '..' not in Path(path).parts
        for prefix in PREFIXES[kind])


def validate_paths(kind, base, head='HEAD'):
    paths = run(['git', 'diff', '--no-renames', '--name-only', '-z', base, head]).split('\0')
    for path in filter(None, paths):
        if not allowed(kind, path):
            raise ValueError('Producer changed a non-allowlisted path: ' + path)
        entry = run(['git', 'ls-tree', head, '--', path])
        if entry and not entry.startswith('100644 blob '):
            raise ValueError('Producer data must be regular non-executable files: ' + path)


def verify(base):
    commands = [
        [sys.executable, 'scripts/check_oracle_archive.py', '--base', base],
        [sys.executable, 'scripts/validate_snapshot.py'],
        [sys.executable, '-m', 'pytest', '-q'],
        ['node', '--test', 'tests/scheduler.test.mjs'],
    ]
    for command in commands:
        # Inherit output so the producer run is the actual test evidence.
        subprocess.run(command, check=True)


def promote(kind, env=None):
    env = os.environ if env is None else env
    if env.get('GITHUB_REF') != 'refs/heads/main' or env.get('GITHUB_EVENT_NAME') not in {
        'schedule', 'push', 'workflow_dispatch', 'pull_request_target'
    }:
        raise ValueError('Data promotion must run from trusted main')
    repo = env['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise ValueError('Invalid repository')
    run_id, attempt = env['GITHUB_RUN_ID'], env['GITHUB_RUN_ATTEMPT']
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError('Invalid run identity')
    branch = f'automation/{kind}-{run_id}-{attempt}'
    base = run(['git', 'rev-parse', 'HEAD'])
    if not run(['git', 'diff', '--cached', '--name-only']):
        print('No producer data changes; no PR created')
        return None
    run(['git', 'diff', '--exit-code'])  # All tracked producer changes must be staged.
    # Check staged paths before even creating a commit; archive integrity follows.
    for path in run(['git', 'diff', '--cached', '--name-only', '-z']).split('\0'):
        if path and not allowed(kind, path):
            raise ValueError('Producer staged a non-allowlisted path: ' + path)
    subprocess.run([sys.executable, 'scripts/check_oracle_archive.py', '--staged'], check=True)
    run(['git', 'switch', '-c', branch])
    run(['git', 'commit', '-m', f'data: validated {kind} publication'])
    run(['git', 'fetch', 'origin', 'main'])
    # Fail closed instead of replaying a stale collector over newer data or code.
    if run(['git', 'rev-parse', 'origin/main']) != base:
        raise ValueError('Main advanced during production; no stale data will be merged')
    head = run(['git', 'rev-parse', 'HEAD'])
    validate_paths(kind, base)
    verify(base)
    if run(['git', 'rev-parse', 'HEAD']) != head:
        raise ValueError('Tested commit changed')
    run(['git', 'diff', '--exit-code', 'HEAD'])
    run(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'])
    run_url = f'https://github.com/{repo}/actions/runs/{run_id}'
    pr = api(repo, 'pulls', 'POST', {
        'title': f'data: validated {kind} publication ({run_id})',
        'head': branch, 'base': 'main',
        'body': f'Trusted main producer. Archive and snapshot validation, full Python tests and scheduler tests passed on `{head}`.\n\nEvidence: {run_url}\n\nNo strategy or code changes.',
    })
    if pr['head']['sha'] != head or pr['base']['ref'] != 'main':
        raise ValueError('Created PR does not match the tested data commit')
    api(repo, 'check-runs', 'POST', {
        'name': 'test', 'head_sha': head, 'status': 'completed', 'conclusion': 'success',
        'details_url': run_url,
        'output': {'title': 'Trusted producer tests passed',
                   'summary': f'Archive integrity, snapshot schema, full pytest and scheduler tests passed on {head}. Logs: {run_url}'},
    })
    # Wait only for GitHub to calculate mergeability. No admin/bypass flag.
    for _ in range(10):
        current = api(repo, f'pulls/{pr["number"]}')
        if current['head']['sha'] != head:
            raise ValueError('Data PR head changed after tests')
        if current.get('mergeable') is not None:
            break
        time.sleep(2)
    # Require the exact base too, so a concurrent main change cannot silently
    # invalidate the test evidence or replace a newer market snapshot.
    if api(repo, 'git/ref/heads/main')['object']['sha'] != base:
        raise ValueError('Main advanced after testing; leave the data PR unmerged')
    result = api(repo, f'pulls/{pr["number"]}/merge', 'PUT',
                 {'sha': head, 'merge_method': 'merge'})
    if not result.get('merged'):
        raise ValueError('Normal protected-branch merge did not succeed')
    merged = api(repo, f'pulls/{pr["number"]}')
    if not merged.get('merged') or merged['merge_commit_sha'] != result['sha']:
        raise ValueError('Merged PR readback failed')
    print(json.dumps({'data_pr': pr['html_url'], 'tested_sha': head,
                      'merge_commit': result['sha'], 'test_evidence': run_url}))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=tuple(PREFIXES))
    promote(parser.parse_args().kind)
