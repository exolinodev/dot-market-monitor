"""Real Git routing plus the production publisher, without a network or clock bypass."""
import copy
import json
from pathlib import Path
import subprocess

from jsonschema import ValidationError
import pytest

from oracle_common import validate
from oracle_submission import read_push_submission, publish_submission
from oracle_forecasts import forecast_id, snapshot_strategy_key, MAX_ACCEPTANCE_DELAY_SECONDS
from datetime import datetime, timedelta, timezone

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
    bad = copy.deepcopy(envelope)
    bad['forecast']['evidence']['opposing_feature_ids'] = ['structure.1h.new_low', 'not_a_feature']
    # The verdict names every unavailable ID so the consumer can see what it cited.
    with pytest.raises(ValueError, match='not ok at this snapshot: structure.1h.new_low, not_a_feature'):
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


def later(envelope, seconds=1):
    result = copy.deepcopy(envelope)
    f = result['forecast']
    f['created_at_utc'] = (datetime.fromisoformat(f['created_at_utc'].replace('Z','+00:00'))
        + timedelta(seconds=seconds)).isoformat().replace('+00:00','Z')
    f['forecast_id'] = forecast_id(f['created_at_utc'],f['snapshot_sha256'])
    return result


def test_new_time_cannot_republish_same_snapshot_even_for_no_trade(submission):
    repo, _, envelope, _ = submission
    original = publish_submission(envelope,envelope['forecast']['created_at_utc'],repo)
    before = original.read_bytes()
    fresh = later(envelope)
    assert fresh['forecast']['trade_setup']['direction'] == 'NONE'
    with pytest.raises(FileExistsError, match='Snapshot/strategy'):
        publish_submission(fresh,fresh['forecast']['created_at_utc'],repo)
    assert original.read_bytes() == before
    assert len(list((repo/'data/oracle/forecasts').glob('*/*/*/*.json'))) == 1
    fresh['forecast']['strategy_version'] = 'oracle-v3.1.0-test'
    assert publish_submission(fresh,fresh['forecast']['created_at_utc'],repo).exists()


def test_legacy_forecast_without_key_still_prevents_duplicate(submission):
    repo, _, envelope, _ = submission
    f = envelope['forecast']
    # Represents an old archive, before reservation files existed.
    path = repo/'data/oracle/forecasts/2026/09/16'/(f['forecast_id']+'.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(f))
    fresh = later(envelope)
    with pytest.raises(FileExistsError,match='already published'):
        publish_submission(fresh,fresh['forecast']['created_at_utc'],repo)


def test_different_ids_race_for_one_snapshot_key(submission):
    from concurrent.futures import ThreadPoolExecutor
    repo, _, envelope, _ = submission
    def attempt(i):
        e = later(envelope,i)
        try:
            return publish_submission(e,e['forecast']['created_at_utc'],repo)
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt,range(6)))
    assert len([p for p in results if p]) == 1
    assert len(list((repo/'data/oracle/forecast_keys').glob('*.json'))) == 1


def test_independent_git_writers_cannot_rebase_two_snapshot_winners(submission,tmp_path_factory):
    repo, _, envelope, _ = submission
    remote = tmp_path_factory.mktemp('remote')/'repo.git'
    git(repo,'clone','--bare',str(repo),str(remote))
    clones = []
    for i in range(2):
        clone = tmp_path_factory.mktemp('writer')/'repo'
        git(repo,'clone',str(remote),str(clone))
        git(clone,'config','user.name','Test')
        git(clone,'config','user.email','test@example.invalid')
        e = later(envelope,i)
        publish_submission(e,e['forecast']['created_at_utc'],clone)
        commit(clone)
        clones.append(clone)
    git(clones[0],'push','origin','HEAD:main')
    git(clones[1],'fetch','origin','main')
    with pytest.raises(subprocess.CalledProcessError):
        git(clones[1],'rebase','origin/main')
    assert len(list((clones[0]/'data/oracle/forecasts').glob('*/*/*/*.json'))) == 1


def run_entrypoint(monkeypatch,repo,envelope,event,route,now=None):
    import importlib.util
    spec = importlib.util.spec_from_file_location('publish_dispatch',ROOT/'scripts/publish_oracle_dispatch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = datetime.fromisoformat((now or envelope['forecast']['created_at_utc']).replace('Z','+00:00'))
    class Clock:
        @staticmethod
        def now(tz): return frozen
    monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.chdir(repo)
    monkeypatch.setenv('GITHUB_EVENT_NAME',route)
    monkeypatch.setenv('SNAPSHOT_COMMIT',envelope['snapshot_commit'])
    monkeypatch.setenv('FORECAST_JSON',json.dumps(envelope['forecast']))
    monkeypatch.setenv('GITHUB_SHA',event['after'])
    payload = repo/'event.json'
    payload.write_text(json.dumps(event))
    monkeypatch.setenv('GITHUB_EVENT_PATH',str(payload))
    module.main()


@pytest.mark.parametrize('route',['workflow_dispatch','push'])
def test_workflow_entrypoints_publish_exact_input_and_enforce_dedup(submission,monkeypatch,route):
    repo, _, envelope, event = submission
    run_entrypoint(monkeypatch,repo,envelope,event,route)
    f = envelope['forecast']
    path = repo/'data/oracle/forecasts/2026/09/16'/(f['forecast_id']+'.json')
    assert json.loads(path.read_text()) == f
    import gzip
    bound = json.loads(gzip.decompress((repo/'data/oracle/inputs'/(f['snapshot_sha256']+'.json.gz')).read_bytes()))
    from oracle_common import digest
    assert digest(bound) == f['snapshot_sha256']
    with pytest.raises(FileExistsError):
        run_entrypoint(monkeypatch,repo,envelope,event,route)


@pytest.mark.parametrize('route',['workflow_dispatch','push'])
@pytest.mark.parametrize('invalid',['hash','id','old_creation','future_creation','stale_snapshot',
    'measurement','oracle_config','feature_version','evidence','reversal','geometry','target_order'])
def test_both_writer_routes_reject_invalid_forecasts(submission,monkeypatch,route,invalid):
    from test_oracle import fixture_forecast
    repo, inbox, envelope, event = submission
    f = envelope['forecast']
    now = f['created_at_utc']
    if invalid=='hash':
        f['snapshot_sha256']='0'*64
        f['forecast_id']=forecast_id(f['created_at_utc'],f['snapshot_sha256'])
    elif invalid=='id': f['forecast_id']='20260916T202328Z-000000000000-oracle-v3'
    elif invalid=='old_creation': now=later(envelope,MAX_ACCEPTANCE_DELAY_SECONDS+1)['forecast']['created_at_utc']
    elif invalid=='future_creation': now=later(envelope,-MAX_ACCEPTANCE_DELAY_SECONDS-1)['forecast']['created_at_utc']
    elif invalid=='stale_snapshot':
        envelope=later(envelope,91*60);f=envelope['forecast'];now=f['created_at_utc']
    elif invalid=='measurement': f['measurement_config_sha256']='0'*64
    elif invalid=='oracle_config': f['oracle_config_sha256']='0'*64
    elif invalid=='feature_version': f['oracle_feature_version']='99.0.0'
    elif invalid=='evidence': f['evidence']['supporting_feature_ids']=['not_a_feature']
    else:
        f['trade_setup']=fixture_forecast()['trade_setup']
        if invalid=='reversal': f['regime']='REVERSAL_ARMED'
        elif invalid=='geometry': f['trade_setup']['failure']['price_usd']=101
        else: f['trade_setup']['targets'].reverse()
    # Route the exact invalid JSON through the real push-event parser as well.
    if route=='push':
        inbox.unlink()
        inbox=inbox.with_name(f['forecast_id']+'.json')
        inbox.write_text(json.dumps(envelope))
        if git(repo,'status','--porcelain'):
            event['after']=commit(repo)
    with pytest.raises((ValueError,ValidationError)):
        run_entrypoint(monkeypatch,repo,envelope,event,route,now)
    assert not list((repo/'data/oracle/forecasts').glob('*/*/*/*.json'))
