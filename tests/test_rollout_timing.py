from copy import deepcopy
from datetime import timedelta
import json
import pytest
from cycles import utc, iso
from rollout_timing import summarize, report
from test_ledger_publication import git

START = '2026-09-20T00:00:00Z'
END = '2026-09-22T00:00:00Z'


def item(boundary, lag=8, publish=40):
    point = utc(boundary)
    return {'row': {'meta': {'cycle_boundary_utc': iso(point),
        'run_kind': 'full' if point.minute == 0 else 'light', 'boundary_lag_seconds': lag,
        'run_duration_seconds': .5, 'generated_at_utc': iso(point + timedelta(seconds=lag + 1)),
        'status': 'ok', 'fresh': True}}, 'publication_commit': 'a' * 40,
        'published_at_utc': iso(point + timedelta(seconds=publish)), 'gzip_paths_changed': []}


def all_quarters():
    return [item(utc(START) + timedelta(minutes=15 * n)) for n in range(192)]


def test_missing_quarters_count_against_denominator_and_short_window_never_passes():
    result = summarize(all_quarters()[:1], START, END, END)
    assert result['on_time_fraction_of_expected'] == 1 / 192
    assert len(result['missing_boundaries']) == 191
    assert not result['timing_and_light_gzip_criteria_met']
    result = summarize([item(START)], START, '2026-09-20T00:15:00Z', END)
    assert not result['window']['at_least_48_hours_elapsed']


def test_full_window_meets_only_scoped_criteria_and_future_window_does_not():
    result = summarize(all_quarters(), START, END, END)
    assert result['expected_light_quarters'] == 144
    assert result['timing_and_light_gzip_criteria_met']
    assert not result['phase1_acceptance_proven']
    # All currently published records are valid; the final required quarter is still future.
    result = summarize(all_quarters()[:-1], START, END, '2026-09-21T23:44:00Z')
    assert not result['window']['at_least_48_hours_elapsed']
    assert not result['timing_and_light_gzip_criteria_met']


def test_fast_capture_does_not_hide_slow_publication_or_gzip_rewrite():
    rows = all_quarters()
    rows[1] = item('2026-09-20T00:15:00Z', publish=120)
    rows[2]['gzip_paths_changed'] = ['data/raw/latest.json.gz']
    result = summarize(rows, START, END, END)
    assert result['on_time_fraction_of_expected'] == 1
    assert result['light_published_under_60_seconds'] == 143
    assert not result['no_light_gzip_changes']
    assert not result['timing_and_light_gzip_criteria_met']


def test_duplicate_or_invalid_timing_is_rejected():
    row = item(START)
    with pytest.raises(ValueError, match='Duplicate'):
        summarize([row, row], START, END, END)
    row['row']['meta']['boundary_lag_seconds'] = float('nan')
    with pytest.raises(ValueError, match='timing'):
        summarize([row], START, END, END)


def test_publication_uses_main_merge_time_not_branch_time_and_detects_rewrite(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'base', date=START)
    git(tmp_path, 'switch', '-c', 'producer')
    path = tmp_path / 'data/intraday/2026/09/20.jsonl'
    path.parent.mkdir(parents=True)
    row = item('2026-09-20T00:15:00Z')['row']
    path.write_text(json.dumps(row) + '\n')
    import gzip
    compressed = tmp_path / 'data/raw/latest.json.gz'
    compressed.parent.mkdir()
    compressed.write_bytes(gzip.compress(b'{}'))
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-m', 'capture', date='2026-09-20T00:15:10Z')
    git(tmp_path, 'switch', 'main')
    git(tmp_path, 'merge', '--no-ff', 'producer', '-m', 'publish', date='2026-09-20T00:17:00Z')
    head = git(tmp_path, 'rev-parse', 'HEAD')
    result = report(tmp_path, head, START, END, END)
    assert result['quarters'][0]['boundary_to_publication_seconds'] == 120
    assert result['quarters'][0]['publication_commit'] == head
    assert result['light_published_under_60_seconds'] == 0
    assert result['quarters'][0]['gzip_paths_changed'] == ['data/raw/latest.json.gz']
    altered = deepcopy(row); altered['meta']['boundary_lag_seconds'] = 1
    path.write_text(json.dumps(altered) + '\n')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-m', 'rewrite', date='2026-09-20T00:18:00Z')
    with pytest.raises(ValueError, match='rewritten'):
        report(tmp_path, git(tmp_path, 'rev-parse', 'HEAD'), START, END, END)


def test_ninety_five_percent_threshold_and_recorded_duration_are_explicit():
    rows = all_quarters()
    for n in range(9):
        rows[n * 4] = item(utc(START) + timedelta(hours=n), lag=31, publish=40)
    assert summarize(rows, START, END, END)['timing_and_light_gzip_criteria_met']
    rows[36] = item(utc(START) + timedelta(hours=9), lag=31, publish=40)
    assert not summarize(rows, START, END, END)['timing_and_light_gzip_criteria_met']
    rows = all_quarters()
    rows[1]['row']['meta']['run_duration_seconds'] = 60
    result = summarize(rows, START, END, END)
    assert not result['all_light_collections_under_60_seconds']
    assert not result['timing_and_light_gzip_criteria_met']
