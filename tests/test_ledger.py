"""Economic and chronology invariants of the isolated deterministic paper engine."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, getcontext
import json
from pathlib import Path
import pytest

from cycles import utc, iso
from ledger import (decimal, digest, effective_at, initial_state, performance,
                    plan_instruction, replay, size_order, state_hash, step)

EPOCH = '2026-09-20T20:00:00Z'


def config(**changes):
    cfg = json.loads((Path(__file__).parents[1] / 'config/ledger.json').read_text())
    cfg.update(enabled=True, funding_convention_verified=True)
    cfg.update(changes)
    return cfg


def order(kind='LIMIT', side='LONG', forecast='forecast-a', **changes):
    item = {'client_id': forecast + '-1', 'action': 'ENTER', 'side': side,
            'entry': {'type': kind, 'price_usd': '1'},
            'stop_usd': '0.99' if side == 'LONG' else '1.01',
            'targets': [{'id': 'T1', 'price_usd': '1.025' if side == 'LONG' else '0.975', 'fraction': '0.4'},
                        {'id': 'T2', 'price_usd': '1.04' if side == 'LONG' else '0.96', 'fraction': '0.3'},
                        {'id': 'T3', 'price_usd': '1.06' if side == 'LONG' else '0.94', 'fraction': '0.3'}],
            'valid_until_utc': '2026-09-20T23:00:00Z', 'risk_tier': 'FULL'}
    item.update(changes)
    return item


def seed(cfg):
    events = [
        {'event_id': 'funding-20', 'type': 'funding_rate', 'at_utc': EPOCH, 'interval_start_utc': EPOCH,
         'absolute_rate': '0.00006', 'relative_rate': '0.00006'},
        {'event_id': 'spread-20', 'type': 'spread', 'at_utc': EPOCH, 'spread_bps': '2'}]
    return replay(cfg, EPOCH, events)[0], events


def prepare(cfg=None, entry=None):
    cfg = cfg or config()
    state, events = seed(cfg)
    entry = entry or order()
    quote = {'bid': '0.9998', 'ask': '1.0002'}
    plan = plan_instruction('forecast-a', EPOCH, [entry], [], state, cfg, quote)
    events.append({'type': 'instruction', 'event_id': 'plan-a', 'at_utc': plan['effective_at_utc'], 'plan': plan})
    return cfg, plan, events


def candle(minute, op='1', high='1.002', low='0.9998', close='1', mark=None):
    at = iso(utc(EPOCH) + timedelta(minutes=minute))
    trade = {'open': op, 'high': high, 'low': low, 'close': close}
    return {'type': 'candle', 'event_id': 'candle-' + at, 'at_utc': at,
            'trade': trade, 'mark': deepcopy(mark or trade)}


def closed(records):
    return [effect['trade'] for r in records for effect in r['effects'] if effect['type'] == 'TRADE_CLOSED']


def test_sizing_includes_costs_caps_notional_and_preserves_input():
    cfg, plan, _ = prepare()
    size = plan['orders'][0]['size']
    assert decimal(size['risk_budget_usd']) == 50
    assert decimal(size['estimated_stop_loss_including_costs_usd']) <= 50
    assert decimal(size['notional_usd']) <= 10000
    assert decimal(size['net_reward_risk_t1']) >= 1
    state, _ = seed(cfg)
    state['equity_usd'] = '10'; state['cash_usd'] = '10'; state['state_sha256'] = state_hash(state)
    quote = {'bid': '.9998', 'ask': '1.0002'}
    with pytest.raises(ValueError): size_order(order(), state, cfg, quote)
    bad = order(); bad['targets'][0]['fraction'] = '.5'
    with pytest.raises(ValueError, match='sum'): size_order(bad, seed(cfg)[0], cfg, quote)
    bad = order(); bad['stop_usd'] = '.99001'
    with pytest.raises(ValueError, match='tick'): size_order(bad, seed(cfg)[0], cfg, quote)


def test_limit_requires_through_trade_not_touch_and_never_mutates_input():
    cfg, _, inputs = prepare()
    inputs.append(candle(1, low='1'))
    before = deepcopy(inputs)
    state, _ = replay(cfg, EPOCH, inputs)
    assert state['position'] is None and state['order'] is not None
    assert inputs == before
    state, records = replay(cfg, EPOCH, inputs + [candle(2, low='.9999')])
    assert state['position']['entry_fill_usd'] == '1'
    assert state['position']['spread_cost_usd'] == '0'
    assert any(e['type'] == 'ENTRY_FILL' for e in records[-1]['effects'])


def test_market_and_short_fills_use_signed_half_spread_and_taker_fees():
    for side, expected in [('LONG', '1.0001'), ('SHORT', '.9999')]:
        cfg, _, inputs = prepare(entry=order(kind='MARKET', side=side))
        state, _ = replay(cfg, EPOCH, inputs + [candle(1)])
        pos = state['position']
        assert decimal(pos['entry_fill_usd']) == decimal(expected)
        assert decimal(pos['fees_usd']) == decimal(expected) * decimal(pos['quantity']) * Decimal('.0005')
        assert decimal(pos['spread_cost_usd']) > 0


def test_stop_wins_same_bar_and_gap_executes_at_worse_open():
    cfg, _, inputs = prepare()
    first = candle(1)
    state, records = replay(cfg, EPOCH, inputs + [first, candle(2, op='.98', high='1.07', low='.97', close='.98')])
    trade = closed(records)[0]
    assert trade['exit_reason'] == 'STOP_LOSS'
    assert not trade['filled_targets']
    fill = next(e for e in records[-1]['effects'] if e['type'] == 'EXIT_FILL')
    assert decimal(fill['fill_price_usd']) == Decimal('.98') * Decimal('.9999')
    assert decimal(state['cash_usd']) == 5000 + decimal(trade['net_pnl_usd'])


def test_targets_reduce_only_rounding_remainder_and_cash_identity():
    cfg, _, inputs = prepare()
    state, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2, high='1.061')])
    trade = closed(records)[0]
    exits = [e for e in records[-1]['effects'] if e['type'] == 'EXIT_FILL']
    assert [e['reason'] for e in exits] == ['T1', 'T2', 'T3']
    assert sum(decimal(e['quantity']) for e in exits) == decimal(trade['initial_quantity'])
    assert state['position'] is None
    assert decimal(trade['net_pnl_usd']) == decimal(trade['gross_pnl_usd']) - decimal(trade['fees_usd']) - decimal(trade['spread_cost_usd']) + decimal(trade['funding_usd'])
    assert decimal(state['cash_usd']) == 5000 + decimal(trade['net_pnl_usd'])
    assert not performance(state, cfg)['by_strategy']['oracle-v4.0.0']['sample_sufficient']


def test_t1_trailing_stop_is_conservative_before_t2_in_same_bar():
    cfg, _, inputs = prepare(entry=order(stop_after_t1_usd='1'))
    _, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2, high='1.05', low='.9998')])
    trade = closed(records)[0]
    assert trade['filled_targets'] == ['T1']
    assert trade['exit_reason'] == 'STOP_AFTER_T1'


def test_expiry_max_hold_and_kill_switch_do_not_need_model():
    cfg, _, inputs = prepare(entry=order(valid_until_utc='2026-09-20T20:03:00Z'))
    state, records = replay(cfg, EPOCH, inputs + [candle(i, low='1') for i in range(1, 4)])
    assert state['order'] is None and state['position'] is None
    assert records[-1]['effects'][0]['reason'] == 'expired'
    cfg, _, inputs = prepare(config(max_hold_hours=1/60))
    state, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2)])
    assert closed(records)[0]['exit_reason'] == 'MAX_HOLD'
    cfg, _, inputs = prepare(config(equity_floor_fraction='.999'))
    state, _ = replay(cfg, EPOCH, inputs + [candle(1), candle(2, op='.98', high='.985', low='.97', close='.98'), candle(3, op='1.1', high='1.11', low='1.09', close='1.1')])
    assert state['kill_switch']
    with pytest.raises(ValueError, match='disabled'): size_order(order(), state, cfg, {'bid': '1', 'ask': '1.0002'})


def test_funding_sign_proration_and_missing_convention_exclusion():
    for side, multiplier in [('LONG', -1), ('SHORT', 1)]:
        cfg, _, inputs = prepare(entry=order(side=side))
        state, _ = replay(cfg, EPOCH, inputs + [candle(1), candle(2)])
        pos = state['position']
        assert decimal(pos['funding_usd']) == multiplier * decimal(pos['quantity']) * Decimal('.00006') / 60 * 2
    cfg, _, inputs = prepare(config(funding_convention_verified=False))
    state, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2, high='1.061')])
    trade = closed(records)[0]
    assert trade['status'] == 'funding_incomplete'
    stats = performance(state, cfg)
    assert stats['net_pnl_provisional'] and stats['funding_incomplete_trades'] == 1
    assert stats['by_strategy']['oracle-v4.0.0']['complete_trades'] == 0
    assert stats['by_strategy']['oracle-v4.0.0']['net_pnl_usd'] == '0'


def test_management_must_cover_bound_objects_and_stale_close_is_noop():
    cfg, _, inputs = prepare()
    state, _ = replay(cfg, EPOCH, inputs + [candle(1)])
    with pytest.raises(ValueError, match='Exactly one'):
        plan_instruction('forecast-b', '2026-09-20T20:02:00Z', [], [], state, cfg, {'bid': '1', 'ask': '1.0002'})
    action = {'action': 'CLOSE', 'position_id': 'forecast-a-1', 'type': 'MARKET'}
    plan = plan_instruction('forecast-b', '2026-09-20T20:02:00Z', [], [action], state, cfg, {'bid': '1', 'ask': '1.0002'})
    instruction = {'type': 'instruction', 'event_id': 'plan-b', 'at_utc': plan['effective_at_utc'], 'plan': plan}
    state, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2, low='.98'), instruction, candle(3)])
    assert state['position'] is None
    assert any(e['type'] == 'MANAGEMENT_NOOP' for r in records for e in r['effects'])


def test_replay_is_idempotent_context_independent_and_rejects_conflicts():
    cfg, _, inputs = prepare()
    inputs += [candle(1), candle(2, high='1.061')]
    expected = replay(cfg, EPOCH, inputs)
    precision = getcontext().prec
    try:
        getcontext().prec = 6
        assert replay(cfg, EPOCH, inputs) == expected
    finally:
        getcontext().prec = precision
    assert replay(cfg, EPOCH, inputs + [inputs[-1]]) == expected
    wrong = deepcopy(inputs[-1]); wrong['trade']['high'] = '1.08'
    with pytest.raises(ValueError, match='reused'): replay(cfg, EPOCH, inputs + [wrong])
    with pytest.raises(ValueError, match='coverage gap'): replay(cfg, EPOCH, inputs[:3] + [candle(1), candle(3)])
    assert effective_at(EPOCH) == '2026-09-20T20:01:00Z'


def test_stop_entry_gap_keeps_submitted_quantity_and_records_risk_variance():
    entry = order(kind='STOP')
    entry['entry']['price_usd'] = '1.001'
    cfg, plan, inputs = prepare(entry=entry)
    state, records = replay(cfg, EPOCH, inputs + [candle(1, op='1.015', high='1.016', low='1.014', close='1.015')])
    assert state['position']['initial_quantity'] == plan['orders'][0]['size']['quantity']
    assert decimal(state['position']['entry_fill_usd']) == Decimal('1.015') * Decimal('1.0001')
    assert any(e['type'] == 'EXECUTION_RISK_VARIANCE' for e in records[-1]['effects'])


def test_negative_funding_rate_reverses_cash_flow_and_missing_next_hour_is_unknown():
    cfg, _, inputs = prepare()
    inputs[0]['absolute_rate'] = '-.00006'
    inputs[0]['relative_rate'] = '-.00006'
    state, _ = replay(cfg, EPOCH, inputs + [candle(i) for i in range(1, 61)])
    position = state['position']
    assert decimal(position['funding_usd']) == decimal(position['quantity']) * Decimal('.000001') * 59
    assert position['funding_missing_intervals'] == ['2026-09-20T21:00:00Z']


def test_pending_entry_auto_cancels_after_configured_150_minutes():
    cfg, _, inputs = prepare()
    state, records = replay(cfg, EPOCH, inputs + [candle(i, low='1') for i in range(1, 152)])
    assert state['order'] is None
    cancel = [e for r in records for e in r['effects'] if e['type'] == 'ORDER_CANCELLED']
    assert len(cancel) == 1 and cancel[0]['at_utc'] == '2026-09-20T22:31:00Z'


def test_default_72_hour_exit_and_bounded_rolling_state():
    cfg, _, inputs = prepare()
    state, records = replay(cfg, EPOCH, inputs + [candle(i) for i in range(1, 4322)])
    trade = closed(records)[0]
    assert trade['duration_seconds'] == 72 * 3600 and trade['exit_reason'] == 'MAX_HOLD'
    assert state['position'] is None and len(json.dumps(state)) < 6000
    assert trade['status'] == 'funding_incomplete'


def test_explicit_close_at_open_precedes_later_intrabar_stop():
    cfg, _, inputs = prepare()
    state, _ = replay(cfg, EPOCH, inputs + [candle(1)])
    plan = plan_instruction('forecast-close', '2026-09-20T20:02:00Z', [],
        [{'action': 'CLOSE', 'position_id': 'forecast-a-1', 'type': 'MARKET'}], state, cfg, {'bid': '.9998', 'ask': '1.0002'})
    instruction = {'type': 'instruction', 'at_utc': plan['effective_at_utc'], 'event_id': 'plan-close', 'plan': plan}
    _, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2), instruction, candle(3, low='.97')])
    trade = closed(records)[0]
    assert trade['exit_reason'] == 'CLOSE'
    fill = next(e for e in records[-1]['effects'] if e['type'] == 'EXIT_FILL')
    assert decimal(fill['fill_price_usd']) == Decimal('.9999')


def test_modify_stop_tightens_only_and_cancel_addresses_pending_order():
    cfg, _, inputs = prepare()
    state, _ = replay(cfg, EPOCH, inputs + [candle(1)])
    action = {'action': 'MODIFY', 'position_id': 'forecast-a-1', 'stop_usd': '.98'}
    with pytest.raises(ValueError, match='increase stop risk'):
        plan_instruction('bad', '2026-09-20T20:02:00Z', [], [action], state, cfg, {'bid': '.9998', 'ask': '1.0002'})
    action['stop_usd'] = '1'
    plan = plan_instruction('tight', '2026-09-20T20:02:00Z', [], [action], state, cfg, {'bid': '.9998', 'ask': '1.0002'})
    instruction = {'type': 'instruction', 'at_utc': plan['effective_at_utc'], 'event_id': 'tighten', 'plan': plan}
    _, records = replay(cfg, EPOCH, inputs + [candle(1), candle(2), instruction, candle(3)])
    assert closed(records)[0]['exit_reason'] == 'STOP_LOSS'
    pending, _ = replay(cfg, EPOCH, inputs)
    plan = plan_instruction('cancel', '2026-09-20T20:01:00Z', [], [{'action': 'CANCEL', 'client_id': 'forecast-a-1'}], pending, cfg, {'bid': '.9998', 'ask': '1.0002'})
    instruction = {'type': 'instruction', 'at_utc': plan['effective_at_utc'], 'event_id': 'cancel', 'plan': plan}
    state, _ = replay(cfg, EPOCH, inputs + [instruction, candle(2)])
    assert state['order'] is None and state['position'] is None


def test_intrabar_entry_cannot_claim_earlier_target_excursion():
    for side in ('LONG', 'SHORT'):
        cfg, _, inputs = prepare(entry=order(side=side))
        bar = candle(1, high='1.061', low='.9998') if side == 'LONG' else candle(1, high='1.0002', low='.939')
        state, records = replay(cfg, EPOCH, inputs + [bar])
        assert state['position'] is not None
        assert state['position']['filled_targets'] == []
        assert not closed(records)


def test_same_minute_stop_preserves_unknown_funding_status():
    cfg, _, inputs = prepare(config(funding_convention_verified=False))
    state, records = replay(cfg, EPOCH, inputs + [candle(1, low='.98')])
    assert closed(records)[0]['status'] == 'funding_incomplete'
    assert performance(state, cfg)['net_pnl_provisional']


def test_marketable_limit_cannot_claim_maker_fee():
    cfg, _ = seed(config())
    with pytest.raises(ValueError, match='wrong side'):
        size_order(order(), cfg, config(), {'bid': '.9998', 'ask': '1'})
