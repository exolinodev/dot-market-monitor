"""Verify published v4 plans; create isolated paper candidates or demo previews."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from order_executor import materialize, preview, run_paper


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('paper', 'demo', 'live'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--trusted-head', required=True)
    parser.add_argument('--forecast-id', required=True)
    parser.add_argument('--boundary', help='Closed paper market boundary to execute through')
    parser.add_argument('--output-dir', type=Path, help='New directory for isolated paper candidate')
    parser.add_argument('--journal-dir', type=Path, help='Existing private demo journal for entry preflight')
    parser.add_argument('--account-uid', help='Expected demo account UUID')
    parser.add_argument('--key-fingerprint', help='Expected SHA-256 of demo API key; not the key itself')
    parser.add_argument('--preview', action='store_true', help='Read-only demo request preview; never sends')
    args = parser.parse_args(argv)
    journal_options = (args.journal_dir, args.account_uid, args.key_fingerprint)
    if any(journal_options) and (args.mode != 'demo' or not args.preview or not all(journal_options)):
        parser.error('Journal preflight requires demo --preview plus journal, account UID and key fingerprint')
    if args.mode == 'live':
        parser.error('Live execution is unavailable: staged approvals and rollout gates are not implemented')
    if args.mode == 'demo' and not args.preview:
        parser.error('Demo sending is unavailable until durable mutation orchestration and demo gates are implemented; use --preview')
    if args.mode == 'paper':
        if not args.boundary or not args.output_dir or args.preview:
            parser.error('Paper requires --boundary and a new --output-dir, without --preview')
        result = run_paper(args.repo, args.trusted_head, args.forecast_id, args.boundary, args.output_dir)
    else:
        if args.boundary or args.output_dir:
            parser.error('Demo preview does not accept paper output/boundary arguments')
        with tempfile.TemporaryDirectory(prefix='oracle-demo-preview-') as temp:
            data = materialize(args.repo, args.trusted_head, temp)
            if args.journal_dir:
                from demo_journal import locked
                from demo_preflight import entry_preflight
                with locked(args.journal_dir, args.account_uid, args.key_fingerprint) as store:
                    result = entry_preflight(data, args.repo, args.trusted_head, args.forecast_id, store, datetime.now(timezone.utc))
            else:
                result = preview(data, args.repo, args.trusted_head, args.forecast_id, datetime.now(timezone.utc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
