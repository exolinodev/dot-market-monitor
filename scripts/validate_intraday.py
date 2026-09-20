"""Validate small producer records and byte-prefix immutability of JSONL archives."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cycles import utc, resolve


def validate_record(row):
    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[1] / 'schema/intraday.schema.json').read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(row)
    meta = row['meta']
    kind, boundary = resolve(utc(meta['generated_at_utc']), meta['run_kind'], meta['cycle_boundary_utc'])
    assert row['schema_version'] == 1
    assert meta['status'] in ('ok', 'partial') and isinstance(meta['fresh'], bool)
    assert meta['request_count'] <= 12
    assert meta['boundary_lag_seconds'] >= 0
    assert meta['late'] == (meta['boundary_lag_seconds'] > (360 if kind == 'full' else 240))
    assert (boundary - utc(row['quarter_start_utc'])).total_seconds() == 900
    for name, rows in row['candles'].items():
        seconds = {'dot_1': 60, 'dot_5': 300, 'dot_15': 900, 'btc_15': 900, 'trade': 60, 'mark': 60}[name]
        stamps = []
        for stamp, op, high, low, close, volume in rows or []:
            assert boundary.timestamp() - 900 <= stamp and stamp + seconds <= boundary.timestamp()
            assert stamp % seconds == 0 and 0 < low <= min(op, close) <= max(op, close) <= high and volume >= 0
            stamps.append(stamp)
        assert stamps == sorted(set(stamps))


def check(base=None, staged=False, root=Path('.')):
    args = ['git', 'diff', '--cached' if staged else base, '--name-only', '--', 'data/intraday', 'data/funding'] if (base or staged) else None
    paths = subprocess.check_output(args, cwd=root, text=True).splitlines() if args else []
    for name in paths:
        current = subprocess.check_output(['git', 'show', ':' + name], cwd=root) if staged else (root / name).read_bytes()
        if name.endswith('.jsonl'):
            old = subprocess.run(['git', 'show', ('HEAD' if staged else base) + ':' + name], cwd=root,
                                 capture_output=True).stdout
            if not current.startswith(old) or (old and not old.endswith(b'\n')):
                raise ValueError('Append-only archive changed: ' + name)
    for path in (root / 'data/intraday').glob('*/*/*.jsonl'):
        seen = set()
        for raw in path.read_bytes().splitlines():
            if len(raw) + 1 > 25000:
                raise ValueError('Quarter exceeds 25 KB')
            row = json.loads(raw)
            validate_record(row)
            boundary = row['meta']['cycle_boundary_utc']
            if boundary in seen:
                raise ValueError('Duplicate quarter')
            seen.add(boundary)
            if path.relative_to(root / 'data/intraday').as_posix() != utc(boundary).strftime('%Y/%m/%d.jsonl'):
                raise ValueError('Wrong archive day')
    latest = root / 'data/intraday/latest.json'
    if latest.exists():
        row = json.loads(latest.read_text())
        validate_record(row)
        from intraday import read_cycle
        if read_cycle(root / 'data', row['meta']['cycle_boundary_utc']) != row:
            raise ValueError('Latest differs from immutable quarter')
    for path in (root / 'data/funding').glob('*/*.jsonl'):
        from perp_data import parse_funding
        parse_funding({'result': 'success', 'rates': [json.loads(line) for line in path.read_text().splitlines()]})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    check(args.base, args.staged)
    print('Intraday and funding archive validation passed')
