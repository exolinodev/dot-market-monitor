"""Raw acquisition -> locked journal -> restart, without any exchange mutations."""
import json
import pytest
from demo_evidence import capture_bundle
from demo_journal import initialize, locked
from demo_observation import import_observation
from kraken_execution import ExecutionError
from test_demo_evidence import Client, KEY
from test_exchange_history import ACCOUNT, START, END, DATE, body, event


def setup(tmp_path, client=None, through=None):
    journal, bundle = tmp_path/'journal', tmp_path/'bundle'
    initialize(journal, ACCOUNT, KEY)
    result = capture_bundle(bundle, client or Client(), ACCOUNT, KEY, START, through)
    return journal, bundle, result['bundle_sha256']


def test_acquisition_import_restart_preserves_raw_and_exact_observation(tmp_path):
    journal, bundle, sha = setup(tmp_path)
    with locked(journal, ACCOUNT, KEY) as store:
        observed = import_observation(store, bundle, sha)
        value = store._read_artifact(observed)
        assert value['reference_utc'] == END
        assert value['execution_history_sha256']
        assert not value['quantity_reconciled'] and not value['authorizes_execution']
        assert (journal/'evidence'/sha/'readback/accounts.raw').read_bytes() == (bundle/'readback/accounts.raw').read_bytes()
        expected = store.state
    with locked(journal, ACCOUNT, KEY) as store: assert store.state == expected
    (journal/'evidence'/sha/'readback/accounts.raw').write_bytes(b'{}')
    with pytest.raises(ExecutionError, match='files changed'):
        with locked(journal, ACCOUNT, KEY): pass


def test_stale_history_end_cannot_resolve_newer_readback(tmp_path):
    journal, bundle, sha = setup(tmp_path, through='2026-09-21T00:59:00Z')
    with locked(journal, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='cover all readback'):
            import_observation(store, bundle, sha)
        assert store.state['latest_capture'] is None


def test_readback_race_rejected_even_if_final_quantity_might_match(tmp_path):
    class Racing(Client):
        def history(self, endpoint, params):
            if endpoint == 'account-log':
                return 200, json.dumps({'accountUid': ACCOUNT, 'logs': []}).encode(), {'Date': DATE}
            return 200, json.dumps(body([event(at=END)] if endpoint == 'executions' else [])).encode(), {'Date': DATE}
    journal, bundle, sha = setup(tmp_path, Racing())
    with locked(journal, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='overlaps readbacks'):
            import_observation(store, bundle, sha)
        assert store.state['latest_capture'] is None


def test_incomplete_history_does_not_enter_journal(tmp_path):
    journal, bundle = tmp_path/'journal', tmp_path/'bundle'; initialize(journal, ACCOUNT, KEY)
    result = capture_bundle(bundle, Client(continuation='next'), ACCOUNT, KEY, START, END, max_pages=1)
    with locked(journal, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='Incomplete'):
            import_observation(store, bundle, result['bundle_sha256'])


def test_external_evidence_symlink_rejected_on_restart(tmp_path):
    journal, bundle, sha = setup(tmp_path)
    with locked(journal, ACCOUNT, KEY) as store: import_observation(store, bundle, sha)
    (journal/'evidence').rename(tmp_path/'moved')
    (journal/'evidence').symlink_to(tmp_path/'moved')
    with pytest.raises(ExecutionError, match='symlink'):
        with locked(journal, ACCOUNT, KEY): pass


def test_nested_source_cannot_recursively_copy_into_itself(tmp_path):
    journal = tmp_path/'journal'; initialize(journal, ACCOUNT, KEY)
    bundle = journal/'evidence'
    result = capture_bundle(bundle, Client(), ACCOUNT, KEY, START)
    with locked(journal, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='must not overlap'):
            import_observation(store, bundle, result['bundle_sha256'])
        assert store.state['latest_capture'] is None
    assert not (bundle/result['bundle_sha256']).exists()


def test_account_log_activity_during_readbacks_prevents_import(tmp_path):
    from test_account_log import row
    class Deposit(Client):
        def history(self, endpoint, params):
            if endpoint == 'account-log':
                rows = [] if 'from' in params else [row(at=END, info='deposit', execution=None, contract=None)]
                return 200, json.dumps({'accountUid': ACCOUNT, 'logs': rows}).encode(), {'Date': DATE}
            return super().history(endpoint, params)
    journal, bundle, sha = setup(tmp_path, Deposit())
    with locked(journal, ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='overlaps readbacks'):
            import_observation(store, bundle, sha)
        assert store.state['latest_capture'] is None
