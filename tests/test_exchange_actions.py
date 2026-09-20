import pytest

from exchange_actions import next_action
from kraken_execution import ExecutionError

HASH = 'a' * 64


def required():
    common = {'symbol': 'PF_DOTUSD', 'side': 'sell', 'reduceOnly': True}
    return {'entry_client_id': 'entry', 'cancel_client_ids': [], 'desired_orders': [
        {'role': 'STOP', 'params': {**common, 'orderType': 'stp', 'size': '10', 'stopPrice': '.99', 'triggerSignal': 'mark'}},
        {'role': 'T1', 'params': {**common, 'orderType': 'lmt', 'size': '4', 'limitPrice': '1.025'}}]}


def opened(role='STOP', ident='stop', **changes):
    item = {'cliOrdId': ident, 'order_id': ident + '-exchange', 'symbol': 'PF_DOTUSD', 'side': 'sell',
        'reduceOnly': True, 'orderType': 'stop' if role == 'STOP' else 'lmt', 'triggerSignal': 'mark',
        'unfilledSize': '10' if role == 'STOP' else '4', 'filledSize': '0', 'stopPrice': '.99', 'limitPrice': '1.025'}
    item.update(changes)
    return item


def owner(role, row):
    return {row['cliOrdId']: {'role': role, 'status': 'open', 'exchange_order_id': row['order_id']}}


def test_stop_creation_is_stable_then_readback_allows_next_target():
    req = required()
    action = next_action(req, {}, [], HASH)
    assert action['endpoint'] == 'sendorder' and action['role'] == 'STOP'
    assert action == next_action(req, {}, [], HASH)
    assert action['operation_id'] == action['params']['cliOrdId'] and len(action['operation_id']) <= 100
    row = opened(ident=action['params']['cliOrdId'])
    following = next_action(req, owner('STOP', row), [row], 'b'*64)
    assert following['role'] == 'T1'
    with pytest.raises(ExecutionError, match='prior mutation'):
        next_action(req, {}, [], HASH, pending_operations=['unresolved-send'])


def test_edit_total_size_includes_already_executed_target_quantity():
    req = required(); stop = opened(); target = opened('T1', 't1', unfilledSize='3', filledSize='2')
    action = next_action(req, {**owner('STOP', stop), **owner('T1', target)}, [stop, target], HASH)
    assert action['endpoint'] == 'editorder' and action['params']['size'] == '6'
    assert action['params']['limitPrice'] == '1.025'


def test_stop_edit_requires_demo_contract_and_never_invents_limit_price():
    req = required(); row = opened(unfilledSize='8')
    with pytest.raises(ExecutionError, match='verified demo evidence'):
        next_action(req, owner('STOP', row), [row], HASH)
    action = next_action(req, owner('STOP', row), [row], HASH, stop_market_edit_verified=True)
    assert action['endpoint'] == 'editorder' and action['params']['stopPrice'] == '.99'
    assert 'limitPrice' not in action['params']


def test_cancel_entry_precedes_remaining_close_and_close_precedes_targets():
    req = required()
    req['cancel_client_ids'] = ['entry']
    req['desired_orders'].append({'role': 'CLOSE', 'params': {'symbol': 'PF_DOTUSD', 'side': 'sell', 'reduceOnly': True, 'size': '10', 'orderType': 'mkt'}})
    row = opened('ENTRY', 'entry', side='buy', reduceOnly=False)
    action = next_action(req, owner('ENTRY', row), [row], HASH)
    assert action['endpoint'] == 'cancelorder' and action['params']['cliOrdId'] == 'entry'
    req['cancel_client_ids'] = []
    action = next_action(req, {}, [], 'b'*64)
    assert action['role'] == 'CLOSE' and action['params']['reduceOnly']


def test_missing_open_order_and_reused_capture_never_create_duplicate():
    req = required(); row = opened()
    with pytest.raises(ExecutionError, match='Missing durable'):
        next_action(req, owner('STOP', row), [], HASH)
    action = next_action(req, {}, [], HASH)
    old = {action['params']['cliOrdId']: {'role': 'STOP', 'status': 'terminal'}}
    with pytest.raises(ExecutionError, match='already consumed'):
        next_action(req, old, [], HASH)


def test_converged_orders_need_no_action_and_flat_cleans_sibling():
    req = required(); stop = opened(); target = opened('T1', 't1')
    owns = {**owner('STOP', stop), **owner('T1', target)}
    assert next_action(req, owns, [stop, target], HASH) is None
    flat = {'entry_client_id': 'entry', 'cancel_client_ids': ['stop', 't1'], 'desired_orders': []}
    assert next_action(flat, owns, [stop, target], HASH)['endpoint'] == 'cancelorder'
