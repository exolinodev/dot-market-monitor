"""Evidence-bound flat baseline and quantity reconciliation for the demo journal."""
from copy import deepcopy

from cycles import utc
from demo_observation import verify_journal_observation
from demo_edits import effective_params
from exchange_reconciliation import numeric, reconcile
from kraken_execution import ExecutionError


def acquired(store, capture_sha256):
    value = store._read_artifact(capture_sha256)
    if 'bundle_sha256' not in value:
        raise ExecutionError('Raw acquired observation required')
    verify_journal_observation(store, value)
    return value


def validate_baseline(store, capture_sha256):
    if store._state.get('baseline_capture') is not None or store._state['operations'] or store._state['ownership']:
        raise ExecutionError('Flat baseline is single-use and must precede all operations')
    if capture_sha256 != store._state['latest_capture']:
        raise ExecutionError('Baseline requires latest acquired observation')
    value = acquired(store, capture_sha256)
    if value['open_orders'] or any(numeric(p['size']) != 0 for p in value['positions']):
        raise ExecutionError('Demo baseline requires no open orders or positions in any instrument')


def control_hash(state):
    from demo_journal import sha
    return sha({key: value for key, value in state.items() if key != 'latest_reconciliation'})


def position_report(store):
    """Derive known intents from this journal, not from user-supplied quantities."""
    state = store._state
    baseline_sha = state.get('baseline_capture')
    if baseline_sha is None:
        raise ExecutionError('Establish an acquired flat baseline before reconciliation')
    baseline = acquired(store, baseline_sha)
    capture_sha = state['latest_capture']
    capture = acquired(store, capture_sha)
    start, end = utc(baseline['reference_utc']), utc(capture['reference_utc'])
    history = deepcopy(store._read_artifact(capture['execution_history_sha256']))
    if (start > end or history['coverage_complete'] is not True
            or utc(history['since_utc']) > start or utc(history['through_utc']) < end):
        raise ExecutionError('Execution history must cover the full baseline-to-observation interval')
    history['fills'] = [f for f in history['fills'] if start < utc(f['fillTime']) <= end]
    known, issues, origin_times = {}, [], {}
    for client_id, owner in sorted(state['ownership'].items()):
        operation = state['operations'][owner['origin_operation_id']]
        params = operation['action']['params']
        if operation.get('outcome') == 'not_dispatched':
            # Keep the identity consumed in the journal, but never whitelist it
            # as an exchange order. Any matching real order/fill stays unknown.
            continue
        if operation['status'] == 'prepared':
            issues.append({'kind': 'undispatched_order_intent', 'client_id': client_id})
            continue
        source = acquired(store, operation['action']['capture_sha256'])
        origin_times[client_id] = utc(source['reference_utc'])
        try:
            params = effective_params(state, client_id)
        except ExecutionError:
            issues.append({'kind': 'edited_order_terms_unverified', 'client_id': client_id})
        known[client_id] = {key: params[key] for key in ('symbol', 'side', 'size', 'reduceOnly')}
        if owner.get('exchange_order_id'):
            known[client_id]['exchange_order_id'] = owner['exchange_order_id']
        matches = [r for r in capture['open_orders'] if r.get('cliOrdId') == client_id
                   or (owner.get('exchange_order_id') and r['order_id'] == owner['exchange_order_id'])]
        if owner['status'] == 'open' and not matches:
            issues.append({'kind': 'missing_owned_open_order', 'client_id': client_id})
        if owner['status'] == 'terminal' and matches:
            issues.append({'kind': 'terminal_order_still_open', 'client_id': client_id})
    for ident, operation in sorted(state['operations'].items()):
        if operation['status'] != 'resolved':
            issues.append({'kind': 'unresolved_operation', 'operation_id': ident})
    reverse = {v['exchange_order_id']: k for k, v in known.items() if 'exchange_order_id' in v}
    for fill in history['fills']:
        if fill['symbol'] != 'PF_DOTUSD':
            issues.append({'kind': 'foreign_instrument_fill', 'fill_id': fill['fill_id']})
        client_id = fill['cliOrdId'] or reverse.get(fill['order_id'])
        if client_id in origin_times and utc(fill['fillTime']) <= origin_times[client_id]:
            issues.append({'kind': 'fill_predates_dispatch_observation', 'fill_id': fill['fill_id']})
    for row in capture['open_orders']:
        if row['symbol'] != 'PF_DOTUSD':
            issues.append({'kind': 'foreign_instrument_order', 'order_id': row['order_id']})
    for row in capture['positions']:
        if row['symbol'] != 'PF_DOTUSD' and numeric(row['size']) != 0:
            issues.append({'kind': 'foreign_instrument_position', 'symbol': row['symbol']})
    report = reconcile(history, known, capture['open_orders'], capture['positions'], starting_quantity='0')
    for row in capture['open_orders']:
        client_id = row.get('cliOrdId')
        if client_id in known:
            quantity = report['orders'].get(client_id, {}).get('filled_quantity', '0')
            if numeric(row['filledSize']) != numeric(quantity):
                issues.append({'kind': 'open_order_fill_history_mismatch', 'client_id': client_id})
    report['issues'].extend(issues)
    report['quantity_reconciled'] = not report['issues']
    report.update(account_uid=store.identity['account_uid'], baseline_capture_sha256=baseline_sha,
                  capture_sha256=capture_sha, control_sha256=control_hash(state), reference_utc=capture['reference_utc'])
    return report
