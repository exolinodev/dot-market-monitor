"""Acquired demo equity breaches survive recovery/restart and cancel exposure."""
from copy import deepcopy

import pytest

from demo_journal import locked
from demo_risk import account_risk
from kraken_execution import ExecutionError
from order_executor import load_published
from test_demo_preflight import setup, observe, run
from test_demo_protection import started, acquire, preview
from test_demo_evidence import KEY
from test_exchange_history import ACCOUNT


def config(args):
    repo, head, forecast, data, _ = args
    return load_published(data, repo, head, forecast)['config']


def test_breach_stays_latched_after_recovery_and_process_restart(tmp_path):
    args = setup(tmp_path, capital='3900')
    with locked(args[-1], ACCOUNT, KEY) as store:
        first = account_risk(store, config(args))
        assert first['kill_switch'] and first['equity_floor_usd'] == '4000'
        observe(store, tmp_path/'recovered', '2026-09-20T20:05:10Z', '5000')
        store.reconcile_position()
        tip = store.tip
        result = account_risk(store, config(args))
        assert store.tip == tip
        assert result['kill_switch'] and result['first_breach'] == first['first_breach']
        assert result['current_usable_capital_usd'] == '5000'
        assert result['minimum_observed_capital_usd'] == '3900'
        assert result['observation_count'] == 3
    with locked(args[-1], ACCOUNT, KEY) as store:
        assert account_risk(store, config(args)) == result
    check = run(args, '2026-09-20T20:05:10Z')
    assert 'demo_equity_floor_breached' in check['reasons']
    assert not check['entry_checks_passed']


@pytest.mark.parametrize('capital,tripped', [('4000',False), ('3999.99',True), ('0',True), ('-10',True)])
def test_exact_floor_and_distressed_wallets(tmp_path, capital, tripped):
    args = setup(tmp_path, capital=capital)
    with locked(args[-1], ACCOUNT, KEY) as store:
        result = account_risk(store, config(args))
        assert result['kill_switch'] is tripped


def test_kill_switch_cancels_entry_remainder_without_removing_position_protection(tmp_path):
    args, plan = started(tmp_path)
    with locked(args[-1], ACCOUNT, KEY) as store:
        acquire(store, tmp_path/'distressed', plan, at='2026-09-20T20:05:15Z',
                capital='-10', available='0')
        store.reconcile_position()
        result = preview(args, store, '2026-09-20T20:05:15Z')
        assert result['account_risk']['kill_switch']
        assert result['next_action']['endpoint'] == 'cancelorder'
        assert result['next_action']['role'] == 'ENTRY'
        assert result['requirements']['remaining_quantity'] == '10'
        assert result['requirements']['close_reason'] is None
        # The requirement list still carries the full position's reduce-only stop.
        assert any(x['role'] == 'STOP' and x['params']['size'] == '10'
                   and x['params']['reduceOnly'] for x in result['requirements']['desired_orders'])


def test_risk_requires_live_lock_and_valid_bound_floor(tmp_path):
    args = setup(tmp_path)
    cfg = config(args)
    with locked(args[-1], ACCOUNT, KEY) as store:
        bad = deepcopy(cfg); bad['equity_floor_fraction'] = '1.1'
        with pytest.raises(ExecutionError, match='floor at most one'):
            account_risk(store, bad)
    with pytest.raises(ExecutionError, match='lock'):
        account_risk(store, cfg)
