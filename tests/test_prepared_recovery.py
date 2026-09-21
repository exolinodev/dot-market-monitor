"""Crash before/after the durable dispatch barrier must have different outcomes."""
import importlib.util
import json
from pathlib import Path

import pytest

from demo_journal import initialize, locked
from kraken_execution import ExecutionError
from test_demo_journal import ACCOUNT, KEY, action, capture, row, start


def test_prepared_send_can_be_abandoned_after_restart_but_identity_stays_consumed(tmp_path):
    root = tmp_path/'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture())
        store.prepare(action(ref))
    with locked(root, ACCOUNT, KEY) as store:
        store.abandon_prepared('entry-1')
        expected = store.state
        assert expected['operations']['entry-1']['outcome'] == 'not_dispatched'
        assert expected['ownership']['entry-1']['terminal_reason'] == 'not_dispatched'
        with pytest.raises(ExecutionError, match='already dispatched'):
            store.before_dispatch('entry-1')
        with pytest.raises(ExecutionError, match='already recorded'):
            store.prepare(action(ref))
        with pytest.raises(ExecutionError, match='never-dispatched'):
            store.abandon_prepared('entry-1')
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected
        next_action = action(ref)
        next_action['operation_id'] = next_action['params']['cliOrdId'] = 'entry-2'
        store.prepare(next_action)


@pytest.mark.parametrize('acknowledged', [False, True])
def test_dispatch_barrier_forbids_abandonment_even_without_a_network_call(tmp_path, acknowledged):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        if acknowledged: store.response('entry-1', {'result': 'success'})
        before, tip = store.state, store.tip
        with pytest.raises(ExecutionError, match='never-dispatched'):
            store.abandon_prepared('entry-1')
        assert store.state == before and store.tip == tip


@pytest.mark.parametrize('endpoint', ['editorder', 'cancelorder'])
def test_abandoning_unsent_management_preserves_existing_order(tmp_path, endpoint):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture(2, [row()]))
        store.resolve_present_send('entry-1', ref, 'exchange-1')
        owner = store.state['ownership']['entry-1']
        management = {**action(ref), 'operation_id': 'management-1', 'endpoint': endpoint,
                      'params': {'cliOrdId': 'entry-1', 'size': '100'}}
        store.prepare(management)
        store.abandon_prepared('management-1')
        assert store.state['ownership']['entry-1'] == owner
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


def test_abandoned_send_never_whitelists_actual_exchange_activity(tmp_path):
    from test_demo_position import initialize_baseline, observe, Client, END, execution, position
    from test_demo_evidence import KEY as acquired_key
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, acquired_key) as store:
        store.prepare(action(store.state['latest_capture']))
        store.abandon_prepared('entry-1')
        _, flat = store.reconcile_position()
        assert flat['quantity_reconciled']
        assert flat['orders'] == {}
        observe(store, tmp_path/'unexpected', Client(END, [row()], [position()], [execution()]))
        _, report = store.reconcile_position()
        assert not report['quantity_reconciled']
        assert report['issues']


def test_abandoned_edit_does_not_poison_acquired_reconciliation(tmp_path):
    from test_demo_position import initialize_baseline, observe, Client, END, execution, position
    from test_demo_evidence import KEY as acquired_key
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, acquired_key) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        ref = observe(store, tmp_path/'partial', Client(END, [row()], [position()], [execution()]))
        store.resolve_present_send('entry-1', ref, 'exchange-1')
        management = {**action(ref), 'operation_id': 'edit-1', 'endpoint': 'editorder',
                      'params': {'cliOrdId': 'entry-1', 'size': '100'}}
        store.prepare(management); store.abandon_prepared('edit-1')
        ident, report = store.reconcile_position()
        assert report['quantity_reconciled']
    with locked(root, ACCOUNT, acquired_key) as store:
        assert store.state['latest_reconciliation'] == ident


def test_cli_defaults_to_inspection_and_refuses_unknown_dispatch(tmp_path, capsys):
    path = Path(__file__).resolve().parents[1]/'scripts/recover_demo_journal.py'
    spec = importlib.util.spec_from_file_location('recover_demo_cli_test', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    root = tmp_path/'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.capture(capture())))
        original = store.tip
    args = ['--journal-dir', str(root), '--account-uid', ACCOUNT, '--key-fingerprint', KEY]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['journal_head_sha256'] == original
    assert result['unresolved_operations'][0]['can_abandon']
    assert cli.main([*args, '--abandon-prepared', 'entry-1']) == 0
    assert json.loads(capsys.readouterr().out)['unresolved_operations'] == []
    with locked(root, ACCOUNT, KEY) as store:
        next_action = action(store.state['latest_capture'])
        next_action['operation_id'] = next_action['params']['cliOrdId'] = 'entry-2'
        store.prepare(next_action); store.before_dispatch('entry-2')
    assert cli.main([*args, '--abandon-prepared', 'entry-2']) == 1
    assert 'Recovery refused' in capsys.readouterr().err


def test_abandoned_edit_leaves_original_fill_size_authoritative(tmp_path):
    from test_demo_fill_recovery import observe, fill
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', ref, 'exchange-1')
        store.prepare({**action(ref), 'operation_id': 'edit-1', 'endpoint': 'editorder',
                       'params': {'cliOrdId': 'entry-1', 'size': '100'}})
        store.abandon_prepared('edit-1')
        proof = observe(store, [fill()])
        store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        assert store.state['ownership']['entry-1']['terminal_reason'] == 'filled'
        assert store.state['operations']['edit-1']['outcome'] == 'not_dispatched'
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


def test_abandoned_edit_cannot_expand_terminal_quantity_authorization(tmp_path):
    from test_order_history_recovery import proof
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', ref, 'exchange-1')
        store.prepare({**action(ref), 'operation_id': 'edit-1', 'endpoint': 'editorder',
                       'params': {'cliOrdId': 'entry-1', 'size': '100'}})
        store.abandon_prepared('edit-1')
        observed, orders = proof(store, tmp_path/'proof', quantity='100')
        with pytest.raises(ExecutionError, match='exceeds persisted authorization'):
            store.resolve_terminal_order('entry-1', observed, orders, 'event-1', 'exchange-1', 'cancelled')
