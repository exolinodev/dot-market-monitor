import importlib.util
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("collection_due", Path(__file__).parents[1] / "scripts/collection_due.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
due = module.collection_due


def doc(stamp, **kwargs):
    return {"meta": {"generated_at_utc": stamp, "fresh": True, "status": "ok", **kwargs}}


def test_backup_skips_current_cycle_but_recovers_hours_old_snapshot():
    now = datetime(2026, 9, 13, 2, 55, tzinfo=timezone.utc)
    assert not due(doc("2026-09-13T02:51:00Z"), now, "schedule")
    assert due(doc("2026-09-12T22:51:00Z"), now, "schedule")


def test_delayed_backup_recovers_after_hour_boundary_and_midnight():
    now = datetime(2026, 9, 13, 0, 7, tzinfo=timezone.utc)
    assert not due(doc("2026-09-12T23:51:00Z"), now, "schedule")
    assert due(doc("2026-09-12T22:51:00Z"), now, "schedule")


def test_manual_coalesces_two_minutes_and_push_runs_new_code():
    now = datetime(2026, 9, 13, 2, 55, tzinfo=timezone.utc)
    assert not due(doc("2026-09-13T02:54:00Z"), now, "workflow_dispatch")
    assert due(doc("2026-09-13T02:51:00Z"), now, "workflow_dispatch")
    assert due(doc("2026-09-13T02:54:00Z"), now, "push")


def test_malformed_stale_future_and_failed_data_do_not_suppress_recovery():
    now = datetime(2026, 9, 13, 2, 55, tzinfo=timezone.utc)
    for document in [None, {}, doc(None), doc("bad"), doc("2026-09-13T02:54:00"),
                     doc("2026-09-13T03:00:00Z"), doc("2026-09-13T02:54:00Z", fresh=False),
                     doc("2026-09-13T02:54:00Z", fresh="true"), doc("2026-09-13T02:54:00Z", status="error")]:
        assert due(document, now, "schedule")
    assert not due(doc("2026-09-13T02:54:00Z", status="partial"), now, "schedule")
