"""Inspect a demo journal or permanently abandon one never-dispatched intent."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from demo_journal import locked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-dir', type=Path, required=True)
    parser.add_argument('--account-uid', required=True)
    parser.add_argument('--key-fingerprint', required=True, help='SHA-256 fingerprint, never an API key')
    parser.add_argument('--abandon-prepared', metavar='OPERATION_ID',
                        help='Append an abandonment event only if dispatch never began')
    args = parser.parse_args(argv)
    try:
        with locked(args.journal_dir, args.account_uid, args.key_fingerprint) as store:
            if args.abandon_prepared:
                store.abandon_prepared(args.abandon_prepared)
            pending = [{'operation_id': ident, 'endpoint': op['action']['endpoint'],
                        'role': op['action']['role'], 'status': op['status'],
                        'can_abandon': op['status'] == 'prepared'}
                       for ident, op in sorted(store.state['operations'].items()) if op['status'] != 'resolved']
            result = {'journal_head_sha256': store.tip, 'unresolved_operations': pending,
                      'abandoned_operation_id': args.abandon_prepared, 'authorizes_execution': False}
    except (ValueError, KeyError, OSError):
        print('Recovery refused; verify journal integrity, account identity and operation state. '
              'No exchange request was made.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
