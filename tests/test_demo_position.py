"""Acquired flat baseline and journal-derived reconciliation integration."""
import json
import pytest
from demo_evidence import capture_bundle
from demo_journal import initialize, locked
from demo_observation import import_observation
from kraken_execution import READS_WITH_PREFERENCES, ExecutionError
from test_demo_evidence import KEY, market_response
from test_demo_journal import action, row
from test_exchange_history import ACCOUNT, START, END, DATE, body, event

BASE = '2026-09-21T00:05:00Z'


class Client:
    def __init__(self, at=BASE, orders=(), positions=(), events=()):
        self.at, self.orders, self.positions, self.events = at, list(orders), list(positions), list(events)
    def market(self, endpoint):
        return market_response(endpoint, self.at)
    def request(self, endpoint, params=None):
        values = {'openorders': self.orders, 'openpositions': self.positions, 'fills': [], 'accounts': {}, 'leveragepreferences': []}
        return 200, json.dumps({'result': 'success', 'serverTime': self.at, READS_WITH_PREFERENCES[endpoint]: values[endpoint]}).encode()
    def history(self, endpoint, params):
        if endpoint == 'account-log':
            return 200, json.dumps({'accountUid': ACCOUNT, 'logs': []}).encode(), {'Date': DATE}
        return 200, json.dumps(body(self.events if endpoint == 'executions' else [])).encode(), {'Date': DATE}


def observe(store, path, client, since=START):
    result = capture_bundle(path, client, ACCOUNT, KEY, since)
    return import_observation(store, path, result['bundle_sha256'])


def initialize_baseline(tmp_path):
    root = tmp_path/'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'baseline', Client())
        store.establish_flat_baseline()
    return root


def execution(client_id='entry-1', size='2'):
    item = event()
    fill = item['event']['execution']['execution']
    fill['quantity'] = size
    fill['order'].update(uid='exchange-1', clientId=client_id)
    return item


def position(size='2', symbol='PF_DOTUSD'):
    return {'symbol': symbol, 'side': 'long', 'size': size}


def test_flat_baseline_report_replays_and_cannot_reset(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        ident, report = store.reconcile_position()
        assert report['quantity_reconciled'] and report['exchange_signed_quantity'] == '0'
        assert not report['authorizes_execution'] and not report['actual_costs_verified']
        assert store.state['latest_reconciliation'] == ident
        with pytest.raises(ExecutionError, match='single-use'):
            store.establish_flat_baseline()
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store: assert store.state == expected


@pytest.mark.parametrize('orders,positions', [([row()], []), ([], [position()]), ([], [position(symbol='PF_BTCUSD')])])
def test_baseline_rejects_any_existing_exposure(tmp_path, orders, positions):
    root = tmp_path/'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'bundle', Client(orders=orders, positions=positions))
        with pytest.raises(ExecutionError, match='no open orders or positions'):
            store.establish_flat_baseline()
        assert store.state.get('baseline_capture') is None


def test_partial_fill_reconciles_from_persisted_intent_and_acquired_evidence(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        capture = observe(store, tmp_path/'partial', Client(END, [row()], [position()], [execution()]))
        _, unknown = store.reconcile_position()
        assert 'unresolved_operation' in {i['kind'] for i in unknown['issues']}
        store.resolve_present_send('entry-1', capture, 'exchange-1')
        assert store.state['latest_reconciliation'] is None
        ident, report = store.reconcile_position()
        assert report['quantity_reconciled']
        assert report['expected_signed_quantity'] == report['exchange_signed_quantity'] == '2'
        assert report['orders']['entry-1']['filled_quantity'] == '2'
        expected = store.state
    with locked(root, ACCOUNT, KEY) as store:
        assert store.state == expected
        assert store._read_artifact(ident) == report


def test_unknown_fills_do_not_hide_behind_matching_positions(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'external', Client(END, positions=[position()], events=[execution('manual')]))
        _, report = store.reconcile_position()
        assert not report['quantity_reconciled']
        assert report['expected_signed_quantity'] == report['exchange_signed_quantity'] == '2'
        assert 'unowned_fill' in {i['kind'] for i in report['issues']}


def test_fill_window_must_reach_baseline_and_new_capture_invalidates_report(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.reconcile_position()
        observe(store, tmp_path/'short-window', Client(END), since='2026-09-21T00:10:00Z')
        assert store.state['latest_reconciliation'] is None
        with pytest.raises(ExecutionError, match='full baseline-to-observation'):
            store.reconcile_position()


def test_native_filled_quantity_must_match_history(tmp_path):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        capture = observe(store, tmp_path/'partial', Client(END, [row(filledSize='3', unfilledSize='7')], [position()], [execution()]))
        store.resolve_present_send('entry-1', capture, 'exchange-1')
        _, report = store.reconcile_position()
        assert 'open_order_fill_history_mismatch' in {i['kind'] for i in report['issues']}
        assert not report['quantity_reconciled']


def test_labels_without_raw_acquisition_cannot_establish_baseline(tmp_path):
    from test_demo_journal import capture
    root = tmp_path/'journal'; initialize(root, ACCOUNT, KEY)
    with locked(root, ACCOUNT, KEY) as store:
        value = capture(); value['api_key_fingerprint'] = KEY
        store.capture(value)
        with pytest.raises(ExecutionError, match='Raw acquired'):
            store.establish_flat_baseline()


def test_cli_explicit_setup_and_rerun_never_reset_journal(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/'scripts/capture_demo_evidence.py'
    spec = importlib.util.spec_from_file_location('demo_capture_cli_test', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    calls = []
    def client(key, secret):
        calls.append(True)
        return Client()
    monkeypatch.setattr(cli, 'DemoClient', client)
    monkeypatch.setenv('KRAKEN_DEMO_API_KEY', 'synthetic-key')
    monkeypatch.setenv('KRAKEN_DEMO_API_SECRET', 'synthetic-secret-not-printed')
    journal = tmp_path/'journal'
    args = ['--account-uid', ACCOUNT, '--since', START, '--journal-dir', str(journal),
            '--output-dir', str(tmp_path/'capture'), '--initialize-journal', '--establish-flat-baseline', '--reconcile']
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['quantity_reconciled'] and not result['authorizes_execution']
    with locked(journal, ACCOUNT, KEY) as store: original = store.state
    with pytest.raises(SystemExit): cli.main(args)
    assert len(calls) == 1
    assert 'synthetic-secret' not in capsys.readouterr().err
    with locked(journal, ACCOUNT, KEY) as store: assert store.state == original
