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
