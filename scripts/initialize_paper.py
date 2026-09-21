"""Preview or explicitly initialize a new paper ledger; no publishing or trading."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ledger_activation import activate, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--config', type=Path, default=Path('config/ledger.json'))
    parser.add_argument('--write', action='store_true', help='Create local ledger only after matching reviewed preview')
    parser.add_argument('--expected-plan-sha256', help='Required with --write; SHA from the read-only preview')
    args = parser.parse_args()
    if args.write != bool(args.expected_plan_sha256):
        parser.error('--write and --expected-plan-sha256 must be supplied together')
    baseline = json.loads(args.config.read_text())
    now = datetime.now(timezone.utc)
    result = activate(args.data_dir, baseline, now, args.expected_plan_sha256) if args.write else prepare(args.data_dir, baseline, now)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
