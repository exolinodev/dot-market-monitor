"""Unactivated stop edits require matching acquired trigger history and readback."""
from copy import deepcopy
import json
import pytest

from demo_edits import effective_params
from demo_journal import locked
from demo_recovery import recover_available
from exchange_history import milliseconds
from kraken_execution import ExecutionError
from test_demo_journal import action, row
from test_demo_position import Client, observe, initialize_baseline, execution, KEY
from test_exchange_history import ACCOUNT, DATE, body
from test_trigger_history import event
from test_evidence_recovery_cli import cli, args


def stop_row(size='10', price='1.01', **changes):
    result = row(order_id='trigger-1', orderType='stop', filledSize='0', unfilledSize=size,
                 stopPrice=price, triggerSignal='mark')
    del result['limitPrice']
    result.update(changes)
    return result


def stop_event(applied=True):
    value = event('OrderTriggerUpdated' if applied else 'OrderTriggerEditRejected', quantity='8')
    value['timestamp'] = milliseconds('2026-09-21T00:40:00Z')
    detail = next(iter(value['event'].values()))
    detail['newOrderTrigger' if applied else 'attemptedOrderTrigger']['triggerOptions']['triggerPrice'] = '1.02'
    return value


class StopClient(Client):
    def __init__(self, value, applied=True):
        super().__init__('2026-09-21T00:45:00Z',
                         [stop_row('8', '1.02') if applied else stop_row()])
        self.triggers = [value]
    def history(self, endpoint, params):
        if endpoint == 'triggers':
            return 200, json.dumps(body(self.triggers)).encode(), {'Date': DATE}
        return super().history(endpoint, params)


def pending_stop(tmp_path, protective=False):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        entry = action(store.state['latest_capture'])
        entry['params'].update(orderType='stp', stopPrice='1.01', triggerSignal='mark')
        del entry['params']['limitPrice']
        if protective:
            entry['role'] = 'STOP'
            entry['params'].update(side='sell', reduceOnly=True)
        store.prepare(entry); store.before_dispatch('entry-1')
        ref = observe(store, tmp_path/'open', Client('2026-09-21T00:35:00Z', [stop_row(side='sell', reduceOnly=True) if protective else stop_row()]))
        store.resolve_present_send('entry-1', ref, 'trigger-1')
        store.prepare({**action(ref), 'operation_id': 'edit-1', 'endpoint': 'editorder', 'role': 'STOP' if protective else 'ENTRY',
                       'params': {'cliOrdId': 'entry-1', 'size': '8', 'stopPrice': '1.02'}})
        store.before_dispatch('edit-1')
    return root


@pytest.mark.parametrize('applied', [True, False])
@pytest.mark.parametrize('automatic', [True, False])
def test_raw_stop_edit_recovery_replay_and_cli(tmp_path, cli, capsys, applied, automatic):
    root = pending_stop(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'edited', StopClient(stop_event(applied), applied))
        if automatic:
            result = recover_available(store)
            assert len(result['resolved']) == 1 and not result['unresolved_operations']
            tip = store.tip
            assert recover_available(store)['resolved'] == [] and store.tip == tip
    if not automatic:
        assert cli.main([*args(root), '--resolve-edit-' + ('applied' if applied else 'rejected'), 'edit-1',
                         '--capture-sha256', ref, '--event-id', 'e1', '--exchange-order-id', 'trigger-1']) == 0
        assert not json.loads(capsys.readouterr().out)['unresolved_operations']
    with locked(root, ACCOUNT, KEY) as store:
        assert effective_params(store.state, 'entry-1')['size'] == ('8' if applied else '10')
        assert effective_params(store.state, 'entry-1')['stopPrice'] == ('1.02' if applied else '1.01')
        assert store.reconcile_position()[1]['quantity_reconciled']
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected


@pytest.mark.parametrize('fault', ['old_size', 'new_price', 'client', 'side', 'reduce_only', 'signal',
                                  'trigger_side', 'limit', 'trailing', 'offset', 'early', 'activation',
                                  'competing', 'absent', 'readback_price', 'readback_size',
                                  'readback_limit', 'readback_signal', 'readback_fill', 'execution', 'outcome'])
def test_contradictory_or_activated_stop_edit_cannot_resolve(tmp_path, fault):
    root = pending_stop(tmp_path)
    value = stop_event(); detail = value['event']['OrderTriggerUpdated']
    new = detail['newOrderTrigger']
    if fault == 'old_size': detail['oldOrderTrigger']['quantity'] = '11'
    if fault == 'new_price': new['triggerOptions']['triggerPrice'] = '1.03'
    if fault == 'client': new['clientId'] = 'foreign'
    if fault == 'side': new['direction'] = 'Sell'
    if fault == 'reduce_only': new['reduceOnly'] = True
    if fault == 'signal': new['triggerOptions']['triggerSignal'] = 'LastPrice'
    if fault == 'trigger_side': new['triggerOptions']['triggerSide'] = 'Below'
    if fault == 'limit': new['limitPrice'] = '1.03'
    if fault == 'trailing': new['triggerOptions']['trailingStopOptions'] = {}
    if fault == 'offset': new['triggerOptions']['limitPriceOffset'] = '0.1'
    if fault == 'early': value['timestamp'] = milliseconds('2026-09-21T00:34:00Z')
    client = StopClient(value)
    if fault in ('activation', 'competing'):
        other = event('OrderTriggerActivated', ident='activated') if fault == 'activation' else deepcopy(value)
        other['uid'] = 'other'; other['timestamp'] = value['timestamp'] + 1
        client.triggers.append(other)
    if fault == 'absent': client.orders = []
    if fault == 'readback_price': client.orders[0]['stopPrice'] = '1.03'
    if fault == 'readback_size': client.orders[0]['unfilledSize'] = '7'
    if fault == 'readback_limit': client.orders[0]['limitPrice'] = '1.03'
    if fault == 'readback_signal': client.orders[0]['triggerSignal'] = 'last'
    if fault == 'readback_fill': client.orders[0]['filledSize'] = '1'
    if fault == 'execution': client.events = [execution()]
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'later', client)
        history = store._read_artifact(ref)['trigger_history_sha256']
        tip = store.tip
        with pytest.raises(ExecutionError):
            store.resolve_limit_edit('edit-1', ref, history, 'e1',
                                     'edit_rejected' if fault == 'outcome' else 'edit_applied')
        assert store.tip == tip
        if fault != 'outcome':
            assert not recover_available(store)['resolved']
        assert store.state['operations']['edit-1']['status'] == 'unknown'


@pytest.mark.parametrize('applied', [True, False])
def test_reduce_only_sell_stop_recovers_without_changing_policy(tmp_path, applied):
    root = pending_stop(tmp_path, protective=True)
    value = stop_event(applied)
    detail = next(iter(value['event'].values()))
    for key in ('oldOrderTrigger', 'newOrderTrigger', 'attemptedOrderTrigger'):
        if key in detail:
            detail[key].update(direction='Sell', reduceOnly=True)
            detail[key]['triggerOptions']['triggerSide'] = 'Below'
    client = StopClient(value, applied)
    client.orders[0].update(side='sell', reduceOnly=True)
    with locked(root, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'edited', client)
        result = recover_available(store)
        assert len(result['resolved']) == 1 and not result['unresolved_operations']
        final = effective_params(store.state, 'entry-1')
        assert final['reduceOnly'] is True and final['side'] == 'sell'
        assert final['stopPrice'] == ('1.02' if applied else '1.01')
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected
