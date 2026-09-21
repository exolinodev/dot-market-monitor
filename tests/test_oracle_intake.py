"""Draft PRs are untrusted data; the trusted writer never merges their trees."""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import ValidationError
from test_oracle_submission import submission, git, commit, run_entrypoint
from datetime import datetime, timedelta
from oracle_submission import read_pr_submission, publish_submission, strict_json, parse_submission
from oracle_receipts import verify_receipt

ROOT=Path(__file__).parents[1]
REPOSITORY='exolinodev/dot-market-monitor'


def draft(submission, kind='valid'):
    repo,inbox,envelope,event=submission
    before=event['before']
    git(repo,'reset','--hard',before)
    inbox.parent.mkdir(parents=True,exist_ok=True)
    contents=json.dumps(envelope)
    if kind=='malformed': contents+='}'
    if kind=='truncated': contents=contents[:-1]
    if kind=='duplicate_key': contents=contents.replace('"schema_version": 1','"schema_version": 1, "schema_version": 1',1)
    if kind=='failure_close':
        from test_oracle import fixture_forecast
        envelope['forecast']['trade_setup']=fixture_forecast()['trade_setup']
        envelope['forecast']['trade_setup']['failure']['kind']='close_below'
        envelope['forecast']['trade_setup']['failure']['interval_minutes']=60
        contents=json.dumps(envelope)
    inbox.write_text(contents)
    if kind=='extra_code': (repo/'evil.py').write_text('raise RuntimeError("must never execute")')
    if kind=='symlink':
        inbox.unlink();inbox.symlink_to('../../llm_snapshot.json')
    if kind=='test_path': inbox.rename(inbox.with_name('test.json'))
    head=commit(repo)
    if kind=='two_commits':
        (repo/'extra.txt').write_text('change');head=commit(repo)
    git(repo,'update-ref','refs/remotes/origin/main',before)
    git(repo,'reset','--hard',before)
    pr={'action':'opened','number':7,'pull_request':{'draft':True,
        'created_at':envelope['forecast']['created_at_utc'],
        'base':{'ref':'main','sha':before,'repo':{'full_name':REPOSITORY}},
        'head':{'ref':'oracle-submission/20260916T202300Z','sha':head,'repo':{'full_name':REPOSITORY}}}}
    if kind=='fork': pr['pull_request']['head']['repo']['full_name']='elsewhere/repo'
    if kind=='not_draft': pr['pull_request']['draft']=False
    if kind=='edited': pr['action']='synchronize'
    if kind=='wrong_base': pr['pull_request']['base']['ref']='another'
    return repo,envelope,pr


def test_draft_preflight_publishes_only_validated_data_and_readable_receipt(submission):
    repo,envelope,event=draft(submission)
    head_before=git(repo,'rev-parse','HEAD')
    actual=read_pr_submission(event,REPOSITORY,repo)
    assert actual==envelope
    path=publish_submission(actual,actual['forecast']['created_at_utc'],repo,archive_submission=True)
    assert path.is_file()
    assert git(repo,'rev-parse','HEAD')==head_before
    receipt_path=repo/'data/oracle/receipts'/(actual['forecast']['forecast_id']+'.json')
    receipt=json.loads(receipt_path.read_text())
    assert verify_receipt(receipt,repo/'data')
    inp=repo/'data/oracle'/receipt['input_path']
    assert git(repo,'hash-object',str(inp))==receipt['input_git_blob_sha1']
    with pytest.raises(FileExistsError):
        publish_submission(actual,actual['forecast']['created_at_utc'],repo,archive_submission=True)
    receipt['input_git_blob_sha1']='0'*40
    with pytest.raises(ValueError,match='receipt differs'):
        verify_receipt(receipt,repo/'data')


@pytest.mark.parametrize('kind',['malformed','truncated','duplicate_key','extra_code','symlink','test_path',
                                'two_commits','fork','not_draft','edited','wrong_base','failure_close'])
def test_bad_drafts_never_reach_main_artifacts(submission,kind):
    repo,original,event=draft(submission,kind)
    with pytest.raises((ValueError,ValidationError,subprocess.CalledProcessError)):
        envelope=read_pr_submission(event,REPOSITORY,repo)
        publish_submission(envelope,envelope['forecast']['created_at_utc'],repo,archive_submission=True)
    assert not list((repo/'data/oracle/forecasts').rglob('*.json'))
    assert not list((repo/'data/oracle/submissions').glob('*.json'))
    assert not list((repo/'data/oracle/receipts').glob('*.json'))


def test_pr_entrypoint_uses_main_code_and_rejects_reruns(submission,monkeypatch):
    repo,envelope,event=draft(submission)
    # Local origin, exact immutable object fetch; no network or production clock bypass.
    git(repo,'remote','add','origin',str(repo))
    monkeypatch.setenv('GITHUB_REPOSITORY',REPOSITORY)
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    run_entrypoint(monkeypatch,repo,envelope,{**event,'after':event['pull_request']['head']['sha']},'pull_request_target')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    with pytest.raises(ValueError,match='reruns are forbidden'):
        run_entrypoint(monkeypatch,repo,envelope,{**event,'after':event['pull_request']['head']['sha']},'pull_request_target')


def guard():
    spec=importlib.util.spec_from_file_location('audit_guard',ROOT/'scripts/check_oracle_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_archive_history_detects_add_then_delete_with_empty_net_diff(submission):
    repo,_,_,event=submission
    git(repo,'reset','--hard',event['before'])
    base=git(repo,'rev-parse','HEAD')
    p=repo/'data/oracle/submissions/test.json';p.parent.mkdir(parents=True);p.write_text('{}')
    commit(repo);p.unlink();commit(repo)
    assert not git(repo,'diff','--name-only',base,'HEAD')
    with pytest.raises(ValueError,match='modified or removed'):
        guard().check(base,repo=repo)


def test_staged_guard_blocks_invalid_json_and_test_files_before_push(submission):
    repo,_,_,event=submission
    git(repo,'reset','--hard',event['before'])
    p=repo/'data/oracle/submissions/test.json';p.parent.mkdir(parents=True);p.write_text('{}')
    git(repo,'add','.')
    with pytest.raises(ValueError,match='Invalid submission path'):
        guard().check(repo=repo,staged=True)


def test_strict_json_rejects_trailing_content_and_duplicate_fields():
    for payload in ('{"x":1}}','{"x":1,"x":2}'):
        with pytest.raises(ValueError): strict_json(payload)


def test_truncated_draft_is_named_and_never_repaired(submission):
    repo,original,event=draft(submission,'truncated')
    with pytest.raises(ValueError,match=r'truncated \(\d+ bytes, brace depth \+1, ends with') as info:
        read_pr_submission(event,REPOSITORY,repo)
    assert 'Expecting' in str(info.value)  # the original decoder position stays visible
    with pytest.raises(ValueError,match='over-closed .*brace depth -1'):
        parse_submission(json.dumps(original)+'}')
    with pytest.raises(ValueError,match=r'malformed .*brace depth \+0'):
        parse_submission('{"x":1,"x":2}')
    assert parse_submission(json.dumps(original))==original


def test_draft_opening_time_is_the_acceptance_clock_not_the_runner_start(submission,monkeypatch):
    # An honest draft opened 60s after creation must survive a 10-minute Actions queue.
    repo,envelope,event=draft(submission)
    git(repo,'remote','add','origin',str(repo))
    monkeypatch.setenv('GITHUB_REPOSITORY',REPOSITORY);monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    created=datetime.fromisoformat(envelope['forecast']['created_at_utc'].replace('Z','+00:00'))
    stamp=lambda seconds:(created+timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
    event['pull_request']['created_at']=stamp(60)
    run_entrypoint(monkeypatch,repo,envelope,{**event,'after':event['pull_request']['head']['sha']},'pull_request_target',now=stamp(660))
    assert (repo/'data/oracle/receipts'/(envelope['forecast']['forecast_id']+'.json')).is_file()


@pytest.mark.parametrize('opened,ran,message',[(210,220,r'\+210s \(limit 180s\)'),
    (60,60+1801,'stale queue'),(200,100,'ahead of the writer clock')])
def test_late_or_implausible_draft_opening_still_rejects(submission,monkeypatch,opened,ran,message):
    repo,envelope,event=draft(submission)
    git(repo,'remote','add','origin',str(repo))
    monkeypatch.setenv('GITHUB_REPOSITORY',REPOSITORY);monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    created=datetime.fromisoformat(envelope['forecast']['created_at_utc'].replace('Z','+00:00'))
    stamp=lambda seconds:(created+timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
    event['pull_request']['created_at']=stamp(opened)
    with pytest.raises(ValueError,match=message):
        run_entrypoint(monkeypatch,repo,envelope,{**event,'after':event['pull_request']['head']['sha']},'pull_request_target',now=stamp(ran))
    assert not list((repo/'data/oracle/forecasts').rglob('*.json'))


def test_rejection_verdict_is_exported_for_the_workflow(submission,monkeypatch,tmp_path):
    repo,original,event=draft(submission,'truncated')
    git(repo,'remote','add','origin',str(repo))
    monkeypatch.setenv('GITHUB_REPOSITORY',REPOSITORY);monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    output=tmp_path/'github_output';monkeypatch.setenv('GITHUB_OUTPUT',str(output))
    monkeypatch.setenv('GITHUB_EVENT_NAME','pull_request_target');monkeypatch.chdir(repo)
    payload=repo/'event.json';payload.write_text(json.dumps(event));monkeypatch.setenv('GITHUB_EVENT_PATH',str(payload))
    import runpy
    with pytest.raises(ValueError):
        runpy.run_path(str(ROOT/'scripts/publish_oracle_dispatch.py'),run_name='__main__')
    verdict=output.read_text()
    assert verdict.startswith('rejection=ValueError: Submission JSON is truncated') and verdict.count('\n')==1
