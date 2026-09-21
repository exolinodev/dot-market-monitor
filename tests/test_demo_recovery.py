"""Unique acquired lifecycle proofs drive recovery; ambiguity never picks a side."""
import pytest

from demo_journal import locked
from demo_recovery import recover_available
from exchange_history import milliseconds
from test_demo_journal import action, row
from test_demo_position import Client, observe, initialize_baseline, execution, position, KEY
from test_exchange_history import ACCOUNT, END
from test_evidence_recovery_cli import HistoryClient, cli, args
from test_order_history_recovery import native_event
from test_trigger_history import event as trigger_event
from test_demo_edit_recovery import pending, EditClient, edit_event


@pytest.mark.parametrize('kind', ['present','filled','cancelled','rejected','trigger_cancelled'])
def test_supported_raw_proofs_recover_once_and_replay(tmp_path, kind):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        entry = action(store.state['latest_capture'])
        if kind == 'trigger_cancelled':
            entry['params'].update(orderType='stp', stopPrice='1.01', triggerSignal='mark')
            del entry['params']['limitPrice']
        store.prepare(entry); store.before_dispatch('entry-1')
        if kind == 'present':
            client = Client(END, [row()], [position()], [execution()])
        elif kind == 'filled':
            client = Client(END, [], [position('10')], [execution(size='10')])
        else:
            event = trigger_event() if kind=='trigger_cancelled' else native_event('OrderCancelled' if kind=='cancelled' else 'OrderRejected')
            event['timestamp'] = milliseconds('2026-09-21T00:30:00Z')
            client = HistoryClient('triggers' if kind=='trigger_cancelled' else 'orders', event)
        observe(store, tmp_path/'later', client)
        result = recover_available(store)
        assert len(result['resolved']) == 1 and not result['issues'] and not result['unresolved_operations']
        assert not result['authorizes_execution']
        tip, expected = store.tip, store.state
        assert recover_available(store)['resolved'] == []
        assert store.tip == tip
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected


@pytest.mark.parametrize('applied', [True,False])
def test_limit_edit_proof_is_selected_from_bound_history(tmp_path, applied):
    root = pending(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'edited', EditClient(edit_event(applied), applied=applied))
        result = recover_available(store)
        assert [r['event_type'] for r in result['resolved']] == ['edit_resolved']
        assert not result['unresolved_operations']
        assert store.state['operations']['edit-1']['outcome'] == ('edit_applied' if applied else 'edit_rejected')


@pytest.mark.parametrize('kind', ['empty','partial','prepared','conflicting'])
def test_absence_partial_fill_and_conflicting_proofs_cannot_resolve(tmp_path, kind):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture']))
        if kind != 'prepared': store.before_dispatch('entry-1')
        client = Client(END)
        if kind == 'partial':
            client.events, client.positions = [execution()], [position()]
        if kind == 'conflicting':
            event = native_event('OrderCancelled', filled='10')
            event['timestamp'] = milliseconds('2026-09-21T00:31:00Z')
            client = HistoryClient('orders', event)
            client.events, client.positions = [execution(size='10')], [position('10')]
        observe(store, tmp_path/'later', client)
        tip = store.tip
        result = recover_available(store)
        assert result['unresolved_operations'] == ['entry-1'] and not result['resolved']
        assert store.tip == tip
        if kind == 'conflicting':
            assert result['issues'][0]['reason'] == 'conflicting_positive_proofs'


def test_explicit_auto_recovery_cli_uses_retained_raw_evidence(tmp_path, cli, capsys):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        observe(store, tmp_path/'later', Client(END, [row()], [position()], [execution()]))
    assert cli.main([*args(root),'--recover-available']) == 0
    import json
    result = json.loads(capsys.readouterr().out)
    assert result['automatic_recovery']['resolved'][0]['event_type'] == 'resolve_send'
    assert not result['unresolved_operations'] and not result['authorizes_execution']
