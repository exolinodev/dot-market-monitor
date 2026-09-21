"""Durability and restart invariants in isolated directories only."""
import json

import pytest

from demo_journal import initialize, locked
from kraken_execution import ExecutionError

ACCOUNT = '11111111-1111-4111-8111-111111111111'
KEY = 'a' * 64


def capture(second=0, rows=()):
    return {'account_uid': ACCOUNT, 'api_key_fingerprint': KEY,
            'reference_utc': f'2026-09-21T00:00:{second:02d}Z', 'open_orders': list(rows)}


def action(capture_hash):
    return {'operation_id': 'entry-1', 'endpoint': 'sendorder', 'role': 'ENTRY',
        'capture_sha256': capture_hash, 'plan_sha256': 'b'*64, 'trusted_head': 'c'*40, 'params': {'cliOrdId': 'entry-1', 'symbol': 'PF_DOTUSD',
            'side': 'buy', 'size': '10', 'orderType': 'lmt', 'limitPrice': '1', 'reduceOnly': False}}


def row(**changes):
    result = {'cliOrdId': 'entry-1', 'order_id': 'exchange-1', 'symbol': 'PF_DOTUSD',
        'side': 'buy', 'filledSize': '2', 'unfilledSize': '8', 'orderType': 'lmt',
        'limitPrice': '1', 'reduceOnly': False}
    result.update(changes)
    return result


def start(root):
    initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        ref = store.capture(capture())
        store.prepare(action(ref))
        store.before_dispatch('entry-1')


def test_unknown_send_survives_restart_and_never_dispatches_twice(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state['operations']['entry-1']['status'] == 'unknown'
        with pytest.raises(ExecutionError, match='already dispatched'):
            store.before_dispatch('entry-1')
        newer = store.capture(capture(1))
        other = action(newer); other['operation_id'] = other['params']['cliOrdId'] = 'entry-2'
        with pytest.raises(ExecutionError, match='unresolved'):
            store.prepare(other)


def test_new_exact_order_presence_resolves_timeout_but_empty_readback_does_not(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        empty = store.capture(capture(1))
        with pytest.raises(ExecutionError, match='unique order presence'):
            store.resolve_present_send('entry-1', empty, 'exchange-1')
        observed = store.capture(capture(2, [row()]))
        store.resolve_present_send('entry-1', observed, 'exchange-1')
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected
        assert store.state['ownership']['entry-1']['status'] == 'open'
        assert store.state['operations']['entry-1']['status'] == 'resolved'


def test_response_acknowledgement_alone_never_resolves_execution(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        store.response('entry-1', {'result': 'success', 'sendStatus': {'status': 'placed'}})
        assert store.state['operations']['entry-1']['status'] == 'acknowledged'
        assert store.state['ownership']['entry-1']['status'] == 'pending'
    with locked(root, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='unresolved'):
            store.prepare({**action(store.state['latest_capture']), 'operation_id': 'new'})


def test_account_key_substitution_and_duplicate_initialization_are_refused(tmp_path):
    root = tmp_path / 'journal'; initialize(root, ACCOUNT, KEY)
    with pytest.raises(FileExistsError): initialize(root, ACCOUNT, KEY)
    with pytest.raises(ExecutionError, match='identity mismatch'):
        with locked(root, ACCOUNT, 'b'*64): pass
    # The failed open releases the lock without masking the original exception.
    with locked(root, ACCOUNT, KEY) as store:
        wrong = capture(); wrong['account_uid'] = '22222222-2222-4222-8222-222222222222'
        with pytest.raises(ExecutionError, match='account/key'):
            store.capture(wrong)


def test_concurrent_runner_and_use_after_unlock_are_refused(tmp_path):
    root = tmp_path / 'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='Another demo runner'):
            with locked(root, ACCOUNT, KEY): pass
    with pytest.raises(ExecutionError, match='no longer held'):
        store.capture(capture())


def test_event_tampering_missing_tail_and_partial_commit_fail_closed(tmp_path):
    root = tmp_path / 'journal'; start(root)
    last = root / 'events/00000003.json'
    content = last.read_bytes(); last.unlink()
    with pytest.raises(ExecutionError, match='head differs'):
        with locked(root, ACCOUNT, KEY): pass
    last.write_bytes(content)
    event = json.loads(content); event['payload']['operation_id'] = 'substitution'
    last.write_text(json.dumps(event))
    with pytest.raises(ExecutionError, match='chain changed'):
        with locked(root, ACCOUNT, KEY): pass
    last.write_bytes(content)
    (root / '.head.next').write_text('{}')
    with pytest.raises(ExecutionError, match='head differs'):
        with locked(root, ACCOUNT, KEY): pass


@pytest.mark.parametrize('changes', [{'limitPrice': '2'}, {'side': 'sell'}, {'filledSize': '9'}, {'reduceOnly': True}])
def test_resolution_rejects_different_order_economics(tmp_path, changes):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        observed = store.capture(capture(1, [row(**changes)]))
        with pytest.raises(ExecutionError):
            store.resolve_present_send('entry-1', observed, 'exchange-1')


def test_resolution_requires_later_capture_and_replay_checks_artifact_bytes(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        simultaneous = store.capture(capture(0, [row()]))
        with pytest.raises(ExecutionError, match='later observation'):
            store.resolve_present_send('entry-1', simultaneous, 'exchange-1')
        observed = store.capture(capture(1, [row()]))
        store.resolve_present_send('entry-1', observed, 'exchange-1')
    (root / 'artifacts' / (observed + '.json')).write_text('{}')
    with pytest.raises(ExecutionError, match='artifact hash mismatch'):
        with locked(root, ACCOUNT, KEY): pass


def test_external_state_copy_cannot_mutate_the_locked_journal(tmp_path):
    root = tmp_path / 'journal'; start(root)
    with locked(root, ACCOUNT, KEY) as store:
        copy = store.state
        copy['operations']['entry-1']['status'] = 'resolved'
        assert store.state['operations']['entry-1']['status'] == 'unknown'
