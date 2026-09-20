"""One mutation at a time from verified bracket requirements and fresh readback."""
from ledger import digest
from exchange_reconciliation import numeric, number
from kraken_execution import ExecutionError


def next_action(requirements, owned, open_orders, capture_sha256, *, pending_operations=(),
                stop_market_edit_verified=False):
    """Caller must persist action/ownership and obtain readback before next call.

    The capture hash must bind authenticated account, positions, fills, orders,
    reference time and effective terms. No action here independently authorizes
    an exchange request. Unknown prior attempts are never bypassed with new IDs.
    """
    import re
    if not re.fullmatch(r'[a-f0-9]{64}', capture_sha256):
        raise ExecutionError('Verified reconciliation capture hash required')
    if pending_operations or any(o['status'] in ('pending', 'unknown') for o in owned.values()):
        raise ExecutionError('Resolve prior mutation outcome before another action')
    current = {}
    by_role = {}
    for row in open_orders:
        if row['symbol'] != 'PF_DOTUSD': continue
        ident = row.get('cliOrdId')
        owner = owned.get(ident)
        if owner is None or owner['status'] != 'open' or ident in current:
            raise ExecutionError('Open orders disagree with durable ownership')
        if owner.get('exchange_order_id') != row['order_id']:
            raise ExecutionError('Open order exchange identity mismatch')
        if owner['role'] in by_role:
            raise ExecutionError('Duplicate open role requires explicit reconciliation')
        current[ident], by_role[owner['role']] = row, row
    for ident, owner in owned.items():
        if owner['status'] not in ('open', 'terminal') or (owner['status'] == 'open' and ident not in current):
            raise ExecutionError('Missing durable open order in readback')
    desired = {item['role']: item['params'] for item in requirements['desired_orders']}
    if len(desired) != len(requirements['desired_orders']):
        raise ExecutionError('Duplicate desired role')

    def result(endpoint, params, role):
        key = digest({'entry': requirements['entry_client_id'], 'capture': capture_sha256,
                      'endpoint': endpoint, 'params': params, 'role': role})
        if endpoint == 'sendorder':
            ident = 'ov4-' + key
            if ident in owned:
                raise ExecutionError('Capture already consumed for this send; require new reconciliation')
            params = {**params, 'cliOrdId': ident}
            operation = ident
        else:
            operation = 'ov4-op-' + key
        return {'operation_id': operation, 'endpoint': endpoint, 'params': params, 'role': role,
                'capture_sha256': capture_sha256, 'authorizes_execution': False}

    def cancel(ident):
        if ident not in current:
            raise ExecutionError('Cancellation lacks current owned open order')
        return result('cancelorder', {'cliOrdId': ident}, owned[ident]['role'])

    def adjust(role):
        params = desired[role]
        if params['symbol'] != 'PF_DOTUSD' or params['reduceOnly'] is not True:
            raise ExecutionError('Only reduce-only PF_DOTUSD exits allowed')
        numeric(params['size'], positive=True)
        row = by_role.get(role)
        if row is None:
            return result('sendorder', params, role)
        if row['side'] != params['side'] or row.get('reduceOnly') is not True:
            raise ExecutionError('Existing exit differs from protected direction')
        expected_type = params['orderType']
        actual_type = 'stp' if row['orderType'] == 'stop' else row['orderType']
        if actual_type != expected_type:
            raise ExecutionError('Cannot edit an exit into a different order type')
        if role == 'CLOSE':
            return None  # An acknowledged open close must be read back, not duplicated.
        remaining, filled = numeric(row['unfilledSize']), numeric(row['filledSize'])
        if remaining < 0 or filled < 0:
            raise ExecutionError('Negative native order quantity')
        price_key = 'stopPrice' if expected_type == 'stp' else 'limitPrice'
        if expected_type == 'stp' and row.get('triggerSignal') != 'mark':
            raise ExecutionError('Existing stop trigger is not mark')
        if remaining == numeric(params['size']) and numeric(row[price_key]) == numeric(params[price_key]):
            return None
        if expected_type == 'stp' and not stop_market_edit_verified:
            raise ExecutionError('Stop-market edit contract needs verified demo evidence')
        # Native edit size includes fills already executed on this order.
        edit = {'cliOrdId': row['cliOrdId'], 'size': number(filled + numeric(params['size'])),
                price_key: params[price_key]}
        return result('editorder', edit, role)

    cancel_ids = requirements['cancel_client_ids']
    entry = requirements['entry_client_id']
    if entry in cancel_ids:
        return cancel(entry)
    if 'CLOSE' in desired:
        action = adjust('CLOSE')
        if action is not None: return action
    if 'STOP' in desired:
        action = adjust('STOP')
        if action is not None: return action
    if cancel_ids:
        return cancel(sorted(cancel_ids)[0])
    for role in ('T1', 'T2', 'T3'):
        if role in desired:
            action = adjust(role)
            if action is not None: return action
    return None
