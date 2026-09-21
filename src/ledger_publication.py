"""Resolve when an executable plan first became visible on trusted main history.

Use first-parent merge time, not the submission's author time or its branch
commit time. The caller supplies the fetched, immutable trusted main revision.
No network calls and no dependency on the local wall clock.
"""
import json
import re
import subprocess

from cycles import iso
from ledger import digest, effective_at


def _git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo)


def publication(repo, trusted_head, plan):
    if not re.fullmatch(r'[a-f0-9]{40}', trusted_head):
        raise ValueError('Immutable trusted main revision required')
    ident = plan['forecast_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
        raise ValueError('Invalid plan identity')
    path = 'data/ledger/plans/' + ident + '.json'
    commits = _git(repo, 'log', '--first-parent', '--diff-merges=first-parent',
                   '--no-patch', '--diff-filter=A', '--format=%H', trusted_head, '--', path).decode().split()
    if len(commits) != 1:
        raise ValueError('Plan needs exactly one first-parent publication')
    commit = commits[0]
    for revision in (commit, trusted_head):
        mode = _git(repo, 'ls-tree', revision, '--', path)
        if not mode.startswith(b'100644 blob '):
            raise ValueError('Published plan must be a regular JSON file')
        if json.loads(_git(repo, 'show', revision + ':' + path)) != plan:
            raise ValueError('Plan differs from its first published evidence')
    stamp = _git(repo, 'show', '-s', '--format=%cI', commit).decode().strip()
    return {'commit': commit, 'at_utc': iso(stamp), 'plan_sha256': digest(plan)}


def instruction(repo, trusted_head, plan):
    evidence = publication(repo, trusted_head, plan)
    from cycles import utc
    available = max(utc(plan['created_at_utc']), utc(evidence['at_utc']))
    return {'type': 'instruction', 'event_id': 'plan-' + plan['forecast_id'],
            'at_utc': effective_at(available), 'plan': plan, 'publication': evidence}


def verify_instruction(repo, trusted_head, event):
    if event != instruction(repo, trusted_head, event['plan']):
        raise ValueError('Instruction publication evidence or execution time differs from Git')
