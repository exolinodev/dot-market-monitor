"""Inspect or recover a demo journal from its retained evidence; never send."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from demo_journal import locked
from demo_position import acquired


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-dir', type=Path, required=True)
    parser.add_argument('--account-uid', required=True)
    parser.add_argument('--key-fingerprint', required=True, help='SHA-256 fingerprint, never an API key')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--abandon-prepared', metavar='OPERATION_ID',
                        help='Append an abandonment event only if dispatch never began')
    actions.add_argument('--resolve-present', metavar='OPERATION_ID', help='Prove a dispatched send is open')
    actions.add_argument('--resolve-filled', metavar='CLIENT_ID', help='Prove the entire order filled')
    actions.add_argument('--resolve-cancelled', metavar='CLIENT_ID', help='Prove ordinary order cancellation')
    actions.add_argument('--resolve-rejected', metavar='CLIENT_ID', help='Prove ordinary order rejection')
    actions.add_argument('--resolve-trigger-cancelled', metavar='CLIENT_ID', help='Prove unactivated stop cancellation')
    parser.add_argument('--capture-sha256', help='Expected latest retained observation; never imports evidence')
    parser.add_argument('--exchange-order-id', help='Exchange identity to prove against retained evidence')
    parser.add_argument('--event-id', help='Exact terminal order/trigger history event')
    args = parser.parse_args(argv)
    resolution = next(((kind, getattr(args, kind)) for kind in
                       ('resolve_present', 'resolve_filled', 'resolve_cancelled', 'resolve_rejected',
                        'resolve_trigger_cancelled') if getattr(args, kind)), None)
    terminal = resolution and resolution[0] in ('resolve_cancelled', 'resolve_rejected', 'resolve_trigger_cancelled')
    if resolution:
        if not args.capture_sha256 or not args.exchange_order_id or bool(args.event_id) != bool(terminal):
            parser.error('Resolution requires --capture-sha256 and --exchange-order-id; --event-id only for terminal history')
    elif any((args.capture_sha256, args.exchange_order_id, args.event_id)):
        parser.error('Evidence selectors require a resolution action')
    try:
        with locked(args.journal_dir, args.account_uid, args.key_fingerprint) as store:
            if args.abandon_prepared:
                store.abandon_prepared(args.abandon_prepared)
            if resolution:
                if args.capture_sha256 != store.state['latest_capture']:
                    raise ValueError('Resolution must use the expected latest observation')
                # History selection comes from the observation's own binding.
                # No external JSON or caller-selected history can replace it.
                capture = acquired(store, args.capture_sha256)
                kind, ident = resolution
                if kind == 'resolve_present':
                    store.resolve_present_send(ident, args.capture_sha256, args.exchange_order_id)
                elif kind == 'resolve_filled':
                    store.resolve_filled_order(ident, args.capture_sha256,
                                               capture['execution_history_sha256'], args.exchange_order_id)
                elif kind == 'resolve_trigger_cancelled':
                    store.resolve_cancelled_trigger(ident, args.capture_sha256,
                        capture['trigger_history_sha256'], args.event_id, args.exchange_order_id)
                else:
                    store.resolve_terminal_order(ident, args.capture_sha256,
                        capture['order_history_sha256'], args.event_id, args.exchange_order_id,
                        'cancelled' if kind == 'resolve_cancelled' else 'rejected')
            pending = [{'operation_id': ident, 'endpoint': op['action']['endpoint'],
                        'role': op['action']['role'], 'status': op['status'],
                        'can_abandon': op['status'] == 'prepared'}
                       for ident, op in sorted(store.state['operations'].items()) if op['status'] != 'resolved']
            result = {'journal_head_sha256': store.tip, 'unresolved_operations': pending,
                      'latest_capture_sha256': store.state['latest_capture'],
                      'resolution': None if resolution is None else {'kind': resolution[0], 'id': resolution[1]},
                      'abandoned_operation_id': args.abandon_prepared, 'authorizes_execution': False}
    except (ValueError, KeyError, OSError):
        print('Recovery refused; verify journal integrity, account identity and operation state. '
              'No exchange request was made.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
