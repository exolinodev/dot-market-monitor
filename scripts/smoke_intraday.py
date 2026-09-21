"""Runner smoke in an isolated directory: no promotion or account operations."""
import argparse
from pathlib import Path
import sys
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from common import utcnow
from cycles import resolve
from intraday import collect
from validate_intraday import validate_record

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    sentinel = args.data_dir / 'sentinel.json.gz'
    sentinel.write_bytes(b'light runs do not rewrite gzip')
    kind, boundary = resolve(utcnow())
    row = collect(args.data_dir, kind, boundary)
    validate_record(row)
    assert sentinel.read_bytes() == b'light runs do not rewrite gzip'
    report = {'meta': row['meta'], 'latest_bytes': (args.data_dir / 'intraday/latest.json').stat().st_size,
              'tape_complete': {k: (v or {}).get('complete') for k, v in row['tape'].items()},
              'archive_files': [str(p.relative_to(args.data_dir)) for p in args.data_dir.rglob('*') if p.is_file()]}
    print(json.dumps(report, indent=2))
    (args.data_dir / 'smoke-report.json').write_text(json.dumps(report, indent=2) + '\n')
