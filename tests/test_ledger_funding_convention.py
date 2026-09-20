"""Official linear-contract examples pin units/sign independently of DOT ratios."""
from datetime import timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path

import pytest
from cycles import iso, utc
from ledger import _accrue_funding, initial_state

ROOT = Path(__file__).parents[1]
EVIDENCE = json.loads((ROOT / 'tests/fixtures/funding_convention.json').read_text())
EPOCH = '2026-09-20T20:00:00Z'


def accrue(side, quantity, absolute, relative, minutes, mark):
    config = json.loads((ROOT / 'config/ledger.json').read_text())
    assert config['funding_convention_verified'] is True
    # Documentary verification must not turn on the account.
    assert config['enabled'] is False
    config['enabled'] = True
    state = initial_state(config, EPOCH)
    initial_cash = Decimal(state['cash_usd'])
    state['mark_usd'] = mark
    state['position'] = {'position_id': 'source-example', 'side': side,
                         'quantity': quantity, 'funding_usd': '0',
                         'funding_missing_intervals': []}
    state['funding_rate'] = {'interval_start_utc': EPOCH, 'absolute_rate': absolute,
                             'relative_rate': relative, 'input_sha256': '0' * 64}
    effects = []
    with localcontext() as ctx:
        ctx.prec = 34
        for minute in range(minutes):
            _accrue_funding(state, config, effects, iso(utc(EPOCH) + timedelta(minutes=minute)))
    assert state['position']['funding_missing_intervals'] == []
    assert len(effects) == minutes
    assert all(item['type'] == 'FUNDING_ACCRUAL' for item in effects)
    payment = Decimal(state['position']['funding_usd'])
    assert abs(Decimal(state['cash_usd']) - initial_cash - payment) < Decimal('1e-25')
    return payment


@pytest.mark.parametrize('example', EVIDENCE['examples'])
def test_official_linear_examples(example):
    assert Decimal(example['reference_index']) * Decimal(example['relative_rate']) == Decimal(example['absolute_rate'])
    result = accrue(example['side'], example['quantity'], example['absolute_rate'],
                    example['relative_rate'], example['minutes'], example['current_mark'])
    assert abs(result - Decimal(example['expected_usd'])) < Decimal('1e-25')


def test_frozen_dot_rates_use_absolute_cash_flow_for_both_sides():
    rates = json.loads((ROOT / 'tests/fixtures/v4/kraken_funding.json').read_text())['rates'][-24:]
    for rate in rates:
        absolute, relative = str(rate['fundingRate']), str(rate['relativeFundingRate'])
        # Deliberately unrelated current mark: conversion must not use it.
        for side, sign in [('LONG', -1), ('SHORT', 1)]:
            payment = accrue(side, '100', absolute, relative, 30, '999')
            expected = sign * Decimal('100') * Decimal(absolute) / 2
            assert abs(payment - expected) < Decimal('1e-25')
