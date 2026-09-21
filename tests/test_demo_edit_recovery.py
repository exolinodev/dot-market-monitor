"""Unknown limit edits recover only with intent, history and current raw readback."""
from copy import deepcopy
import json

import pytest

from demo_edits import effective_params
from demo_journal import locked
from exchange_history import milliseconds
from kraken_execution import ExecutionError
from test_demo_journal import action, row
from test_demo_position import Client, observe, initialize_baseline, execution, position, KEY
from test_exchange_history import ACCOUNT, DATE, END, body
from test_order_history_recovery import native_event
from test_evidence_recovery_cli import cli, args

EDIT_AT = '2026-09-21T00:40:00Z'
OBSERVED = '2026-09-21T00:45:00Z'


def edit_event(applied=True):
    value = native_event('OrderUpdated' if applied else 'OrderEditRejected', quantity='20', filled='2', limitPrice='1.1')
    value['timestamp'] = milliseconds(EDIT_AT)
    detail = next(iter(value['event'].values()))
    detail['oldOrder']['filled'] = '2'
    return value


class EditClient(Client):
    def __init__(self, event, *, applied=True, rows=None):
        super().__init__(OBSERVED, rows if rows is not None else [row(
            unfilledSize='18' if applied else '8', limitPrice='1.1' if applied else '1')],
            [position()], [execution()])
        self.events_order = [event]

    def history(self, endpoint, params):
        if endpoint == 'orders':
            return 200, json.dumps(body(self.events_order)).encode(), {'Date': DATE}
        return super().history(endpoint, params)


def pending(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        ref = observe(store, tmp_path/'open', Client('2026-09-21T00:35:00Z', [row()], [position()], [execution()]))
        store.resolve_present_send('entry-1', ref, 'exchange-1')
        store.prepare({**action(ref), 'operation_id': 'edit-1', 'endpoint': 'editorder',
                       'params': {'cliOrdId': 'entry-1', 'size': '20', 'limitPrice': '1.1'}})
        store.before_dispatch('edit-1')
    return root


@pytest.mark.parametrize('applied', [True, False])
def test_raw_cli_edit_recovery_reconciles_then_full_fill_uses_effective_size(tmp_path, cli, capsys, applied):
    root = pending(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'edited', EditClient(edit_event(applied), applied=applied))
        _, report = store.reconcile_position()
        assert not report['quantity_reconciled']
    outcome = 'applied' if applied else 'rejected'
    command = [*args(root), '--resolve-edit-' + outcome, 'edit-1', '--capture-sha256', ref,
               '--event-id', 'event-1', '--exchange-order-id', 'exchange-1']
    assert cli.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result['authorizes_execution'] and not result['unresolved_operations']
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state['latest_reconciliation'] is None
        assert effective_params(store.state, 'entry-1')['size'] == ('20' if applied else '10')
        _, report = store.reconcile_position()
        assert report['quantity_reconciled']
        second = execution(size='18' if applied else '8')
        second['uid'] = 'e2'
        second['timestamp'] = milliseconds('2026-09-21T00:50:00Z')
        fill = second['event']['execution']['execution']
        fill.update(uid='fill-e2', timestamp=second['timestamp'])
        ref = observe(store, tmp_path/'filled', Client(END, [], [position('20' if applied else '10')], [execution(), second]))
        capture = store._read_artifact(ref)
        store.resolve_filled_order('entry-1', ref, capture['execution_history_sha256'], 'exchange-1')
        _, report = store.reconcile_position()
        assert report['quantity_reconciled']
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


@pytest.mark.parametrize('failure', ['old_size', 'new_price', 'identity', 'side', 'policy', 'too_early',
                                     'competing_event', 'readback_size', 'not_open', 'wrong_outcome', 'decreased_fills'])
def test_conflicting_edit_evidence_leaves_operation_unknown(tmp_path, failure):
    root = pending(tmp_path)
    value = edit_event()
    detail = value['event']['OrderUpdated']
    if failure == 'old_size': detail['oldOrder']['quantity'] = '9'
    if failure == 'new_price': detail['newOrder']['limitPrice'] = '1.2'
    if failure == 'identity': detail['newOrder']['clientId'] = 'manual'
    if failure == 'side': detail['newOrder']['direction'] = 'Sell'
    if failure == 'policy': detail['newOrder']['reduceOnly'] = True
    if failure == 'decreased_fills': detail['oldOrder']['filled'] = '3'
    if failure == 'too_early': value['timestamp'] = milliseconds('2026-09-21T00:34:00Z')
    client = EditClient(value, rows=[] if failure == 'not_open' else None)
    if failure == 'readback_size': client.orders[0]['unfilledSize'] = '17'
    if failure == 'competing_event':
        other = deepcopy(value); other['uid'] = 'another-event'
        client.events_order.append(other)
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'later', client)
        history = store._read_artifact(ref)['order_history_sha256']
        before = store.tip
        with pytest.raises(ExecutionError):
            store.resolve_limit_edit('edit-1', ref, history, 'event-1',
                                     'edit_rejected' if failure == 'wrong_outcome' else 'edit_applied')
        assert store.tip == before
        assert store.state['operations']['edit-1']['status'] == 'unknown'
        with pytest.raises(ExecutionError, match='effective-size'):
            effective_params(store.state, 'entry-1')


def test_resolution_cannot_repeat(tmp_path):
    root = pending(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'later', EditClient(edit_event()))
        history = store._read_artifact(ref)['order_history_sha256']
        store.resolve_limit_edit('edit-1', ref, history, 'event-1', 'edit_applied')
        before = store.tip
        with pytest.raises(ExecutionError, match='unresolved dispatched'):
            store.resolve_limit_edit('edit-1', ref, history, 'event-1', 'edit_applied')
        assert store.tip == before


def test_successive_size_and_price_edits_follow_proven_effective_terms(tmp_path):
    root = pending(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        ref = observe(store, tmp_path/'first', EditClient(edit_event()))
        history = store._read_artifact(ref)['order_history_sha256']
        store.resolve_limit_edit('edit-1', ref, history, 'event-1', 'edit_applied')
        store.prepare({**action(ref), 'operation_id': 'edit-2', 'endpoint': 'editorder',
                       'params': {'cliOrdId': 'entry-1', 'size': '15', 'limitPrice': '1.2'}})
        store.before_dispatch('edit-2')
        value = edit_event(); value['uid'] = 'event-2'
        value['timestamp'] = milliseconds('2026-09-21T00:47:00Z')
        detail = value['event']['OrderUpdated']
        detail['oldOrder'].update(quantity='20', limitPrice='1.1')
        detail['newOrder'].update(quantity='15', limitPrice='1.2')
        client = EditClient(value, rows=[row(unfilledSize='13', limitPrice='1.2')])
        client.at = '2026-09-21T00:49:00Z'
        client.events_order.insert(0, edit_event())
        second = observe(store, tmp_path/'second', client)
        history = store._read_artifact(second)['order_history_sha256']
        with pytest.raises(ExecutionError):
            store.resolve_limit_edit('edit-2', ref, history, 'event-2', 'edit_applied')
        store.resolve_limit_edit('edit-2', second, history, 'event-2', 'edit_applied')
        effective = effective_params(store.state, 'entry-1')
        assert effective['size'] == '15' and effective['limitPrice'] == '1.2'
        _, report = store.reconcile_position()
        assert report['quantity_reconciled']
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected
