"""One real orchestration path over synthetic demo HTTP; never network."""
import json
from datetime import datetime

import pytest

from demo_dispatch import dispatch_once
from demo_journal import locked
from kraken_execution import ExecutionError, OutcomeUnknown
from order_executor import materialize
from test_demo_preflight import Funded, setup
from test_demo_protection import started
from test_demo_evidence import KEY
from test_exchange_history import ACCOUNT
from test_order_executor import git


def enable(args, tmp_path):
    repo, _, forecast, _, journal = args
    p = repo/'config/demo_executor.json'
    settings = json.loads(p.read_text()); settings['enabled'] = True
    p.write_text(json.dumps(settings)); git(repo, 'add', 'config')
    git(repo, 'commit', '-m', 'enable isolated synthetic demo', date='2026-09-20T20:05:11Z')
    head = git(repo, 'rev-parse', 'HEAD'); git(repo, 'update-ref', 'refs/remotes/origin/main', head)
    return repo, head, forecast, materialize(repo, head, tmp_path/'enabled'), journal


class Exchange(Funded):
    key_fingerprint = KEY
    def __init__(self, store, *, timeout=False, changed=False):
        super().__init__('2026-09-20T20:05:15Z')
        self.calls, self.timeout, self.changed = [], timeout, changed
        capture = store._read_artifact(store.state['latest_capture'])
        self.orders, self.positions = capture['open_orders'], capture['positions']
        # Preserve the acquired entry's execution in the synthetic API history.
        from test_demo_position import execution
        from exchange_history import milliseconds
        ident = next(iter(store.state['ownership']))
        item = execution(ident, '10')
        item['timestamp'] = milliseconds('2026-09-20T20:05:05Z')
        item['event']['execution']['execution']['timestamp'] = item['timestamp']
        item['event']['execution']['execution']['order']['uid'] = 'entry-exchange'
        self.events = [item]
        self.store = store
        self.open_reads = 0

    def request(self, endpoint, params=None):
        self.calls.append(endpoint)
        if endpoint == 'openorders':
            self.open_reads += 1
            if self.changed and self.open_reads == 2:
                self.orders = []
        if endpoint in ('sendorder', 'cancelorder', 'editorder'):
            # The barrier and exact intent must be durable before this call.
            operations = self.store.state['operations']
            pending = [x for x in operations.values() if x['status'] == 'unknown']
            assert len(pending) == 1 and pending[0]['action']['params'] == params
            if self.timeout: raise OutcomeUnknown('synthetic lost reply')
            if endpoint == 'cancelorder':
                self.orders = [o for o in self.orders if o['cliOrdId'] != params['cliOrdId']]
                self.at = '2026-09-20T20:05:17Z'
                return 200, json.dumps({'result':'success','cancelStatus':{'status':'cancelled'}}).encode()
            assert endpoint == 'sendorder' and params['reduceOnly'] is True
            self.orders = [*self.orders, {**params, 'orderType':'stop', 'order_id':'new-stop',
                                          'filledSize':'0', 'unfilledSize':params['size']}]
            self.at = '2026-09-20T20:05:17Z'
            return 200, json.dumps({'result':'success','sendStatus':{'status':'placed','order_id':'new-stop'}}).encode()
        return super().request(endpoint, params)


def run(args, store, client, clock=lambda: datetime.fromisoformat('2026-09-20T20:05:16+00:00')):
    repo, head, forecast, data, _ = args
    return dispatch_once(data, repo, head, forecast, store, client, protection=True, clock=clock)


def test_durable_stop_attempt_then_raw_readback_and_no_restart_resend(tmp_path):
    args, _ = started(tmp_path); args = enable(args, tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store)
        result = run(args, store, client)
        assert result['mutation_attempted'] and result['operation_status'] == 'acknowledged'
        assert result['post_capture_sha256'] and not result['fill_verified']
        assert client.calls.count('sendorder') == 1
        operation = result['operation_id']
        assert (store.root/'attempts'/operation/'response.raw').exists()
        assert store.state['latest_reconciliation'] is None
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store)
        with pytest.raises(ExecutionError, match='unresolved'):
            run(args, store, client)
        assert client.calls == []
        store.resolve_present_send(operation, result['post_capture_sha256'], 'new-stop')
        store.reconcile_position()
        assert store.state['ownership'][operation]['status'] == 'open'


@pytest.mark.parametrize('failure', ['timeout','changed','stale'])
def test_failure_consumes_intent_without_retry(tmp_path, failure):
    args, _ = started(tmp_path); args = enable(args, tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store, timeout=failure=='timeout', changed=failure=='changed')
        stamps = iter(['2026-09-20T20:05:16+00:00',
                       '2026-09-20T20:06:00+00:00' if failure=='stale' else '2026-09-20T20:05:16+00:00'])
        result = run(args, store, client, lambda: datetime.fromisoformat(next(stamps)))
        assert result['operation_status'] == ('unknown' if failure=='timeout' else 'prepared')
        assert client.calls.count('sendorder') == (1 if failure=='timeout' else 0)
        before = list(client.calls)
        with pytest.raises(ExecutionError, match='unresolved'):
            run(args, store, client)
        assert client.calls == before


def test_disabled_policy_and_wrong_key_do_not_acquire_or_send(tmp_path):
    args, _ = started(tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store)
        with pytest.raises(ExecutionError, match='disabled'): run(args, store, client)
        assert client.calls == []
    args = enable(args, tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store); client.key_fingerprint = 'b'*64
        with pytest.raises(ExecutionError, match='key differs'): run(args, store, client)
        assert client.calls == []


def test_unproven_margin_never_dispatches_an_entry(tmp_path):
    args = enable(setup(tmp_path), tmp_path)
    repo, head, forecast, data, root = args
    with locked(root, ACCOUNT, KEY) as store:
        client = Funded('2026-09-20T20:05:15Z'); client.key_fingerprint = KEY
        with pytest.raises(ExecutionError, match='margin proof'):
            dispatch_once(data, repo, head, forecast, store, client,
                          clock=lambda: datetime.fromisoformat('2026-09-20T20:05:16+00:00'))
        assert not store.state['operations']


def test_kill_switch_cancellation_needs_positive_history_even_after_ack(tmp_path):
    args, _ = started(tmp_path); args = enable(args, tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store); client.capital = '3900'
        result = run(args, store, client)
        assert client.calls.count('cancelorder') == 1
        assert result['operation_status'] == 'acknowledged' and result['recovery_required']
        assert result['post_capture_sha256']
        # Absence after a cancel acknowledgement is not terminal history proof.
        entry = next(v for v in store.state['ownership'].values() if v['role']=='ENTRY')
        assert entry['status'] == 'open'


def test_failed_post_readback_does_not_erase_or_repeat_acknowledged_send(tmp_path):
    args, _ = started(tmp_path); args = enable(args, tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        client = Exchange(store)
        original = client.market
        def market(endpoint):
            if 'sendorder' in client.calls: raise ExecutionError('synthetic unavailable readback')
            return original(endpoint)
        client.market = market
        result = run(args, store, client)
        assert result['operation_status'] == 'acknowledged'
        assert result['post_capture_sha256'] is None
        assert client.calls.count('sendorder') == 1
        with pytest.raises(ExecutionError, match='unresolved'):
            run(args, store, client)
        assert client.calls.count('sendorder') == 1


def test_cli_dispatch_failure_redacts_credentials(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    import kraken_execution
    args = setup(tmp_path)
    repo, head, forecast, _, journal = args
    path = Path(__file__).resolve().parents[1]/'scripts/execute_orders.py'
    spec = importlib.util.spec_from_file_location('dispatch_cli_test', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    monkeypatch.setenv('KRAKEN_DEMO_API_KEY', 'synthetic-key')
    monkeypatch.setenv('KRAKEN_DEMO_API_SECRET', 'must-not-leak')
    def failure(*args): raise RuntimeError('must-not-leak')
    monkeypatch.setattr(kraken_execution, 'DemoClient', failure)
    assert cli.main(['--mode','demo','--dispatch-once','--repo',str(repo),'--trusted-head',head,
                     '--forecast-id',forecast,'--journal-dir',str(journal),
                     '--account-uid',ACCOUNT,'--key-fingerprint',KEY]) == 1
    output = capsys.readouterr()
    assert 'must-not-leak' not in output.out + output.err
    assert 'No automatic retry' in output.err
    with locked(journal, ACCOUNT, KEY) as store: assert not store.state['operations']
