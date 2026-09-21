"""API-shaped order-history evidence and control-journal terminal recovery."""

import pytest

from demo_journal import locked
from exchange_history import capture_orders, verify_capture, milliseconds
from kraken_execution import ExecutionError
from test_exchange_history import History, body, ACCOUNT, START, END
from test_demo_journal import KEY, start, capture
from test_demo_fill_recovery import fill


def native_order(**changes):
    row = {'uid': 'exchange-1', 'accountUid': ACCOUNT, 'clientId': 'entry-1',
           'tradeable': 'PF_DOTUSD', 'direction': 'Buy', 'quantity': '10', 'filled': '0',
           'limitPrice': '1', 'reduceOnly': False, 'orderType': 'Limit',
           'timestamp': milliseconds(START), 'lastUpdateTimestamp': milliseconds('2026-09-21T00:00:02Z')}
    row.update(changes)
    return row


def native_event(kind='OrderCancelled', ident='event-1', **changes):
    row = native_order(**changes)
    details = {'order': row, 'reason': 'synthetic', 'orderError': 'synthetic'}
    if kind == 'OrderUpdated': details = {'oldOrder': native_order(), 'newOrder': row}
    if kind == 'OrderEditRejected': details = {'oldOrder': native_order(), 'attemptedOrder': row}
    if kind == 'OrderNotFound': details = {'accountUid': ACCOUNT, 'orderId': 'exchange-1'}
    return {'uid': ident, 'timestamp': milliseconds('2026-09-21T00:00:02Z'), 'event': {kind: details}}


@pytest.mark.parametrize('kind', ['OrderPlaced', 'OrderUpdated', 'OrderRejected', 'OrderCancelled', 'OrderEditRejected', 'OrderNotFound'])
def test_order_variants_capture_and_replay_with_explicit_source(tmp_path, kind):
    root = tmp_path / 'orders'
    client = History([(body([native_event(kind)]), {})])
    report = capture_orders(root, client, ACCOUNT, START, END)
    assert report['source'] == 'order_history' and report['coverage_complete']
    assert report['order_events'][0]['kind'] == kind
    assert client.calls[0][0] == 'orders'
    assert client.calls[0][1]['opened'] is True and client.calls[0][1]['closed'] is True
    assert verify_capture(root, ACCOUNT, START, END, source='order_history') == report
    with pytest.raises(ExecutionError, match='source differs'):
        verify_capture(root, ACCOUNT, START, END)


def test_nested_wrong_account_and_ambiguous_variants_fail(tmp_path):
    wrong = native_event(accountUid='22222222-2222-4222-8222-222222222222')
    with pytest.raises(ExecutionError, match='different account'):
        capture_orders(tmp_path/'wrong', History([(body([wrong]), {})]), ACCOUNT, START, END)
    mixed = native_event(); mixed['event']['OrderRejected'] = mixed['event']['OrderCancelled']
    with pytest.raises(ExecutionError, match='ambiguous'):
        capture_orders(tmp_path/'mixed', History([(body([mixed]), {})]), ACCOUNT, START, END)


def proof(store, tmp_path, kind='OrderCancelled', fills=(), **changes):
    report = capture_orders(tmp_path, History([(body([native_event(kind, **changes)]), {})]), ACCOUNT, START, END)
    orders_hash = store.artifact(report)
    execution = {'source': 'execution_history', 'environment': 'demo', 'account_uid': ACCOUNT,
                 'coverage_complete': True, 'since_utc': START, 'through_utc': END, 'fills': list(fills)}
    execution_hash = store.artifact(execution)
    observed = capture(3)
    observed.update(order_history_sha256=orders_hash, execution_history_sha256=execution_hash)
    observation = store.capture(observed)
    return observation, orders_hash


@pytest.mark.parametrize('kind,reason', [('OrderCancelled', 'cancelled'), ('OrderRejected', 'rejected')])
def test_explicit_terminal_event_recovers_unknown_send_and_replays(tmp_path, kind, reason):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        observed, orders = proof(store, tmp_path/'proof', kind)
        store.resolve_terminal_order('entry-1', observed, orders, 'event-1', 'exchange-1', reason)
        expected = store.state
        assert expected['ownership']['entry-1']['terminal_reason'] == reason
        assert expected['operations']['entry-1']['status'] == 'resolved'
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


def test_partial_fill_cancellation_requires_exact_execution_count(tmp_path):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        observed, orders = proof(store, tmp_path/'bad', filled='4')
        with pytest.raises(ExecutionError, match='differs from actual fills'):
            store.resolve_terminal_order('entry-1', observed, orders, 'event-1', 'exchange-1', 'cancelled')
        observed, orders = proof(store, tmp_path/'good', fills=[fill(size='4')], filled='4')
        store.resolve_terminal_order('entry-1', observed, orders, 'event-1', 'exchange-1', 'cancelled')
        assert store.state['ownership']['entry-1']['status'] == 'terminal'


def test_not_found_and_edit_rejection_are_not_terminal_evidence(tmp_path):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        for kind in ('OrderNotFound', 'OrderEditRejected', 'OrderUpdated'):
            observed, orders = proof(store, tmp_path/kind, kind)
            with pytest.raises(ExecutionError, match='does not prove'):
                store.resolve_terminal_order('entry-1', observed, orders, 'event-1', 'exchange-1', 'cancelled')
        assert store.state['operations']['entry-1']['status'] == 'unknown'


def test_later_update_prevents_stale_terminal_resolution(tmp_path):
    root = tmp_path/'journal'; start(root)
    events = [native_event(), native_event('OrderUpdated', ident='later')]
    events[1]['timestamp'] += 1
    report = capture_orders(tmp_path/'proof', History([(body(events), {})]), ACCOUNT, START, END)
    with locked(root, ACCOUNT, KEY) as store:
        orders = store.artifact(report)
        execution = store.artifact({'source': 'execution_history', 'environment': 'demo', 'account_uid': ACCOUNT,
            'coverage_complete': True, 'since_utc': START, 'through_utc': END, 'fills': []})
        observed = capture(3); observed.update(order_history_sha256=orders, execution_history_sha256=execution)
        ref = store.capture(observed)
        with pytest.raises(ExecutionError, match='superseded'):
            store.resolve_terminal_order('entry-1', ref, orders, 'event-1', 'exchange-1', 'cancelled')
