"""Resolve a quarter-hour cycle before installing collector dependencies."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cycles import resolve, utc, iso


def collection_due(document, now, event, run_attempt=1, kind=None, boundary=None):
    kind, point = resolve(now, kind, boundary, event)
    try:
        meta = document['meta']
        generated = utc(meta['generated_at_utc'])
        healthy = meta['fresh'] is True and meta['status'] in ('ok', 'partial')
        same = utc(meta['cycle_boundary_utc']) == point and meta['run_kind'] == kind
        # A committed cycle is immutable, including after a code push. A forced
        # old-hour rerun would also try to rewind an already advanced ledger.
        return not (healthy and same and point <= generated <= now + timedelta(seconds=60))
    except (KeyError, TypeError, ValueError, AttributeError):
        return True


def main():
    now = datetime.now(timezone.utc)
    event = os.environ.get('GITHUB_EVENT_NAME', 'workflow_dispatch')
    kind, point = resolve(now, os.environ.get('RUN_KIND') or None,
                          os.environ.get('BOUNDARY_UTC') or None, event)
    path = 'data/llm_snapshot.json' if kind == 'full' else 'data/intraday/latest.json'
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        document = None
    due = collection_due(document, now, event, kind=kind, boundary=iso(point))
    print(f'{kind} {iso(point)}: ' + ('collect' if due else 'already published'))
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a') as handle:
            handle.write(f'due={str(due).lower()}\nrun_kind={kind}\nboundary_utc={iso(point)}\n')


if __name__ == '__main__':
    main()
