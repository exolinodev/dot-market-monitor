"""Recovery command uses raw acquired evidence, never an absent-order guess."""
import importlib.util
import json
from pathlib import Path

import pytest

from demo_journal import locked
from exchange_history import milliseconds
from test_demo_journal import action, row, start, capture, KEY as SIMPLE_KEY
from test_demo_position import Client, observe, initialize_baseline, execution, position, KEY
from test_exchange_history import ACCOUNT, END, DATE, body
from test_order_history_recovery import native_event
from test_trigger_history import event as trigger_event


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1]/'scripts/recover_demo_journal.py'
    spec = importlib.util.spec_from_file_location('evidence_recovery_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(root, key=KEY):
    return ['--journal-dir', str(root), '--account-uid', ACCOUNT, '--key-fingerprint', key]


class HistoryClient(Client):
    def __init__(self, endpoint, event):
        super().__init__(END)
        self.endpoint, self.event = endpoint, event

    def history(self, endpoint, params):
        if endpoint == self.endpoint:
            return 200, json.dumps(body([self.event])).encode(), {'Date': DATE}
        return super().history(endpoint, params)


@pytest.mark.parametrize('kind', ['present', 'filled', 'cancelled', 'rejected', 'trigger-cancelled'])
def test_recovery_from_raw_bundle_replays_and_never_permits_redispatch(tmp_path, cli, capsys, kind):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        entry = action(store.state['latest_capture'])
        if kind == 'trigger-cancelled':
            entry['params'].update(orderType='stp', stopPrice='1.01', triggerSignal='mark')
            del entry['params']['limitPrice']
        store.prepare(entry); store.before_dispatch('entry-1')
        extra = []
        exchange_id = 'exchange-1'
        if kind == 'present':
            client = Client(END, [row()], [position()], [execution()])
        elif kind == 'filled':
            client = Client(END, [], [position('10')], [execution(size='10')])
        else:
            event = (trigger_event() if kind == 'trigger-cancelled' else
                     native_event('OrderCancelled' if kind == 'cancelled' else 'OrderRejected'))
            event['timestamp'] = milliseconds('2026-09-21T00:30:00Z')
            client = HistoryClient('triggers' if kind == 'trigger-cancelled' else 'orders', event)
            extra = ['--event-id', event['uid']]
            if kind == 'trigger-cancelled': exchange_id = 'trigger-1'
        ref = observe(store, tmp_path/'later', client)
    command = [*args(root), '--resolve-' + kind, 'entry-1', '--capture-sha256', ref,
               '--exchange-order-id', exchange_id, *extra]
    assert cli.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['unresolved_operations'] == [] and not result['authorizes_execution']
    with locked(root, ACCOUNT, KEY) as store:
        assert store.tip == result['journal_head_sha256']
        assert store.state['operations']['entry-1']['status'] == 'resolved'
        assert store.state['ownership']['entry-1']['status'] == ('open' if kind == 'present' else 'terminal')
        before = store.tip
    # Repeating a successful recovery must not duplicate a journal event.
    assert cli.main(command) == 1
    capsys.readouterr()
    with locked(root, ACCOUNT, KEY) as store: assert store.tip == before


@pytest.mark.parametrize('failure', ['empty', 'wrong_identity', 'stale_capture'])
def test_unproven_presence_leaves_journal_unchanged(tmp_path, cli, capsys, failure):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        prior = store.state['latest_capture']
        store.prepare(action(prior)); store.before_dispatch('entry-1')
        ref = observe(store, tmp_path/'later', Client(END, [] if failure == 'empty' else [row()]))
        before = store.tip
    assert cli.main([*args(root), '--resolve-present', 'entry-1', '--capture-sha256',
                     prior if failure == 'stale_capture' else ref, '--exchange-order-id',
                     'wrong' if failure == 'wrong_identity' else 'exchange-1']) == 1
    assert 'Recovery refused' in capsys.readouterr().err
    with locked(root, ACCOUNT, KEY) as store: assert store.tip == before


def test_normalized_capture_without_raw_bundle_cannot_drive_cli(tmp_path, cli, capsys):
    root = tmp_path/'journal'; start(root)
    with locked(root, ACCOUNT, SIMPLE_KEY) as store:
        ref = store.capture(capture(1, [row()])); before = store.tip
    assert cli.main([*args(root, SIMPLE_KEY), '--resolve-present', 'entry-1',
                     '--capture-sha256', ref, '--exchange-order-id', 'exchange-1']) == 1
    capsys.readouterr()
    with locked(root, ACCOUNT, SIMPLE_KEY) as store: assert store.tip == before


def test_partial_fill_does_not_resolve_unknown_send(tmp_path, cli, capsys):
    root = initialize_baseline(tmp_path)
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare(action(store.state['latest_capture'])); store.before_dispatch('entry-1')
        ref = observe(store, tmp_path/'partial', Client(END, [], [position('9')], [execution(size='9')]))
        before = store.tip
    assert cli.main([*args(root), '--resolve-filled', 'entry-1', '--capture-sha256', ref,
                     '--exchange-order-id', 'exchange-1']) == 1
    capsys.readouterr()
    with locked(root, ACCOUNT, KEY) as store:
        assert store.tip == before
        assert store.state['operations']['entry-1']['status'] == 'unknown'


@pytest.mark.parametrize('options', [
    ['--resolve-present', 'entry-1'],
    ['--resolve-filled', 'entry-1', '--capture-sha256', 'a'*64, '--exchange-order-id', 'x', '--event-id', 'e'],
    ['--resolve-cancelled', 'entry-1', '--capture-sha256', 'a'*64, '--exchange-order-id', 'x'],
    ['--event-id', 'e'], ['--abandon-prepared', 'x', '--resolve-present', 'y'],
])
def test_ambiguous_or_incomplete_commands_rejected_before_journal_access(tmp_path, cli, options):
    with pytest.raises(SystemExit) as error:
        cli.main([*args(tmp_path/'does-not-exist'), *options])
    assert error.value.code == 2
