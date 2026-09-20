"""Actual-fill bracket requirements, without exchange calls or synthetic fills."""
from copy import deepcopy

import pytest

from exchange_brackets import requirements
from kraken_execution import ExecutionError
from test_ledger import prepare, config, order, EPOCH

NOW = '2026-09-20T20:05:00Z'


def setup(side='LONG', **cfg_changes):
    cfg, plan, _ = prepare(cfg=config(**cfg_changes), entry=order(side=side, stop_after_t1_usd='1'))
    ident = plan['orders'][0]['order']['client_id']
    owners = {ident: {'role': 'ENTRY', 'exchange_order_id': 'entry-exchange'}}
    for role in ('STOP', 'T1', 'T2', 'T3', 'CLOSE'):
        owners[role.lower()] = {'role': role, 'exchange_order_id': role + '-exchange'}
    return cfg, plan, owners


def fill(plan, role, qty, ident=None):
    long = plan['orders'][0]['order']['side'] == 'LONG'
    return {'fill_id': ident or 'fill-' + role, 'order_id': 'entry-exchange' if role == 'ENTRY' else role + '-exchange',
            'cliOrdId': plan['orders'][0]['order']['client_id'] if role == 'ENTRY' else role.lower(),
            'symbol': 'PF_DOTUSD', 'side': ('buy' if long else 'sell') if role == 'ENTRY' else ('sell' if long else 'buy'),
            'size': str(qty), 'price': '1', 'fillTime': '2026-09-20T20:01:00Z', 'fillType': 'maker'}


def opened(plan, role, **changes):
    long = plan['orders'][0]['order']['side'] == 'LONG'
    item = {'symbol': 'PF_DOTUSD', 'cliOrdId': plan['orders'][0]['order']['client_id'] if role == 'ENTRY' else role.lower(),
            'order_id': 'entry-exchange' if role == 'ENTRY' else role + '-exchange',
            'side': ('buy' if long else 'sell') if role == 'ENTRY' else ('sell' if long else 'buy'),
            'reduceOnly': role != 'ENTRY', 'stopPrice': '.99' if long else '1.01'}
    item.update(changes)
    return item


def run(cfg, plan, owners, fills, position, opens=(), status='cancelled', **kwargs):
    return requirements(plan, cfg, fills, owners, list(opens), str(position), kwargs.pop('reference', NOW),
                        EPOCH, status, coverage_complete=True, **kwargs)


def by_role(report): return {o['role']: o['params'] for o in report['desired_orders']}


@pytest.mark.parametrize('side,position,exit_side', [('LONG', 10, 'sell'), ('SHORT', -10, 'buy')])
def test_partial_entry_brackets_only_actual_quantity(side, position, exit_side):
    cfg, plan, owners = setup(side)
    report = run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], position, [opened(plan, 'ENTRY')], status='open')
    desired = by_role(report)
    assert {role: row['size'] for role, row in desired.items()} == {'STOP': '10', 'T1': '4', 'T2': '3', 'T3': '3'}
    assert all(row['side'] == exit_side and row['reduceOnly'] for row in desired.values())
    assert desired['STOP']['triggerSignal'] == 'mark' and 'limitPrice' not in desired['STOP']
    assert not report['cancel_client_ids'] and not report['authorizes_execution']
    assert report == run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], position, [opened(plan, 'ENTRY')], status='open')


def test_small_partial_fill_never_creates_zero_size_targets():
    cfg, plan, owners = setup()
    desired = by_role(run(cfg, plan, owners, [fill(plan, 'ENTRY', 1)], 1))
    assert set(desired) == {'STOP', 'T3'}
    assert desired['T3']['size'] == '1'


def test_partial_target_subtracts_only_filled_quantity_and_cancels_entry_remainder():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10), fill(plan, 'T1', 2)]
    report = run(cfg, plan, owners, rows, 8, [opened(plan, 'ENTRY'), opened(plan, 'T1')], status='open')
    desired = by_role(report)
    assert desired['STOP']['size'] == '8' and desired['T1']['size'] == '2'
    assert desired['STOP']['stopPrice'] == '0.99'
    assert report['cancel_client_ids'] == [plan['orders'][0]['order']['client_id']]


def test_full_t1_trails_only_when_entry_is_terminal_and_cleans_filled_target():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10), fill(plan, 'T1', 4)]
    pending = run(cfg, plan, owners, rows, 6, [opened(plan, 'ENTRY')], status='open')
    assert by_role(pending)['STOP']['stopPrice'] == '0.99'
    terminal = run(cfg, plan, owners, rows, 6, [opened(plan, 'T1')])
    assert by_role(terminal)['STOP']['stopPrice'] == '1'
    assert terminal['cancel_client_ids'] == ['t1']
    assert 'T1' not in by_role(terminal)


def test_partial_stop_requires_finishing_close_and_preserves_protection():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10), fill(plan, 'STOP', 3)]
    report = run(cfg, plan, owners, rows, 7, [opened(plan, role) for role in ('STOP', 'T1', 'T2', 'T3')])
    assert set(by_role(report)) == {'STOP', 'CLOSE'}
    assert by_role(report)['CLOSE']['size'] == '7'
    assert report['close_reason'] == 'finish_partial_close'
    assert report['cancel_client_ids'] == ['t1', 't2', 't3']


def test_flat_position_removes_exit_siblings_and_resting_entry():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10), fill(plan, 'STOP', 10)]
    report = run(cfg, plan, owners, rows, 0, [opened(plan, role) for role in ('ENTRY', 'T1', 'T2', 'T3')], status='open')
    assert report['desired_orders'] == []
    assert set(report['cancel_client_ids']) == {plan['orders'][0]['order']['client_id'], 't1', 't2', 't3'}


def test_order_expiry_cancels_unfilled_remainder_without_closing_position():
    cfg, plan, owners = setup(max_order_age_minutes=2)
    report = run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], 10, [opened(plan, 'ENTRY')], status='open')
    assert report['entry_cancel_required']
    assert report['close_reason'] is None and 'CLOSE' not in by_role(report)
    assert report['entry_deadline_utc'] == '2026-09-20T20:02:00Z'


def test_max_hold_starts_at_first_actual_entry_and_triggers_market_close():
    cfg, plan, owners = setup(max_hold_hours=1)
    rows = [fill(plan, 'ENTRY', 10)]
    before = run(cfg, plan, owners, rows, 10, reference='2026-09-20T21:00:59Z')
    assert 'CLOSE' not in by_role(before)
    after = run(cfg, plan, owners, rows, 10, reference='2026-09-20T21:01:00Z')
    assert after['close_reason'] == 'max_hold' and by_role(after)['CLOSE']['orderType'] == 'mkt'
    again = run(cfg, plan, owners, rows, 10, reference='2026-09-20T21:02:00Z', prior_terms=after['effective_terms'])
    assert again['close_reason'] == 'max_hold'


def test_management_close_and_tighter_stop_are_respected():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10)]
    report = run(cfg, plan, owners, rows, 10, [opened(plan, 'STOP', stopPrice='.998')],
                 management={'action': 'MODIFY', 'stop_usd': '.995'})
    assert by_role(report)['STOP']['stopPrice'] == '0.998'
    report = run(cfg, plan, owners, rows, 10, management={'action': 'CLOSE', 'type': 'MARKET'})
    assert report['close_reason'] == 'management_close'
    with pytest.raises(ExecutionError, match='increases original risk'):
        run(cfg, plan, owners, rows, 10, management={'action': 'MODIFY', 'stop_usd': '.98'})


def test_unknown_attempt_incomplete_history_and_unowned_orders_block_planning():
    cfg, plan, owners = setup()
    with pytest.raises(ExecutionError, match='unknown entry'):
        run(cfg, plan, owners, [], 0, status='unknown')
    with pytest.raises(ExecutionError, match='Complete'):
        requirements(plan, cfg, [], owners, [], '0', NOW, EPOCH, 'cancelled')
    with pytest.raises(ExecutionError, match='Unowned'):
        run(cfg, plan, owners, [], 0, [opened(plan, 'T1', cliOrdId='external')])


def test_position_mismatch_exit_overfill_and_nonreduceonly_fail():
    cfg, plan, owners = setup()
    with pytest.raises(ExecutionError, match='position differs'):
        run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], 11)
    with pytest.raises(ExecutionError, match='exceed authorized'):
        run(cfg, plan, owners, [fill(plan, 'ENTRY', 10), fill(plan, 'STOP', 11)], -1)
    with pytest.raises(ExecutionError, match='not reduce-only'):
        run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], 10, [opened(plan, 'STOP', reduceOnly=False)])


def test_filled_status_must_equal_authorized_quantity_and_target_changes_stop_after_exit():
    cfg, plan, owners = setup()
    with pytest.raises(ExecutionError, match='Filled entry status'):
        run(cfg, plan, owners, [fill(plan, 'ENTRY', 10)], 10, status='filled')
    with pytest.raises(ExecutionError, match='after partial exit'):
        run(cfg, plan, owners, [fill(plan, 'ENTRY', 10), fill(plan, 'T1', 1)], 9,
            management={'action': 'MODIFY', 'targets': deepcopy(plan['orders'][0]['order']['targets'])})



def test_modified_terms_and_close_latch_survive_followup_hold():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10)]
    targets = deepcopy(plan['orders'][0]['order']['targets'])
    targets[0]['price_usd'] = '1.03'
    modified = run(cfg, plan, owners, rows, 10,
        management={'action': 'MODIFY', 'stop_usd': '.995', 'targets': targets})
    held = run(cfg, plan, owners, rows, 10, prior_terms=modified['effective_terms'])
    assert by_role(held)['T1']['limitPrice'] == '1.03'
    assert by_role(held)['STOP']['stopPrice'] == '0.995'
    closing = run(cfg, plan, owners, rows, 10, prior_terms=held['effective_terms'],
                  management={'action': 'CLOSE', 'type': 'MARKET'})
    held_again = run(cfg, plan, owners, rows, 10, [opened(plan, 'CLOSE')], prior_terms=closing['effective_terms'])
    assert 'CLOSE' in by_role(held_again)
    assert 'close' not in held_again['cancel_client_ids']
    with pytest.raises(ExecutionError, match='increases original risk'):
        run(cfg, plan, owners, rows, 10, prior_terms=held['effective_terms'],
            management={'action': 'MODIFY', 'stop_usd': '.993'})


def test_partial_close_is_recomputed_from_fills_not_original_size():
    cfg, plan, owners = setup()
    rows = [fill(plan, 'ENTRY', 10), fill(plan, 'CLOSE', 4)]
    report = run(cfg, plan, owners, rows, 6)
    assert by_role(report)['CLOSE']['size'] == '6'
    assert by_role(report)['STOP']['size'] == '6'
    closed = run(cfg, plan, owners, rows + [fill(plan, 'CLOSE', 6, 'second-close-fill')], 0,
                 [opened(plan, 'STOP')], prior_terms=report['effective_terms'])
    assert closed['desired_orders'] == [] and closed['cancel_client_ids'] == ['stop']
