"""Publication chronology follows main visibility, not earlier branch timestamps."""
from copy import deepcopy
import json
import os
import subprocess
import pytest
from ledger import replay
from ledger_publication import instruction, publication, verify_instruction
from test_ledger import prepare, candle, EPOCH, order


def git(repo, *args, date=None):
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.check_output(['git', *args], cwd=repo, env=env, stderr=subprocess.DEVNULL).decode().strip()


def merged_plan(tmp_path, plan):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'genesis', date=EPOCH)
    before = git(tmp_path, 'rev-parse', 'HEAD')
    git(tmp_path, 'switch', '-c', 'writer')
    path = tmp_path / 'data/ledger/plans' / (plan['forecast_id'] + '.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(plan))
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'plan', date='2026-09-20T20:00:10Z')
    git(tmp_path, 'switch', 'main')
    git(tmp_path, 'merge', '--no-ff', 'writer', '-m', 'publish', date='2026-09-20T20:04:30Z')
    return before, git(tmp_path, 'rev-parse', 'HEAD')


def test_plan_uses_first_main_merge_and_cannot_fill_earlier(tmp_path):
    cfg, plan, events = prepare()
    before, head = merged_plan(tmp_path, plan)
    event = instruction(tmp_path, head, plan)
    assert event['publication']['commit'] == head
    assert event['publication']['at_utc'] == '2026-09-20T20:04:30Z'
    assert event['at_utc'] == '2026-09-20T20:05:00Z'
    verify_instruction(tmp_path, head, event)
    with pytest.raises(ValueError, match='publication'):
        publication(tmp_path, before, plan)
    state, records = replay(cfg, EPOCH, events[:2] + [candle(i) for i in range(1, 5)] + [event, candle(5)])
    fills = [e for r in records for e in r['effects'] if e['type'] == 'ENTRY_FILL']
    assert len(fills) == 1 and fills[0]['at_utc'] == '2026-09-20T20:05:00Z'
    assert state['position']['opened_at_utc'] == fills[0]['at_utc']


def test_expired_during_writer_delay_never_becomes_resting_order(tmp_path):
    cfg, plan, events = prepare(entry=order(valid_until_utc='2026-09-20T20:03:00Z'))
    _, head = merged_plan(tmp_path, plan)
    event = instruction(tmp_path, head, plan)
    state, records = replay(cfg, EPOCH, events[:2] + [event, candle(5)])
    assert state['position'] is None and state['order'] is None
    assert records[-2]['effects'][0]['reason'] == 'expired_before_publication'


def test_replay_and_git_verifier_reject_backdated_or_forged_publication(tmp_path):
    cfg, plan, events = prepare()
    _, head = merged_plan(tmp_path, plan)
    event = instruction(tmp_path, head, plan)
    bad = deepcopy(event); bad['at_utc'] = plan['effective_at_utc']
    with pytest.raises(ValueError, match='effective-time'):
        replay(cfg, EPOCH, events[:2] + [bad])
    bad = deepcopy(event); bad['publication']['commit'] = 'a' * 40
    with pytest.raises(ValueError, match='differs from Git'):
        verify_instruction(tmp_path, head, bad)
    path = tmp_path / 'data/ledger/plans' / (plan['forecast_id'] + '.json')
    wrong = deepcopy(plan); wrong['quote']['bid'] = '.1'; path.write_text(json.dumps(wrong))
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'mutate', date='2026-09-20T20:06:00Z')
    with pytest.raises(ValueError, match='first published'):
        publication(tmp_path, git(tmp_path, 'rev-parse', 'HEAD'), plan)
