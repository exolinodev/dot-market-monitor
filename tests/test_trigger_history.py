"""Synthetic stop-trigger history; activation is never mistaken for an exit."""
import pytest

from demo_journal import initialize, locked
from exchange_history import capture_triggers, verify_capture, milliseconds
from kraken_execution import ExecutionError
from test_exchange_history import History, body, ACCOUNT, START, END
from test_demo_journal import KEY, capture, action
from test_demo_fill_recovery import fill


def trigger(**changes):
    value = {'uid': 'trigger-1', 'accountUid': ACCOUNT, 'clientId': 'entry-1', 'tradeable': 'PF_DOTUSD',
             'direction': 'Buy', 'quantity': '10', 'limitPrice': '0', 'orderType': 'Stop', 'reduceOnly': False,
             'timestamp': milliseconds(START), 'lastUpdateTimestamp': milliseconds('2026-09-21T00:00:02Z'),
             'triggerOptions': {'triggerPrice': '1.01', 'triggerSignal': 'MarkPrice', 'triggerSide': 'Above',
                                'trailingStopOptions': None, 'limitPriceOffset': None}}
    value.update(changes); return value


def event(kind='OrderTriggerCancelled', ident='e1', **changes):
    value = trigger(**changes)
    details = {'order': value, 'reason': 'synthetic'}
    if kind == 'OrderTriggerUpdated': details = {'oldOrderTrigger': trigger(), 'newOrderTrigger': value}
    if kind == 'OrderTriggerEditRejected': details = {'oldOrderTrigger': trigger(), 'attemptedOrderTrigger': value, 'orderError': 'synthetic'}
    return {'uid': ident, 'timestamp': milliseconds('2026-09-21T00:00:02Z'), 'event': {kind: details}}


@pytest.mark.parametrize('kind', ['OrderTriggerPlaced', 'OrderTriggerCancelled', 'OrderTriggerUpdated', 'OrderTriggerActivated', 'OrderTriggerEditRejected'])
def test_trigger_variants_capture_replay_and_bind_source(tmp_path, kind):
    root = tmp_path/'history'; client = History([(body([event(kind)]), {})])
    report = capture_triggers(root, client, ACCOUNT, START, END)
    assert report['source'] == 'trigger_history' and report['event_count'] == 1
    assert report['trigger_events'][0]['kind'] == kind
    assert client.calls[0][0] == 'triggers' and client.calls[0][1]['closed'] is True
    assert verify_capture(root, ACCOUNT, START, END, source='trigger_history') == report
    with pytest.raises(ExecutionError, match='source differs'):
        verify_capture(root, ACCOUNT, START, END, source='order_history')


def start(root):
    initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture())
        entry = action(ref); entry['params'].update(orderType='stp', stopPrice='1.01', triggerSignal='mark')
        del entry['params']['limitPrice']
        store.prepare(entry); store.before_dispatch('entry-1')


def observe(store, root, events, fills=()):
    history = capture_triggers(root, History([(body(events), {})]), ACCOUNT, START, END)
    history_hash = store.artifact(history)
    executions = store.artifact({'source': 'execution_history', 'environment': 'demo', 'account_uid': ACCOUNT,
        'coverage_complete': True, 'since_utc': START, 'through_utc': END, 'fills': list(fills)})
    data = capture(3); data.update(trigger_history_sha256=history_hash, execution_history_sha256=executions)
    return store.capture(data), history_hash


def test_never_activated_cancelled_trigger_recovers_and_replays(tmp_path):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, tmp_path/'proof', [event()])
        store.resolve_cancelled_trigger('entry-1', *proof, 'e1', 'trigger-1')
        expected = store.state
        assert expected['ownership']['entry-1']['terminal_reason'] == 'cancelled'
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


def test_activation_is_not_terminal_and_blocks_later_cancellation_shortcut(tmp_path):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        activated = event('OrderTriggerActivated'); activated['timestamp'] -= 1
        proof = observe(store, tmp_path/'activated', [activated])
        with pytest.raises(ExecutionError, match='Explicit trigger cancellation'):
            store.resolve_cancelled_trigger('entry-1', *proof, 'e1', 'trigger-1')
        proof = observe(store, tmp_path/'race', [activated, event(ident='cancel')])
        with pytest.raises(ExecutionError, match='child-order'):
            store.resolve_cancelled_trigger('entry-1', *proof, 'cancel', 'trigger-1')


def test_fill_contradicts_unactivated_cancellation(tmp_path):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, tmp_path/'proof', [event()], [fill(order_id='trigger-1')])
        with pytest.raises(ExecutionError, match='has executions'):
            store.resolve_cancelled_trigger('entry-1', *proof, 'e1', 'trigger-1')


def test_wrong_account_and_trigger_policy_fail(tmp_path):
    wrong = event(accountUid='22222222-2222-4222-8222-222222222222')
    with pytest.raises(ExecutionError, match='different account'):
        capture_triggers(tmp_path/'wrong', History([(body([wrong]), {})]), ACCOUNT, START, END)
    root = tmp_path/'journal'; start(root)
    altered = trigger()['triggerOptions']; altered['triggerSignal'] = 'LastPrice'
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, tmp_path/'signal', [event(triggerOptions=altered)])
        with pytest.raises(ExecutionError, match='policy differs'):
            store.resolve_cancelled_trigger('entry-1', *proof, 'e1', 'trigger-1')
