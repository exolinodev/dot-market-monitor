"""Read-only quarter timing evidence from immutable first-parent Git history."""
from datetime import timedelta
import json
import math
import re
import subprocess

from cycles import iso, utc


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo)


def published_quarters(repo, head, start, end):
    if not re.fullmatch(r'[a-f0-9]{40}', head):
        raise ValueError('Full immutable trusted main SHA required')
    if git(repo, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('Full Git history required for publication evidence')
    days, day = [], utc(start).replace(hour=0, minute=0, second=0, microsecond=0)
    while day < utc(end):
        days.append('data/intraday/' + day.strftime('%Y/%m/%d.jsonl'))
        day += timedelta(days=1)
    commits = git(repo, 'log', '--first-parent', '--reverse', '--diff-merges=first-parent',
                  '--no-patch', '--format=%H', head, '--', *days).decode().split()
    prior, seen, result = {}, set(), []
    for commit in commits:
        stamp = git(repo, 'show', '-s', '--format=%cI', commit).decode().strip()
        parents = git(repo, 'show', '-s', '--format=%P', commit).decode().split()
        changed = (git(repo, 'diff', '--name-only', parents[0], commit) if parents else
                   git(repo, 'diff-tree', '--root', '--no-commit-id', '--name-only', '-r', commit)).decode().splitlines()
        gzip_paths = [p for p in changed if p.endswith('.gz')]
        for path in (p for p in changed if p in days):
            mode = git(repo, 'ls-tree', commit, '--', path)
            if not mode.startswith(b'100644 blob '):
                raise ValueError('Quarter archive deleted or not a regular file')
            raw = git(repo, 'show', commit + ':' + path)
            old = prior.get(path, b'')
            if not raw.startswith(old) or not raw.endswith(b'\n'):
                raise ValueError('Quarter archive was rewritten')
            for line in raw[len(old):].splitlines():
                row = json.loads(line)
                boundary = utc(row['meta']['cycle_boundary_utc'])
                if path != 'data/intraday/' + boundary.strftime('%Y/%m/%d.jsonl'):
                    raise ValueError('Quarter archived on wrong day')
                if boundary in seen:
                    raise ValueError('Duplicate quarter publication')
                seen.add(boundary)
                if utc(start) <= boundary < utc(end):
                    result.append({'row': row, 'publication_commit': commit,
                                   'published_at_utc': iso(stamp), 'gzip_paths_changed': gzip_paths})
            prior[path] = raw
    return result


def summarize(publications, start, end, now):
    start, end, now = utc(start), utc(end), utc(now)
    if start >= end or any(t.minute % 15 or t.second or t.microsecond for t in (start, end)):
        raise ValueError('Ordered quarter-aligned UTC window required')
    expected, point = [], start
    while point < end:
        expected.append(point)
        point += timedelta(minutes=15)
    observed = {}
    for item in publications:
        meta = item['row']['meta']
        boundary = utc(meta['cycle_boundary_utc'])
        if not start <= boundary < end:
            continue
        if boundary not in expected or boundary in observed:
            raise ValueError('Duplicate or misaligned quarter')
        kind = 'full' if boundary.minute == 0 else 'light'
        if meta['run_kind'] != kind:
            raise ValueError('Run kind does not match boundary')
        lag, duration = meta['boundary_lag_seconds'], meta['run_duration_seconds']
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in (lag, duration)):
            raise ValueError('Invalid recorded timing')
        generated, published = utc(meta['generated_at_utc']), utc(item['published_at_utc'])
        acquisition_started = boundary + timedelta(seconds=lag)
        if not acquisition_started <= generated <= published <= now:
            raise ValueError('Publication/generation chronology is inconsistent')
        healthy = meta.get('fresh') is True and meta.get('status') in ('ok', 'partial')
        observed[boundary] = {'boundary_utc': iso(boundary), 'run_kind': kind,
            'healthy': healthy, 'recorded_acquisition_lag_seconds': lag,
            'recorded_collection_duration_seconds': duration,
            'boundary_to_publication_seconds': (published - boundary).total_seconds(),
            'acquisition_to_publication_seconds': (published - acquisition_started).total_seconds(),
            'publication_commit': item['publication_commit'], 'published_at_utc': iso(published),
            'gzip_paths_changed': item['gzip_paths_changed']}
    on_time = sum(r['healthy'] and r['recorded_acquisition_lag_seconds'] <= 30 for r in observed.values())
    light_expected = sum(p.minute != 0 for p in expected)
    lights = [r for r in observed.values() if r['run_kind'] == 'light']
    light_fast = sum(r['healthy'] and r['boundary_to_publication_seconds'] < 60 for r in lights)
    fast_collections = len(lights) == light_expected and all(r['recorded_collection_duration_seconds'] < 60 for r in lights)
    no_gzip = len(lights) == light_expected and all(not r['gzip_paths_changed'] for r in lights)
    complete = now >= end and (end - start).total_seconds() >= 48 * 3600
    ratio = on_time / len(expected)
    return {'window': {'start_utc': iso(start), 'end_exclusive_utc': iso(end), 'checked_at_utc': iso(now),
                       'at_least_48_hours_elapsed': complete},
            'expected_quarters': len(expected), 'published_quarters': len(observed),
            'missing_boundaries': [iso(p) for p in expected if p not in observed],
            'on_time_quarters': on_time, 'on_time_fraction_of_expected': ratio,
            'expected_light_quarters': light_expected, 'light_published_under_60_seconds': light_fast,
            'all_light_publications_under_60_seconds': light_fast == light_expected,
            'all_light_collections_under_60_seconds': fast_collections,
            'no_light_gzip_changes': no_gzip,
            'timing_and_light_gzip_criteria_met': complete and ratio >= .95 and light_fast == light_expected and fast_collections and no_gzip,
            'phase1_acceptance_proven': False,
            'additional_evidence_required': ['Cloudflare deployment and scheduled dispatch provenance',
                'hourly snapshots contain four quarters', 'writer publication latency', 'Git storage growth'],
            'scope': 'Recorded acquisition lag; first-parent main publication time. Collection duration is not runner or publication duration.',
            'quarters': [observed[p] for p in expected if p in observed]}


def report(repo, head, start, end, now):
    # Validate window before querying potentially large histories.
    summarize([], start, end, now)
    return {'trusted_main_sha': head, **summarize(published_quarters(repo, head, start, end), start, end, now)}
