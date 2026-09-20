"""Independent accounting expectations for the passive benchmark and curve."""
from decimal import Decimal
import json
import pytest

import ledger_performance
from ledger import replay
from ledger_performance import report
from ledger_store import append_events, initialize, rebuild_views, verify
from test_ledger import candle, config, EPOCH, seed


def inputs(cfg):
    return seed(cfg)[1]


def test_passive_benchmark_pays_entry_costs_and_funding_on_same_mark_basis():
    cfg = config()
    events = inputs(cfg) + [candle(0), candle(1, op='1.1', high='1.1', low='1.1', close='1.1')]
    state, records = replay(cfg, EPOCH, events)
    metrics = report(state, cfg, records)
    passive = metrics['buy_hold']
    # Independently calculated: floor(5000 / (1.0001 * 1.0005)) = 4997 DOT.
    assert passive['quantity'] == '4997'
    assert Decimal(passive['entry_fee_usd']) == Decimal('2.49874985')
    assert Decimal(passive['entry_spread_cost_usd']) == Decimal('0.4997')
    assert Decimal(passive['funding_usd']) == Decimal('-0.009994')
    expected = Decimal('5000') - Decimal('2.49874985') - Decimal('0.009994') + Decimal('4997') * Decimal('0.0999')
    assert Decimal(passive['equity_usd']) == expected
    assert Decimal(metrics['excess_net_pnl_usd']) == Decimal('5000') - expected
    assert not metrics['comparison_provisional']
    assert metrics['equity_curve']['points'] == [['2026-09-20T20:02:00Z', '5000', str(expected)]]
    assert state['position'] is None  # Passive benchmark never sends a ledger order.


def test_missing_funding_and_candle_gap_keep_comparison_provisional():
    cfg = config()
    events = inputs(cfg) + [candle(0), candle(60)]
    state, records = replay(cfg, EPOCH, events)
    metrics = report(state, cfg, records)
    assert metrics['buy_hold']['funding_missing_hours'] == 1
    assert metrics['buy_hold']['missing_candle_minutes'] == 59
    assert metrics['comparison_provisional']
    assert metrics['buy_hold']['funding_usd'] == '-0.004997'


def test_negative_funding_and_disabled_fees_are_accounted():
    cfg = config(fee_taker_pct='0', fee_maker_pct='0')
    events = inputs(cfg)
    events[0].update(absolute_rate='-0.00006', relative_rate='-0.00006')
    state, records = replay(cfg, EPOCH, events + [candle(0)])
    passive = report(state, cfg, records)['buy_hold']
    assert passive['quantity'] == '4999'
    assert passive['entry_fee_usd'] == '0'
    assert passive['funding_usd'] == '0.004999'
    assert passive['entry_spread_cost_usd'] == '0.4999'


def test_curve_is_bounded_and_keeps_last_mark_of_each_quarter(monkeypatch):
    monkeypatch.setattr(ledger_performance, 'CURVE_LIMIT', 2)
    cfg = config()
    state, records = replay(cfg, EPOCH, inputs(cfg) + [candle(m) for m in range(31)])
    curve = report(state, cfg, records)['equity_curve']
    assert curve['truncated'] and curve['total_mark_points'] == 31
    assert [p[0] for p in curve['points']] == ['2026-09-20T20:30:00Z', '2026-09-20T20:31:00Z']


def test_performance_tampering_rejected_and_rebuilt_from_journal(tmp_path):
    cfg = config()
    initialize(tmp_path, cfg, EPOCH)
    assert verify(tmp_path)['performance']['buy_hold'] is None
    append_events(tmp_path, inputs(cfg) + [candle(0)])
    path = tmp_path / 'ledger/performance.json'
    expected = json.loads(path.read_text())
    corrupt = json.loads(path.read_text())
    corrupt['equity_curve']['points'][0][2] = '99999'
    path.write_text(json.dumps(corrupt))
    with pytest.raises(ValueError, match='performance differs'):
        verify(tmp_path)
    rebuild_views(tmp_path)
    assert json.loads(path.read_text()) == expected
