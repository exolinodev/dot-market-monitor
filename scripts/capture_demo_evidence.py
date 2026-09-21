"""Capture private Kraken demo evidence using read-only calls; never place orders."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from demo_evidence import capture_bundle
from kraken_execution import DemoClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-uid', required=True)
    parser.add_argument('--since', required=True)
    parser.add_argument('--through', help='Defaults to the latest readback server time')
    parser.add_argument('--journal-dir', type=Path, help='Existing private journal; hold its lock during acquisition/import')
    parser.add_argument('--initialize-journal', action='store_true', help='Exclusively create a new private demo journal after acquisition; requires flat baseline')
    parser.add_argument('--establish-flat-baseline', action='store_true', help='Record a one-time acquired empty-account baseline')
    parser.add_argument('--reconcile', action='store_true', help='Record quantity reconciliation against the existing baseline')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-pages', type=int, default=100)
    args = parser.parse_args(argv)
    if args.initialize_journal and (args.journal_dir is None or not args.establish_flat_baseline):
        parser.error('--initialize-journal requires --journal-dir and --establish-flat-baseline')
    if args.initialize_journal and (args.journal_dir.exists() or args.journal_dir.is_symlink()):
        parser.error('Journal already exists; initialization never resets it')
    if (args.establish_flat_baseline or args.reconcile) and args.journal_dir is None:
        parser.error('Baseline/reconciliation requires --journal-dir')
    parent = args.output_dir.parent.resolve()
    if not parent.is_dir():
        parser.error('Output parent must already exist')
    probe = subprocess.run(['git', '-C', str(parent), 'rev-parse', '--is-inside-work-tree'], capture_output=True)
    if probe.returncode == 0:
        parser.error('Private demo evidence must be stored outside a Git worktree')
    if args.journal_dir is not None:
        journal_parent = args.journal_dir.resolve()
        while not journal_parent.exists():
            journal_parent = journal_parent.parent
        journal_probe = subprocess.run(['git', '-C', str(journal_parent), 'rev-parse', '--is-inside-work-tree'], capture_output=True)
        if journal_probe.returncode == 0:
            parser.error('Private demo journal must be stored outside a Git worktree')
    key = os.environ.get('KRAKEN_DEMO_API_KEY', '')
    secret = os.environ.get('KRAKEN_DEMO_API_SECRET', '')
    if not key or not secret:
        parser.error('Provide KRAKEN_DEMO_API_KEY and KRAKEN_DEMO_API_SECRET in the private runner environment')
    try:
        fingerprint = hashlib.sha256(key.encode()).hexdigest()
        client = DemoClient(key, secret)
        def acquire():
            return capture_bundle(args.output_dir, client, args.account_uid, fingerprint,
                                  args.since, args.through, max_pages=args.max_pages)
        if args.journal_dir is None:
            result = acquire()
        else:
            from demo_journal import initialize, locked
            from demo_observation import import_observation
            if args.initialize_journal:
                result = acquire()
                initialize(args.journal_dir, args.account_uid, fingerprint)
            with locked(args.journal_dir, args.account_uid, fingerprint) as store:
                if not args.initialize_journal:
                    result = acquire()
                result['observation_sha256'] = import_observation(store, args.output_dir, result['bundle_sha256'])
                if args.establish_flat_baseline:
                    store.establish_flat_baseline()
                if args.reconcile:
                    ident, report = store.reconcile_position()
                    result.update(reconciliation_sha256=ident, quantity_reconciled=report['quantity_reconciled'],
                                  reconciliation_issue_count=len(report['issues']))
    except Exception:
        # Do not expose server payloads, credentials or request exception details.
        print('Demo capture failed; inspect retained private evidence. No orders were sent.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
