import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import pytest
from cycles import resolve, metadata

spec = importlib.util.spec_from_file_location('collection_due', Path(__file__).parents[1] / 'scripts/collection_due.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_prewarm_rollover_and_alignment():
    now = datetime(2026, 9, 20, 23, 59, tzinfo=timezone.utc)
    kind, boundary = resolve(now, 'full', '2026-09-21T00:00:00Z')
    assert kind == 'full' and boundary.day == 21
    for b in ('2026-09-21T00:00:01Z', '2026-09-21T00:05:00Z', '2026-09-21T01:00:00Z'):
        with pytest.raises(ValueError): resolve(now, 'full', b)


def test_quarter_identity_suppresses_duplicates_not_previous_quarters():
    now = datetime(2026, 9, 20, 20, 18, tzinfo=timezone.utc)
    row = {'meta': {'generated_at_utc': '2026-09-20T20:15:15Z', 'run_kind': 'light',
                    'cycle_boundary_utc': '2026-09-20T20:15:00Z', 'fresh': True, 'status': 'partial'}}
    assert not module.collection_due(row, now, 'schedule')
    assert not module.collection_due(row, now, 'schedule', 2)
    row['meta']['cycle_boundary_utc'] = '2026-09-20T20:00:00Z'
    assert module.collection_due(row, now, 'schedule')
    assert module.collection_due(None, now, 'schedule')
    assert module.collection_due(row, now, 'push')


def test_full_hour_only_and_measured_lateness():
    now = datetime(2026, 9, 20, 20, 18, tzinfo=timezone.utc)
    assert resolve(now, event='schedule')[0] == 'light'
    assert resolve(now, event='push')[1].minute == 0
    with pytest.raises(ValueError): resolve(now, 'full', '2026-09-20T20:15:00Z')
    assert metadata('light', '2026-09-20T20:00:00Z', now)['late']


@pytest.mark.parametrize('status', ['ok', 'partial'])
def test_code_push_does_not_repeat_published_hour_after_quarter_advances(status):
    now = datetime(2026, 9, 21, 7, 36, tzinfo=timezone.utc)
    row = {'meta': {'generated_at_utc': '2026-09-21T07:10:03Z', 'run_kind': 'full',
                    'cycle_boundary_utc': '2026-09-21T07:00:00Z', 'fresh': True, 'status': status}}
    assert not module.collection_due(row, now, 'push')
    assert not module.collection_due(row, now, 'push', 2)
    # A later hour still needs capture. Deploying before any valid current full
    # snapshot also remains a collection trigger.
    assert module.collection_due(row, now.replace(hour=8), 'push')
    assert module.collection_due(None, now, 'push')
    row['meta']['fresh'] = False
    assert module.collection_due(row, now, 'push')


def test_boundary_wait_limit_and_late_start():
    spec = importlib.util.spec_from_file_location('wait_boundary', Path(__file__).parents[1] / 'scripts/wait_boundary.py')
    wait = importlib.util.module_from_spec(spec); spec.loader.exec_module(wait)
    now = datetime(2026, 9, 20, 20, 59, tzinfo=timezone.utc)
    delays = []
    assert wait.wait('2026-09-20T21:00:00Z', lambda: now, delays.append) == 68
    assert delays == [68]
    assert wait.wait('2026-09-20T20:45:00Z', lambda: now, delays.append) == 0
    with pytest.raises(ValueError): wait.wait('2026-09-20T21:15:00Z', lambda: now, delays.append)


def test_two_minute_prewarm_waits_until_boundary_plus_eight():
    spec = importlib.util.spec_from_file_location('wait_boundary', Path(__file__).parents[1] / 'scripts/wait_boundary.py')
    wait = importlib.util.module_from_spec(spec); spec.loader.exec_module(wait)
    now = datetime(2026, 9, 20, 23, 58, tzinfo=timezone.utc)
    kind, boundary = resolve(now, 'full', '2026-09-21T00:00:00Z')
    delays = []
    assert kind == 'full' and boundary.day == 21
    assert wait.wait(boundary, lambda: now, delays.append) == 128
    assert delays == [128]
    with pytest.raises(ValueError):
        resolve(now.replace(minute=57), 'full', '2026-09-21T00:00:00Z')


@pytest.mark.parametrize('event', ['push', 'workflow_dispatch', 'schedule'])
def test_missing_full_snapshot_is_deferred_after_light_advanced_ledger(event):
    now = datetime(2026, 9, 24, 15, 21, tzinfo=timezone.utc)
    state = {'last_candle_utc': '2026-09-24T15:14:00Z'}
    assert not module.collection_due(None, now, event, kind='full',
                                     boundary='2026-09-24T15:00:00Z', ledger_state=state)
    # Next hour and missing light quarters must still run. No data is made fresh
    # and no existing account state is replaced just to regenerate an old hour.
    assert module.collection_due(None, now.replace(hour=16, minute=0), event,
                                  kind='full', ledger_state=state)
    assert module.collection_due(None, now.replace(minute=30), event,
                                  kind='light', ledger_state=state)


@pytest.mark.parametrize('state', [None, {}, {'last_candle_utc': None},
    {'last_candle_utc': 'invalid'}, {'last_candle_utc': '2026-09-24T14:59:00Z'},
    {'last_candle_utc': '2026-09-25T15:14:00Z'}])
def test_ledger_deferral_does_not_hide_due_or_invalid_state(state):
    now = datetime(2026, 9, 24, 15, 21, tzinfo=timezone.utc)
    assert module.collection_due(None, now, 'push', ledger_state=state)


def test_workflow_outputs_defer_obsolete_full_without_changing_data(tmp_path, monkeypatch, capsys):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 24, 15, 21, tzinfo=timezone.utc)
    state = tmp_path / 'data/ledger/state.json'
    state.parent.mkdir(parents=True)
    raw = '{"last_candle_utc":"2026-09-24T15:14:00Z"}'
    state.write_text(raw)
    output = tmp_path / 'output'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.delenv('RUN_KIND', raising=False)
    monkeypatch.delenv('BOUNDARY_UTC', raising=False)
    module.main()
    assert output.read_text() == 'due=false\nrun_kind=full\nboundary_utc=2026-09-24T15:00:00Z\n'
    assert 'deferred until next hour' in capsys.readouterr().out
    assert state.read_text() == raw
    assert not (tmp_path / 'data/llm_snapshot.json').exists()
