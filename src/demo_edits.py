"""Recover ordinary limit edits from bound history and exact current readback."""
from copy import deepcopy

from cycles import utc
from exchange_reconciliation import numeric
from kraken_execution import ExecutionError


def effective_params(state, client_id, *, exclude=None):
    owner = state['ownership'][client_id]
    params = deepcopy(state['operations'][owner['origin_operation_id']]['action']['params'])
    for ident, operation in state['operations'].items():
        action = operation['action']
        if ident == exclude or action['endpoint'] != 'editorder' or action['params']['cliOrdId'] != client_id:
            continue
        outcome = operation.get('outcome')
        if operation['status'] != 'resolved' or outcome not in ('not_dispatched', 'edit_applied', 'edit_rejected'):
            raise ExecutionError('Edited order needs effective-size recovery evidence')
        if outcome == 'edit_applied':
            params.update(action['params'])
    return params


def validate_edit(store, payload):
    state = store._state
    operation = state['operations'][payload['operation_id']]
    action, status = operation['action'], operation['status']
    if action['endpoint'] != 'editorder' or status not in ('unknown', 'acknowledged'):
        raise ExecutionError('Edit recovery requires an unresolved dispatched edit')
    if payload['capture_sha256'] != state['latest_capture']:
        raise ExecutionError('Edit recovery requires latest capture')
    edit = action['params']
    client_id = edit['cliOrdId']
    owner = state['ownership'][client_id]
    exchange_id = owner.get('exchange_order_id')
    if owner['status'] != 'open' or not exchange_id:
        raise ExecutionError('Edit recovery requires a previously proven open order')
    original = effective_params(state, client_id, exclude=payload['operation_id'])
    if (original['orderType'] != 'lmt' or not {'size', 'limitPrice'} & set(edit)
            or set(edit) - {'cliOrdId', 'size', 'limitPrice'}):
        raise ExecutionError('Only ordinary limit size/price edits can be recovered')
    expected = {**original, **edit}
    numeric(expected['size'], positive=True); numeric(expected['limitPrice'], positive=True)
    capture = store._read_artifact(payload['capture_sha256'])
    previous = store._read_artifact(action['capture_sha256'])
    start, end = utc(previous['reference_utc']), utc(capture['reference_utc'])
    history = store._read_artifact(payload['history_sha256'])
    if (capture.get('order_history_sha256') != payload['history_sha256'] or end <= start
            or history.get('source') != 'order_history' or history.get('environment') != 'demo'
            or history.get('account_uid') != store.identity['account_uid']
            or history.get('coverage_complete') is not True
            or utc(history['since_utc']) > start or utc(history['through_utc']) < end):
        raise ExecutionError('Complete bound edit-lifetime history required')
    events = [e for e in history['order_events'] if e['event_id'] == payload['event_id']]
    if len(events) != 1:
        raise ExecutionError('Unique edit history event required')
    event = events[0]
    if event['kind'] not in ('OrderUpdated', 'OrderEditRejected') or not start < utc(event['at_utc']) <= end:
        raise ExecutionError('History event does not prove this edit outcome')
    applied = event['kind'] == 'OrderUpdated'
    if payload['outcome'] != ('edit_applied' if applied else 'edit_rejected'):
        raise ExecutionError('Edit outcome differs from history')

    def terms(row, params):
        if (row['account_uid'] != store.identity['account_uid'] or row['order_id'] != exchange_id
                or row['client_id'] not in (None, client_id) or row['symbol'] != params['symbol']
                or row['direction'] != ('Buy' if params['side'] == 'buy' else 'Sell')
                or row['order_type'] != 'Limit' or row['reduce_only'] is not params['reduceOnly']
                or numeric(row['quantity']) != numeric(params['size'])
                or numeric(row['limit_price']) != numeric(params['limitPrice'])
                or not 0 <= numeric(row['filled']) <= numeric(row['quantity'])):
            raise ExecutionError('Historical edit identity or terms differ from persisted intent')

    terms(event['old_order'] if applied else event['order'], original)
    terms(event['order'] if applied else event['attempted_order'], expected)
    if applied and numeric(event['order']['filled']) < numeric(event['old_order']['filled']):
        raise ExecutionError('Edit history decreased cumulative filled quantity')
    for other in history['order_events']:
        if other is event or not start < utc(other['at_utc']) <= end:
            continue
        rows = [other.get(k, {}) for k in ('order', 'old_order', 'attempted_order')]
        if (other.get('order_id') == exchange_id or any(
                r.get('order_id') == exchange_id or r.get('client_id') == client_id for r in rows)):
            raise ExecutionError('Competing order history makes edit recovery ambiguous')
    # Recovery establishes effective terms only while the order remains open.
    # Terminal/activated orders need their separate lifecycle proof.
    final = expected if applied else original
    rows = [r for r in capture['open_orders'] if r.get('cliOrdId') == client_id or r['order_id'] == exchange_id]
    if len(rows) != 1:
        raise ExecutionError('Edit recovery needs unique current open-order readback')
    row = rows[0]
    if (row['order_id'] != exchange_id or row.get('cliOrdId') not in (None, client_id)
            or row['symbol'] != final['symbol'] or row['side'] != final['side']
            or row['orderType'] != 'lmt' or row['reduceOnly'] is not final['reduceOnly']
            or numeric(row['filledSize']) < numeric(event['order']['filled'])
            or numeric(row['unfilledSize']) <= 0
            or numeric(row['filledSize']) + numeric(row['unfilledSize']) != numeric(final['size'])
            or numeric(row['limitPrice']) != numeric(final['limitPrice'])):
        raise ExecutionError('Current readback contradicts recovered edit terms')
