"""Published entry + acquired partial fills + journal -> one protective request."""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest

from demo_evidence import capture_bundle
from demo_journal import locked
from demo_observation import import_observation
from demo_protection import protection_preview, management_terms
from exchange_history import milliseconds
from kraken_execution import ExecutionError, entry_request
from ledger import digest
from order_executor import load_published
from test_demo_preflight import setup, Funded, START
from test_demo_evidence import KEY
from test_demo_position import execution, position
from test_exchange_history import ACCOUNT
from test_exchange_brackets import setup as bracket_setup, fill

AT = '2026-09-20T20:05:10Z'


def acquire(store, path, plan, *, at=AT, extra=(), quantity_delta=0):
    ident = plan['orders'][0]['order']['client_id']
    client = Funded(at)
    total = Decimal(plan['orders'][0]['size']['quantity']) + quantity_delta
    client.orders = [{'cliOrdId': ident, 'order_id': 'entry-exchange', 'symbol': 'PF_DOTUSD',
                      'side': 'buy', 'filledSize': '10', 'unfilledSize': str(total-10),
                      'orderType': 'lmt', 'limitPrice': '1', 'reduceOnly': False}, *extra]
    client.positions = [position('10')]
    event = execution(ident, '10')
    event['timestamp'] = milliseconds('2026-09-20T20:05:05Z')
    event['event']['execution']['execution']['timestamp'] = event['timestamp']
    event['event']['execution']['execution']['order']['uid'] = 'entry-exchange'
    client.events = [event]
    result = capture_bundle(path, client, ACCOUNT, KEY, START)
    return import_observation(store, path, result['bundle_sha256'])


def started(tmp_path, *, delta=0):
    args = setup(tmp_path)
    repo, head, forecast, data, root = args
    bound = load_published(data, repo, head, forecast)
    plan = bound['plan']; params = entry_request(plan, bound['config'])
    if delta: params['size'] = str(Decimal(params['size'])+delta)
    ident = params['cliOrdId']
    with locked(root, ACCOUNT, KEY) as store:
        store.prepare({'operation_id': ident, 'endpoint': 'sendorder', 'role': 'ENTRY',
                       'params': params, 'plan_sha256': digest(plan), 'trusted_head': head,
                       'capture_sha256': store.state['latest_capture']})
        store.before_dispatch(ident)
        ref = acquire(store, tmp_path/'partial', plan, quantity_delta=delta)
        store.resolve_present_send(ident, ref, 'entry-exchange')
        store.reconcile_position()
    return args, plan


def preview(args, store, at=AT):
    repo, head, forecast, data, _ = args
    return protection_preview(data, repo, head, forecast, store, at)


def test_partial_fill_creates_stop_then_next_target_after_durable_readback(tmp_path):
    args, plan = started(tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        tip = store.tip
        first = preview(args, store)
        assert store.tip == tip and first == preview(args, store)
        action = first['next_action']
        assert action['role'] == 'STOP' and action['params']['size'] == '10'
        assert action['params']['reduceOnly'] and not first['authorizes_execution']
        store.prepare(action); store.before_dispatch(action['operation_id'])
        stop = {**action['params'], 'order_id': 'stop-exchange', 'orderType': 'stop',
                'filledSize': '0', 'unfilledSize': '10'}
        ref = acquire(store, tmp_path/'protected', plan, at='2026-09-20T20:05:15Z', extra=[stop])
        store.resolve_present_send(action['operation_id'], ref, 'stop-exchange')
        store.reconcile_position()
        second = preview(args, store, '2026-09-20T20:05:15Z')
        assert second['next_action']['role'] == 'T1'
        assert second['next_action']['params']['size'] == '4'
        expected = store.state
    with locked(args[-1], ACCOUNT, KEY) as store:
        assert store.state == expected
        assert preview(args, store, '2026-09-20T20:05:15Z') == second


def test_expired_entry_remainder_is_cancelled_before_more_protective_sends(tmp_path):
    args, plan = started(tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        acquire(store, tmp_path/'expired', plan, at='2026-09-20T22:35:00Z')
        store.reconcile_position()
        result = preview(args, store, '2026-09-20T22:35:00Z')
        assert result['next_action']['endpoint'] == 'cancelorder'
        assert result['next_action']['role'] == 'ENTRY'
        assert result['requirements']['remaining_quantity'] == '10'


def test_cli_materializes_published_evidence_and_leaves_journal_unchanged(tmp_path, monkeypatch, capsys):
    args, _ = started(tmp_path)
    repo, head, forecast, _, root = args
    path = Path(__file__).resolve().parents[1]/'scripts/execute_orders.py'
    spec = importlib.util.spec_from_file_location('protective_cli', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    class Clock:
        @staticmethod
        def now(tz): return datetime.fromisoformat(AT.replace('Z', '+00:00'))
    monkeypatch.setattr(cli, 'datetime', Clock)
    with locked(root, ACCOUNT, KEY) as store: tip = store.tip
    assert cli.main(['--mode','demo','--preview','--protection','--repo',str(repo),
                     '--trusted-head',head,'--forecast-id',forecast,'--journal-dir',str(root),
                     '--account-uid',ACCOUNT,'--key-fingerprint',KEY]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['next_action']['role'] == 'STOP' and not report['authorizes_execution']
    with locked(root, ACCOUNT, KEY) as store: assert store.tip == tip


def test_stale_unreconciled_and_wrong_published_quantity_are_refused(tmp_path):
    args, plan = started(tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='stale'):
            preview(args, store, '2026-09-20T20:06:00Z')
        acquire(store, tmp_path/'newer', plan, at='2026-09-20T20:05:15Z')
        with pytest.raises(ExecutionError, match='durable position'):
            preview(args, store, '2026-09-20T20:05:15Z')
    other = tmp_path/'other'; other.mkdir()
    args, _ = started(other, delta=-1)
    with locked(args[-1], ACCOUNT, KEY) as store:
        with pytest.raises(ExecutionError, match='differs from published'):
            preview(args, store)


def instruction(plan, action, ident, at):
    return {'event_id': ident, 'at_utc': at,
            'plan': {'forecast_id': ident, 'config_sha256': plan['config_sha256'], 'management': [action]},
            'publication': {'commit': 'a'*40, 'at_utc': at}}


def test_published_management_is_chronological_and_close_cancel_stay_latched():
    cfg, plan, owners = bracket_setup()
    ident = plan['orders'][0]['order']['client_id']
    events = [instruction(plan, {'position_id':ident, 'action':'MODIFY', 'stop_usd':'0.995'}, 'a', '2026-09-20T20:02:00Z'),
              instruction(plan, {'position_id':ident, 'action':'CLOSE', 'type':'MARKET'}, 'b', '2026-09-20T20:03:00Z'),
              instruction(plan, {'client_id':ident, 'action':'CANCEL'}, 'c', '2026-09-20T20:03:00Z'),
              instruction(plan, {'position_id':ident, 'action':'HOLD'}, 'd', '2026-09-20T20:04:00Z'),
              instruction(plan, {'position_id':ident, 'action':'MODIFY', 'stop_usd':'0.998'}, 'future', '2026-09-20T21:00:00Z')]
    terms, cancel, used = management_terms(plan, cfg, events[::-1], [], owners, '2026-09-20T20:05:00Z')
    assert terms['stop_usd'] == '0.995' and terms['close_requested'] and cancel
    assert [x['forecast_id'] for x in used] == ['a','b','c','d']
    bad = instruction(plan, {'position_id':ident, 'action':'MODIFY', 'stop_usd':'0.99'}, 'e', '2026-09-20T20:04:30Z')
    with pytest.raises(ExecutionError, match='loosens'):
        management_terms(plan, cfg, [*events, bad], [], owners, '2026-09-20T20:05:00Z')


def test_target_change_is_checked_against_actual_exit_time():
    cfg, plan, owners = bracket_setup()
    ident = plan['orders'][0]['order']['client_id']
    change = instruction(plan, {'position_id':ident, 'action':'MODIFY',
                                'targets':deepcopy(plan['orders'][0]['order']['targets'])}, 'a', '2026-09-20T20:02:00Z')
    exited = fill(plan, 'T1', 1)
    with pytest.raises(ExecutionError, match='actual partial exit'):
        management_terms(plan, cfg, [change], [exited], owners, '2026-09-20T20:05:00Z')
    exited['fillTime'] = '2026-09-20T20:03:00Z'
    assert management_terms(plan, cfg, [change], [exited], owners, '2026-09-20T20:05:00Z')[2]
