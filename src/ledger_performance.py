"""Derived, replay-verifiable performance views; no account-state mutation."""
from collections import deque
from datetime import timedelta
from decimal import ROUND_FLOOR, localcontext

from cycles import iso, utc
from ledger import decimal, fee, number, performance, spread_bps

CURVE_LIMIT = 192  # Latest 48 hours at quarter resolution; full MARK series in journal.


def report(state, config, records):
    with localcontext() as context:
        context.prec = 34
        return _report(state, config, records)


def _report(state, config, records):
    result = performance(state, config)
    capital = decimal(config['equity_start_usd'])
    contract = decimal(config['contract_size_dot'])
    step = decimal(config['quantity_step'])
    quote_state, rate = {'spread': None}, None
    entry = None
    funding = decimal('0')
    missing_hours = set()
    missing_minutes = 0
    previous = None
    peak, drawdown = capital, decimal('0')
    curve = deque(maxlen=CURVE_LIMIT)
    buckets = 0
    last_bucket = None
    marks = 0
    benchmark = None
    for record in records:
        event = record['input']
        at = utc(event['at_utc'])
        if event['type'] == 'spread':
            quote_state['spread'] = {'at_utc': event['at_utc'], 'bps': event['spread_bps']}
        elif event['type'] == 'funding_rate':
            rate = event
        elif event['type'] == 'candle':
            spread = spread_bps(quote_state, config, at)
            mark = decimal(event['mark']['close'])
            if entry is None:
                reference = decimal(event['trade']['open'])
                fill = reference * (1 + spread / 20000)
                # One-times capital including entry costs, rounded to contract step.
                unit_cost = fill * contract * (1 + decimal(config['fee_taker_pct']) / 100)
                qty = (capital / unit_cost / step).to_integral_value(rounding=ROUND_FLOOR) * step
                entry = {'at_utc': iso(at), 'input_sha256': record['input_sha256'],
                         'reference_price_usd': number(reference), 'fill_price_usd': number(fill),
                         'quantity': number(qty), 'entry_fee_usd': number(fee(fill, qty, False, config)),
                         'entry_spread_cost_usd': number((fill - reference) * qty * contract)}
            qty = decimal(entry['quantity'])
            if previous is not None:
                missing_minutes += max(0, int((at - previous).total_seconds() / 60) - 1)
            previous = at
            hour = at.replace(minute=0, second=0, microsecond=0)
            if qty:
                if config['funding_convention_verified'] and rate and utc(rate['interval_start_utc']) == hour:
                    funding -= qty * decimal(rate['absolute_rate']) / 60
                else:
                    missing_hours.add(iso(hour))
            equity = capital - decimal(entry['entry_fee_usd']) + funding + qty * contract * (mark - decimal(entry['fill_price_usd']))
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak)
            benchmark = {**entry, 'kind': 'one_times_capital_long_perpetual',
                         'valuation': 'mark; no hypothetical closing fee or spread',
                         'equity_usd': number(equity), 'net_pnl_usd': number(equity - capital),
                         'funding_usd': number(funding), 'max_drawdown_fraction': number(drawdown),
                         'funding_missing_hours': len(missing_hours), 'missing_candle_minutes': missing_minutes,
                         'provisional': bool(missing_hours or missing_minutes),
                         'margin_liquidation_modelled': False}
            mark_effects = [e for e in record['effects'] if e['type'] == 'MARK']
            if len(mark_effects) != 1:
                raise ValueError('One replayed MARK required per candle')
            marks += 1
            end = at + timedelta(minutes=1)
            bucket = int(at.timestamp()) // 900
            point = [iso(end), mark_effects[0]['equity_usd'], number(equity)]
            if bucket == last_bucket:
                curve[-1] = point
            else:
                curve.append(point)
                buckets += 1
            last_bucket = bucket
    result['buy_hold'] = benchmark
    result['buy_hold_costs_included'] = benchmark is not None
    result['excess_net_pnl_usd'] = None if benchmark is None else number(decimal(result['net_pnl_usd']) - decimal(benchmark['net_pnl_usd']))
    result['comparison_provisional'] = result['net_pnl_provisional'] or benchmark is None or benchmark['provisional']
    result['equity_curve'] = {'columns': ['close_utc', 'strategy_equity_usd', 'buy_hold_equity_usd'],
                            'resolution_minutes': 15, 'max_points': CURVE_LIMIT,
                            'total_mark_points': marks, 'truncated': buckets > CURVE_LIMIT,
                            'last_bucket_may_be_partial': True,
                            'full_resolution_source': 'ledger/events/YYYY/MM/DD.jsonl MARK effects',
                            'points': list(curve)}
    return result
