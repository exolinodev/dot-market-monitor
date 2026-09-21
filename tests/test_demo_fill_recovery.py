"""Positive execution proof for restart recovery; no absence-based resolution."""

import pytest

from demo_journal import locked
from kraken_execution import ExecutionError
from test_demo_journal import ACCOUNT, KEY, action, capture, row, start


def fill(ident='f1', size='10', **changes):
    value = {'fill_id': ident, 'order_id': 'exchange-1', 'cliOrdId': 'entry-1',
             'fillTime': '2026-09-21T00:00:01Z', 'symbol': 'PF_DOTUSD',
             'side': 'buy', 'size': size, 'price': '1', 'fillType': 'maker'}
    value.update(changes)
    return value


def observe(store, fills, *, opens=(), **history_changes):
    history = {'source': 'execution_history', 'environment': 'demo', 'account_uid': ACCOUNT,
               'coverage_complete': True, 'since_utc': '2026-09-21T00:00:00Z',
               'through_utc': '2026-09-21T00:00:03Z', 'fills': fills}
    history.update(history_changes)
    history_hash = store.artifact(history)
    observed = capture(3, opens); observed['execution_history_sha256'] = history_hash
    capture_hash = store.capture(observed)
    return capture_hash, history_hash


def test_timeout_after_full_execution_resolves_and_replays_from_exact_partial_fills(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, [fill('a', '4'), fill('b', '6'), fill('a', '4')])
        store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        state = store.state
        assert state['ownership']['entry-1']['status'] == 'terminal'
        assert state['ownership']['entry-1']['terminal_reason'] == 'filled'
        assert state['operations']['entry-1']['outcome'] == 'filled'
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == state
        with pytest.raises(ExecutionError, match='already dispatched'):
            store.before_dispatch('entry-1')


def test_previously_open_order_can_become_terminal_without_an_outstanding_send(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        appeared = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', appeared, 'exchange-1')
        proof = observe(store, [fill()])
        store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        assert store.state['ownership']['entry-1']['status'] == 'terminal'


@pytest.mark.parametrize('fills', [[], [fill(size='9')], [fill(size='11')]])
def test_empty_partial_and_overfilled_orders_never_resolve(tmp_path, fills):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, fills)
        with pytest.raises(ExecutionError, match='do not equal'):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        assert store.state['operations']['entry-1']['status'] == 'unknown'


@pytest.mark.parametrize('change', [{'coverage_complete': False}, {'account_uid': '22222222-2222-4222-8222-222222222222'},
                                   {'since_utc': '2026-09-21T00:00:01Z'}, {'through_utc': '2026-09-21T00:00:02Z'}])
def test_incomplete_wrong_account_or_short_history_fails(tmp_path, change):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, [fill()], **change)
        with pytest.raises(ExecutionError):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')


def test_fill_identity_conflicts_and_still_open_order_fail(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, [fill(cliOrdId='someone-else')])
        with pytest.raises(ExecutionError, match='identity or economics'):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        proof = observe(store, [fill()], opens=[row()])
        with pytest.raises(ExecutionError, match='still open'):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')


def test_fill_completion_can_resolve_cancel_that_lost_the_race(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        observed = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', observed, 'exchange-1')
        cancel = {**action(observed), 'operation_id': 'cancel-1', 'endpoint': 'cancelorder', 'params': {'cliOrdId': 'entry-1'}}
        store.prepare(cancel); store.before_dispatch('cancel-1')
        proof = observe(store, [fill()])
        store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        assert store.state['operations']['cancel-1']['outcome'] == 'order_already_filled'
        assert store.state['operations']['cancel-1']['status'] == 'resolved'


def test_unknown_edit_cannot_be_hidden_by_original_size_fills(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        observed = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', observed, 'exchange-1')
        edit = {**action(observed), 'operation_id': 'edit-1', 'endpoint': 'editorder',
                'params': {'cliOrdId': 'entry-1', 'size': '20', 'limitPrice': '1'}}
        store.prepare(edit); store.before_dispatch('edit-1')
        proof = observe(store, [fill()])
        with pytest.raises(ExecutionError, match='effective-size'):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        assert store.state['operations']['edit-1']['status'] == 'unknown'


def test_history_artifact_substitution_and_replay_tamper_fail(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, [fill()])
        alternate = store.artifact({'fills': []})
        with pytest.raises(ExecutionError, match='not bound'):
            store.resolve_filled_order('entry-1', proof[0], alternate, 'exchange-1')
        store.resolve_filled_order('entry-1', *proof, 'exchange-1')
    (root / 'artifacts' / (proof[1] + '.json')).write_text('{}')
    with pytest.raises(ExecutionError, match='artifact hash'):
        with locked(root, ACCOUNT, KEY): pass



def test_missing_client_id_requires_prior_proven_exchange_identity(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        proof = observe(store, [fill(cliOrdId=None)])
        with pytest.raises(ExecutionError, match='No proven link'):
            store.resolve_filled_order('entry-1', *proof, 'exchange-1')
        observed = store.capture(capture(4, [row()]))
        store.resolve_present_send('entry-1', observed, 'exchange-1')
        history = {'source': 'execution_history', 'environment': 'demo', 'account_uid': ACCOUNT,
                   'coverage_complete': True, 'since_utc': '2026-09-21T00:00:00Z',
                   'through_utc': '2026-09-21T00:00:05Z', 'fills': [fill(cliOrdId=None)]}
        history_hash = store.artifact(history)
        snapshot = capture(5); snapshot['execution_history_sha256'] = history_hash
        ref = store.capture(snapshot)
        store.resolve_filled_order('entry-1', ref, history_hash, 'exchange-1')
        assert store.state['ownership']['entry-1']['status'] == 'terminal'
