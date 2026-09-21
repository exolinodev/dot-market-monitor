"""Recompute the paper account from its immutable genesis, plans and journal."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ledger_store import verify, rebuild_views

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--rebuild-views', action='store_true', help='Explicitly repair derived state/performance only; never alter journal or existing trades')
    args = parser.parse_args()
    if args.rebuild_views:
        rebuild_views(args.data_dir)
    result = verify(args.data_dir)
    print(json.dumps({'replay': 'identical', 'state_sha256': result['state']['state_sha256'],
                      'events': len(result['records']), 'trades': len(result['trades'])}))
