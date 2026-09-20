"""Pure deterministic PF_DOTUSD paper ledger. Decimal strings are the storage contract.

No clock, network, filesystem or model decisions. Input time is UTC; candles are
closed one-minute trade/mark bars labelled by OPEN time. Equal timestamps sort
funding rates, spreads, instructions, then candles. Caller supplies that ordering.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import hashlib
import json

from cycles import utc, iso

PRIORITY = {'funding_rate': 0, 'spread': 1, 'instruction': 2, 'candle': 3}
ZERO = Decimal(0)
ONE = Decimal(1)
BPS = Decimal(10000)


def decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Finite decimal required')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Finite decimal required') from exc
    if not result.is_finite():
        raise ValueError('Finite decimal required')
    return result


def number(value):
    result = format(decimal(value), 'f')
    return (result.rstrip('0').rstrip('.') if '.' in result else result) or '0'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def state_hash(state):
    return digest({k: v for k, v in state.items() if k != 'state_sha256'})


def effective_at(created):
    # Strictly later than publication, including timestamps exactly on a minute.
    return iso(utc(created).replace(second=0, microsecond=0) + timedelta(minutes=1))


def validate_config(config):
    if config['instrument'] != 'PF_DOTUSD' or config['ledger_version'] != '1.0.0':
        raise ValueError('Unsupported ledger instrument/version')
    if config['max_open_positions'] != 1 or decimal(config['contract_size_dot']) != 1:
        raise ValueError('v4.0 supports one 1-DOT-contract position')
    if decimal(config['slippage_bps']) != 0:
        raise ValueError('Only explicit zero-slippage paper model implemented')
    positive = ('equity_start_usd', 'quantity_step', 'tick_size_usd', 'risk_fraction_per_trade',
                'max_notional_multiple_of_equity', 'min_stop_bps', 'max_stop_bps',
                'min_net_reward_risk_t1', 'max_order_age_minutes', 'max_hold_hours',
                'funding_interval_minutes', 'equity_floor_fraction')
    if any(decimal(config[k]) <= 0 for k in positive):
        raise ValueError('Positive ledger limits required')
    if config['funding_interval_minutes'] != 60:
        raise ValueError('Fixture-backed funding is hourly')
    if not 0 < decimal(config['risk_fraction_per_trade']) <= 1 or not 0 < decimal(config['equity_floor_fraction']) <= 1:
        raise ValueError('Invalid equity/risk fraction')
    if decimal(config['min_stop_bps']) > decimal(config['max_stop_bps']):
        raise ValueError('Inverted stop limits')
    if any(decimal(config[k]) < 0 for k in ('fee_maker_pct', 'fee_taker_pct', 'spread_floor_bps')):
        raise ValueError('Negative cost assumption')
    if set(config['risk_tiers']) != {'FULL', 'HALF', 'QUARTER'} or any(
            not 0 < decimal(v) <= 1 for v in config['risk_tiers'].values()):
        raise ValueError('Invalid risk tiers')
    if not isinstance(config['risk_budget_includes_costs'], bool):
        raise ValueError('Explicit risk-cost policy required')
    if config['funding_positive_payer'] != 'LONG' or not isinstance(config['funding_convention_verified'], bool):
        raise ValueError('Unsupported funding convention')
    canonical(config)
    return config


def initial_state(config, epoch):
    validate_config(config)
    epoch = iso(epoch)
    if config.get('ledger_epoch_utc') and iso(config['ledger_epoch_utc']) != epoch:
        raise ValueError('Ledger epoch/config mismatch')
    state = {'ledger_version': config['ledger_version'], 'config_sha256': digest(config),
             'epoch_utc': epoch, 'instrument': config['instrument'],
             'cash_usd': number(config['equity_start_usd']), 'equity_usd': number(config['equity_start_usd']),
             'unrealized_pnl_usd': '0', 'position': None, 'order': None,
             'spread': None, 'funding_rate': None, 'mark_usd': None, 'buy_hold_entry_usd': None,
             'kill_switch': False, 'last_event_key': None, 'last_candle_utc': None,
             'last_input_sha256': None, 'processed_event_count': 0,
             'trade_count': 0, 'funding_incomplete_trade_count': 0, 'strategy_totals': {},
             'equity_peak_usd': number(config['equity_start_usd']), 'max_drawdown_fraction': '0'}
    state['state_sha256'] = state_hash(state)
    return state


def sign(side):
    if side not in ('LONG', 'SHORT'):
        raise ValueError('Unknown side')
    return ONE if side == 'LONG' else -ONE


def tick(value, config):
    value = decimal(value)
    if value <= 0 or value % decimal(config['tick_size_usd']) != 0:
        raise ValueError('Price must be positive and on tick grid')
    return value


def validate_targets(targets, side, entry, config):
    if len(targets) != 3 or [t['id'] for t in targets] != ['T1', 'T2', 'T3']:
        raise ValueError('Ordered T1/T2/T3 required')
    direction = sign(side)
    previous = decimal(entry)
    fractions = ZERO
    for target in targets:
        price = tick(target['price_usd'], config)
        fraction = decimal(target['fraction'])
        if direction * (price - previous) <= 0 or not 0 < fraction < 1:
            raise ValueError('Targets must advance from entry with positive shares')
        fractions += fraction
        previous = price
    if fractions != 1:
        raise ValueError('Target fractions must sum to one')


def spread_bps(state, config, at):
    quote = state['spread']
    if quote is None or utc(quote['at_utc']) > utc(at):
        raise ValueError('Recorded spread before fill required')
    return max(decimal(config['spread_floor_bps']), decimal(quote['bps']))


def fee(price, quantity, maker, config):
    return price * quantity * decimal(config['contract_size_dot']) * decimal(
        config['fee_maker_pct'] if maker else config['fee_taker_pct']) / 100


def cost_per_contract(order, config, spread):
    entry = decimal(order['entry']['price_usd'])
    stop = decimal(order['stop_usd'])
    target = decimal(order['targets'][0]['price_usd'])
    maker = order['entry']['type'] == 'LIMIT'
    entry_cost = fee(entry, ONE, maker, config) + (ZERO if maker else entry * spread / (2 * BPS))
    stop_cost = entry_cost + fee(stop, ONE, False, config) + stop * spread / (2 * BPS)
    target_cost = entry_cost + fee(target, ONE, True, config)
    return stop_cost, target_cost


def size_order(order, state, config, quote):
    """Pure Python sizing; configurable risk includes stop costs by default."""
    with localcontext() as context:
        context.prec = 34
        if state['kill_switch'] or state['position'] is not None or state['order'] is not None:
            raise ValueError('Entries disabled or account already occupied')
        direction = sign(order['side'])
        entry = tick(order['entry']['price_usd'], config)
        stop = tick(order['stop_usd'], config)
        if order['entry']['type'] not in ('LIMIT', 'MARKET', 'STOP'):
            raise ValueError('Unknown order type')
        distance = direction * (entry - stop)
        if distance <= 0:
            raise ValueError('Stop must oppose entry')
        stop_bps = distance / entry * BPS
        if not decimal(config['min_stop_bps']) <= stop_bps <= decimal(config['max_stop_bps']):
            raise ValueError('Stop distance outside configured range')
        validate_targets(order['targets'], order['side'], entry, config)
        if order.get('stop_after_t1_usd') is not None:
            trail = tick(order['stop_after_t1_usd'], config)
            if direction * (trail - stop) < 0 or direction * (decimal(order['targets'][0]['price_usd']) - trail) <= 0:
                raise ValueError('T1 stop may tighten only and must precede T1')
        bid, ask = decimal(quote['bid']), decimal(quote['ask'])
        if not 0 < bid <= ask:
            raise ValueError('Invalid bound quote')
        if order['entry']['type'] == 'LIMIT' and (entry >= ask if direction == 1 else entry <= bid):
            raise ValueError('Limit price on wrong side of bound quote')
        if order['entry']['type'] == 'STOP' and (entry <= ask if direction == 1 else entry >= bid):
            raise ValueError('Stop entry must be beyond bound quote')
        spread = max(decimal(config['spread_floor_bps']), (ask - bid) / ((ask + bid) / 2) * BPS)
        loss_cost, profit_cost = cost_per_contract(order, config, spread)
        reward = abs(decimal(order['targets'][0]['price_usd']) - entry) - profit_cost
        net_rr = reward / (distance + loss_cost)
        if net_rr < decimal(config['min_net_reward_risk_t1']):
            raise ValueError('Net T1 reward/risk below minimum')
        equity = decimal(state['equity_usd'])
        if equity < decimal(config['equity_start_usd']) * decimal(config['equity_floor_fraction']):
            raise ValueError('Entries disabled below equity floor')
        budget = equity * decimal(config['risk_fraction_per_trade']) * decimal(config['risk_tiers'][order['risk_tier']])
        unit = decimal(config['quantity_step'])
        contract = decimal(config['contract_size_dot'])
        risk_unit = distance * contract + (loss_cost if config['risk_budget_includes_costs'] else ZERO)
        multiple = decimal(config['max_notional_multiple_of_equity'])
        entry_cost = fee(entry, ONE, order['entry']['type'] == 'LIMIT', config)
        if order['entry']['type'] != 'LIMIT':
            entry_cost += entry * spread / (2 * BPS)
        quantity = min(budget / risk_unit, equity * multiple / (entry * contract + multiple * entry_cost))
        quantity = (quantity / unit).to_integral_value(rounding=ROUND_FLOOR) * unit
        if quantity <= 0 or any((quantity * decimal(t['fraction']) / unit).to_integral_value(rounding=ROUND_FLOOR) == 0 for t in order['targets'][:2]):
            raise ValueError('Insufficient quantity for three nonzero exits')
        return {'quantity': number(quantity), 'risk_budget_usd': number(budget),
                'initial_price_risk_usd': number(quantity * distance * contract),
                'estimated_stop_loss_including_costs_usd': number(quantity * (distance * contract + loss_cost)),
                'notional_usd': number(quantity * entry * contract), 'net_reward_risk_t1': number(net_rr),
                'cost_assumptions': {'spread_bps': number(spread), 'slippage_bps': config['slippage_bps'],
                                     'fee_maker_pct': config['fee_maker_pct'], 'fee_taker_pct': config['fee_taker_pct']},
                'ledger_state_sha256': digest(state), 'config_sha256': digest(config)}


def plan_instruction(forecast_id, created, orders, management, state, config, quote, strategy_version='oracle-v4.0.0'):
    if state.get('state_sha256') != state_hash(state) or state['config_sha256'] != digest(config):
        raise ValueError('Invalid bound ledger state')
    if utc(created) < utc(state['epoch_utc']) or (state['last_candle_utc'] and utc(created) < utc(state['last_candle_utc']) + timedelta(minutes=1)):
        raise ValueError('Forecast predates bound market evidence')
    if state['last_event_key'] and utc(created) < utc(state['last_event_key'][0]):
        raise ValueError('Forecast predates bound ledger evidence')
    if len(orders) > 1:
        raise ValueError('At most one entry')
    expected = set()
    if state['order']: expected.add(('order', state['order']['client_id']))
    if state['position']: expected.add(('position', state['position']['position_id']))
    actual = []
    for action in management:
        kind = 'order' if 'client_id' in action else 'position'
        ident = action.get('client_id') if kind == 'order' else action.get('position_id')
        if action['action'] not in (('HOLD', 'CANCEL') if kind == 'order' else ('HOLD', 'CLOSE', 'MODIFY')):
            raise ValueError('Management action does not apply to object')
        if action['action'] == 'CLOSE' and action.get('type') != 'MARKET':
            raise ValueError('Only market close supported')
        if action['action'] == 'MODIFY' and not any(k in action for k in ('stop_usd', 'targets')):
            raise ValueError('MODIFY requires numeric changes')
        if kind == 'position' and state['position'] and ident == state['position']['position_id']:
            position = state['position']
            if action['action'] == 'MODIFY':
                if 'stop_usd' in action:
                    stop = tick(action['stop_usd'], config)
                    if sign(position['side']) * (stop - decimal(position['stop_usd'])) < 0:
                        raise ValueError('Management cannot increase stop risk')
                if 'targets' in action:
                    if position['filled_targets']:
                        raise ValueError('Target modification after partial exit not supported in v4.0')
                    validate_targets(action['targets'], position['side'], position['entry_fill_usd'], config)
        actual.append((kind, ident))
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Exactly one management action per bound open object required')
    prepared = []
    for order in orders:
        if order['client_id'] != forecast_id + '-1' or order.get('action') != 'ENTER':
            raise ValueError('Entry identity must bind forecast')
        if utc(order['valid_until_utc']) <= utc(effective_at(created)):
            raise ValueError('Order expires before becoming executable')
        size = size_order(order, state, config, quote)
        prepared.append({'order': deepcopy(order), 'size': size})
    return {'forecast_id': forecast_id, 'strategy_version': strategy_version, 'created_at_utc': iso(created),
            'effective_at_utc': effective_at(created), 'ledger_state_sha256': digest(state),
            'config_sha256': digest(config), 'quote': deepcopy(quote), 'orders': prepared,
            'management': deepcopy(management)}


def _mark(state, price, config):
    state['mark_usd'] = number(price)
    if state['buy_hold_entry_usd'] is None:
        state['buy_hold_entry_usd'] = number(price)
    position = state['position']
    unrealized = ZERO if position is None else sign(position['side']) * decimal(position['quantity']) * decimal(config['contract_size_dot']) * (price - decimal(position['entry_fill_usd']))
    state['unrealized_pnl_usd'] = number(unrealized)
    state['equity_usd'] = number(decimal(state['cash_usd']) + unrealized)
    if decimal(state['equity_usd']) < decimal(config['equity_start_usd']) * decimal(config['equity_floor_fraction']):
        state['kill_switch'] = True  # Latched; no automatic risk reset after a bounce.


def _emit(outputs, kind, at, **fields):
    outputs.append({'type': kind, 'at_utc': iso(at), **fields})


def _fill_price(reference, side, spread, maker):
    return reference if maker else reference * (ONE + sign(side) * spread / (2 * BPS))


def _exit(state, config, outputs, at, quantity, reference, maker, reason, spread):
    position = state['position']
    quantity = min(quantity, decimal(position['quantity']))
    fill = _fill_price(reference, 'SHORT' if position['side'] == 'LONG' else 'LONG', spread, maker)
    cost = fee(fill, quantity, maker, config)
    pnl = sign(position['side']) * quantity * decimal(config['contract_size_dot']) * (fill - decimal(position['entry_fill_usd']))
    gross = sign(position['side']) * quantity * decimal(config['contract_size_dot']) * (reference - decimal(position['entry_reference_usd']))
    spread_cost = abs(fill - reference) * quantity * decimal(config['contract_size_dot'])
    state['cash_usd'] = number(decimal(state['cash_usd']) + pnl - cost)
    for key, value in [('gross_pnl_usd', gross), ('spread_cost_usd', spread_cost), ('fees_usd', cost)]:
        position[key] = number(decimal(position[key]) + value)
    position['quantity'] = number(decimal(position['quantity']) - quantity)
    _emit(outputs, 'EXIT_FILL', at, position_id=position['position_id'], reason=reason,
          quantity=number(quantity), reference_price_usd=number(reference), fill_price_usd=number(fill),
          fee_usd=number(cost), spread_cost_usd=number(spread_cost), realized_pnl_usd=number(pnl))
    if decimal(position['quantity']) == 0:
        trade = deepcopy(position)
        trade.update(closed_at_utc=iso(at), exit_reason=reason,
                     trade_id=position['position_id'], duration_seconds=int((utc(at) - utc(position['opened_at_utc'])).total_seconds()))
        trade['net_pnl_usd'] = number(decimal(trade['gross_pnl_usd']) - decimal(trade['spread_cost_usd']) - decimal(trade['fees_usd']) + decimal(trade['funding_usd']))
        trade['net_r'] = number(decimal(trade['net_pnl_usd']) / decimal(trade['initial_price_risk_usd']))
        trade['status'] = 'funding_incomplete' if trade['funding_missing_intervals'] else 'complete'
        state['trade_count'] += 1
        version = trade['strategy_version']
        totals = state['strategy_totals'].setdefault(version, {'complete_trades': 0, 'funding_incomplete_trades': 0,
            'wins': 0, 'gains_usd': '0', 'losses_usd': '0', 'sum_net_r': '0'})
        if trade['status'] == 'complete':
            totals['complete_trades'] += 1
            net = decimal(trade['net_pnl_usd'])
            totals['wins'] += net > 0
            totals['gains_usd'] = number(decimal(totals['gains_usd']) + max(ZERO, net))
            totals['losses_usd'] = number(decimal(totals['losses_usd']) + max(ZERO, -net))
            totals['sum_net_r'] = number(decimal(totals['sum_net_r']) + decimal(trade['net_r']))
        else:
            state['funding_incomplete_trade_count'] += 1
            totals['funding_incomplete_trades'] += 1
        state['position'] = None
        _emit(outputs, 'TRADE_CLOSED', at, trade_id=trade['trade_id'], net_pnl_usd=trade['net_pnl_usd'], status=trade['status'], trade=trade)


def _bar(value):
    out = {key: decimal(value[key]) for key in ('open', 'high', 'low', 'close')}
    if not 0 < out['low'] <= min(out['open'], out['close']) <= max(out['open'], out['close']) <= out['high']:
        raise ValueError('Invalid OHLC bar')
    return out


def _apply_instruction(state, event, config, outputs):
    plan = event['plan']
    at = event['at_utc']
    if plan['config_sha256'] != digest(config) or iso(at) != plan['effective_at_utc']:
        raise ValueError('Instruction config/effective-time mismatch')
    for action in plan['management']:
        kind = 'order' if 'client_id' in action else 'position'
        ident_key = 'client_id' if kind == 'order' else 'position_id'
        obj = state[kind]
        if obj is None or obj[ident_key] != action[ident_key]:
            _emit(outputs, 'MANAGEMENT_NOOP', at, action=action['action'], target_id=action[ident_key], reason='bound_object_already_closed')
            continue
        if action['action'] == 'CANCEL':
            state['order'] = None
        elif action['action'] == 'CLOSE':
            obj['close_requested'] = True
        elif action['action'] == 'MODIFY':
            if 'stop_usd' in action:
                stop = tick(action['stop_usd'], config)
                if sign(obj['side']) * (stop - decimal(obj['stop_usd'])) < 0:
                    raise ValueError('Management cannot increase stop risk')
                obj['stop_usd'] = number(stop)
            if 'targets' in action:
                if obj['filled_targets']:
                    raise ValueError('Target modification after partial exit not supported in v4.0')
                validate_targets(action['targets'], obj['side'], obj['entry_fill_usd'], config)
                obj['targets'] = deepcopy(action['targets'])
        _emit(outputs, 'MANAGEMENT', at, action=action['action'], target_id=action[ident_key])
    for entry in plan['orders']:
        if state['kill_switch'] or state['order'] or state['position']:
            _emit(outputs, 'ENTRY_REJECTED', at, reason='account_changed_or_kill_switch', client_id=entry['order']['client_id'])
            continue
        obj = deepcopy(entry['order'])
        obj.update(size=deepcopy(entry['size']), submitted_at_utc=iso(at),
                   forecast_id=plan['forecast_id'], strategy_version=plan['strategy_version'])
        deadline = min(utc(obj['valid_until_utc']), utc(at) + timedelta(minutes=config['max_order_age_minutes']))
        obj['expires_at_utc'] = iso(deadline)
        state['order'] = obj
        _emit(outputs, 'ORDER_ACCEPTED', at, client_id=obj['client_id'], expires_at_utc=obj['expires_at_utc'])


def _accrue_funding(state, config, outputs, at, coverage_only=False):
    position = state['position']
    if position is None:
        return
    interval_seconds = int(config['funding_interval_minutes']) * 60
    interval_start = utc(at).replace(minute=0, second=0, microsecond=0)
    if interval_seconds != 3600:
        raise ValueError('Fixture-backed funding is hourly')
    rate = state['funding_rate']
    complete = config['funding_convention_verified'] and rate and utc(rate['interval_start_utc']) == interval_start
    if not complete:
        marker = iso(interval_start)
        if marker not in position['funding_missing_intervals']:
            position['funding_missing_intervals'].append(marker)
            _emit(outputs, 'FUNDING_UNKNOWN', at, position_id=position['position_id'], interval_start_utc=marker)
        return
    if coverage_only:
        return
    # Kraken accrues continuously: model one-minute exposure, not a whole hourly
    # payment for a one-minute trade. Absolute rate is USD per 1-DOT contract/hour.
    payment = -sign(position['side']) * decimal(position['quantity']) * decimal(rate['absolute_rate']) / 60
    position['funding_usd'] = number(decimal(position['funding_usd']) + payment)
    state['cash_usd'] = number(decimal(state['cash_usd']) + payment)
    _emit(outputs, 'FUNDING_ACCRUAL', at, position_id=position['position_id'], amount_usd=number(payment),
          interval_start_utc=iso(interval_start), rate_input_sha256=rate['input_sha256'], exposure_seconds=60)


def _apply_candle(state, event, config, outputs):
    at = utc(event['at_utc'])
    if at.second or at.microsecond:
        raise ValueError('One-minute candle must align to UTC minute')
    trade, mark = _bar(event['trade']), _bar(event['mark'])
    if state['last_candle_utc'] and at <= utc(state['last_candle_utc']):
        raise ValueError('Candle already processed')
    if state['last_candle_utc'] and at != utc(state['last_candle_utc']) + timedelta(minutes=1):
        # Never silently skip unobserved stops while a position/order exists.
        if state['position'] or state['order']:
            raise ValueError('Market coverage gap while account has exposure')
        _emit(outputs, 'MARKET_GAP', at, previous_candle_utc=state['last_candle_utc'])
    spread = spread_bps(state, config, at)
    _mark(state, mark['open'], config)
    entry_during_bar = False
    order = state['order']
    if order and (at >= utc(order['expires_at_utc']) or state['kill_switch']):
        _emit(outputs, 'ORDER_CANCELLED', at, client_id=order['client_id'], reason='kill_switch' if state['kill_switch'] else 'expired')
        state['order'] = order = None
    if order:
        entry = decimal(order['entry']['price_usd'])
        direction = sign(order['side'])
        maker = order['entry']['type'] == 'LIMIT'
        crossed = (trade['low'] <= entry - decimal(config['tick_size_usd']) if direction == 1 else trade['high'] >= entry + decimal(config['tick_size_usd'])) if maker else (
            True if order['entry']['type'] == 'MARKET' else (mark['high'] >= entry if direction == 1 else mark['low'] <= entry))
        if crossed:
            # OHLC cannot establish whether the profitable excursion preceded
            # an intrabar entry. Defer targets unless entry occurred at open.
            entry_during_bar = (trade['open'] > entry - decimal(config['tick_size_usd']) if direction == 1 else trade['open'] < entry + decimal(config['tick_size_usd'])) if maker else (
                False if order['entry']['type'] == 'MARKET' else (mark['open'] < entry if direction == 1 else mark['open'] > entry))
            reference = entry if maker else (trade['open'] if order['entry']['type'] == 'MARKET' else
                                            max(entry, mark['open']) if direction == 1 else min(entry, mark['open']))
            fill = _fill_price(reference, order['side'], spread, maker)
            budget = decimal(order['size']['risk_budget_usd'])
            qty = decimal(order['size']['quantity'])
            # Resting exchange orders have a fixed quantity. Never pretend that
            # a gap allowed us to resize/cancel a fill after seeing its outcome.
            distance = abs(decimal(order['entry']['price_usd']) - decimal(order['stop_usd']))
            stop_fill = _fill_price(decimal(order['stop_usd']), 'SHORT' if direction == 1 else 'LONG', spread, False)
            estimated_loss = qty * max(ZERO, direction * (fill - stop_fill)) + fee(fill, qty, maker, config) + fee(stop_fill, qty, False, config)
            entry_equity = decimal(state['equity_usd']) - fee(fill, qty, maker, config) - qty * max(ZERO, direction * (fill - mark['open']))
            cap = max(ZERO, entry_equity * decimal(config['max_notional_multiple_of_equity']))
            if estimated_loss > budget or qty * fill > cap:
                _emit(outputs, 'EXECUTION_RISK_VARIANCE', at, client_id=order['client_id'],
                      estimated_stop_loss_usd=number(estimated_loss), risk_budget_usd=number(budget),
                      notional_usd=number(qty * fill), notional_cap_usd=number(cap))
            if qty > 0:
                cost = fee(fill, qty, maker, config)
                state['cash_usd'] = number(decimal(state['cash_usd']) - cost)
                position = {'position_id': order['client_id'], 'forecast_id': order['forecast_id'], 'strategy_version': order['strategy_version'],
                            'entry_input_sha256': digest(event), 'side': order['side'], 'quantity': number(qty), 'initial_quantity': number(qty),
                            'entry_fill_usd': number(fill), 'entry_reference_usd': number(reference),
                            'initial_price_risk_usd': number(qty * distance), 'risk_budget_usd': number(budget),
                            'stop_usd': order['stop_usd'], 'targets': deepcopy(order['targets']),
                            'stop_after_t1_usd': order.get('stop_after_t1_usd'), 'filled_targets': [],
                            'opened_at_utc': iso(at), 'gross_pnl_usd': '0', 'fees_usd': number(cost),
                            'spread_cost_usd': number(abs(fill - reference) * qty), 'funding_usd': '0',
                            'funding_missing_intervals': [], 'close_requested': False}
                state['position'], state['order'] = position, None
                _emit(outputs, 'ENTRY_FILL', at, position_id=position['position_id'], quantity=number(qty),
                      reference_price_usd=number(reference), fill_price_usd=number(fill), fee_usd=number(cost))
    position = state['position']
    if position:
        _accrue_funding(state, config, outputs, at, coverage_only=True)
        direction = sign(position['side'])
        stop = decimal(position['stop_usd'])
        stop_hit = mark['low'] <= stop if direction == 1 else mark['high'] >= stop
        if position['close_requested'] or (at - utc(position['opened_at_utc'])).total_seconds() >= config['max_hold_hours'] * 3600:
            _exit(state, config, outputs, at, decimal(position['quantity']), trade['open'], False,
                  'CLOSE' if position['close_requested'] else 'MAX_HOLD', spread)
        elif stop_hit:
            reference = min(stop, mark['open']) if direction == 1 else max(stop, mark['open'])
            _exit(state, config, outputs, at, decimal(position['quantity']), reference, False, 'STOP_LOSS', spread)
        elif not entry_during_bar:
            for target in position['targets']:
                if target['id'] in position['filled_targets']:
                    continue
                price = decimal(target['price_usd'])
                crossed = trade['high'] >= price + decimal(config['tick_size_usd']) if direction == 1 else trade['low'] <= price - decimal(config['tick_size_usd'])
                if not crossed:
                    break
                qty = decimal(position['quantity']) if target['id'] == 'T3' else (
                    decimal(position['initial_quantity']) * decimal(target['fraction']) / decimal(config['quantity_step'])).to_integral_value(rounding=ROUND_FLOOR) * decimal(config['quantity_step'])
                if qty <= 0:
                    raise ValueError('Target quantity rounded to zero')
                position['filled_targets'].append(target['id'])
                _exit(state, config, outputs, at, qty, price, True, target['id'], spread)
                if state['position'] is None:
                    break
                if target['id'] == 'T1' and position['stop_after_t1_usd'] is not None:
                    trail = decimal(position['stop_after_t1_usd'])
                    position['stop_usd'] = number(trail)
                    if mark['low'] <= trail if direction == 1 else mark['high'] >= trail:
                        _exit(state, config, outputs, at, decimal(position['quantity']), trail, False, 'STOP_AFTER_T1', spread)
                        break
    # Only positions retained through this whole modelled minute accrue funding.
    _accrue_funding(state, config, outputs, at)
    _mark(state, mark['close'], config)
    state['last_candle_utc'] = iso(at)
    peak = max(decimal(state['equity_peak_usd']), decimal(state['equity_usd']))
    state['equity_peak_usd'] = number(peak)
    state['max_drawdown_fraction'] = number(max(decimal(state['max_drawdown_fraction']), (peak - decimal(state['equity_usd'])) / peak))
    _emit(outputs, 'MARK', at, price_usd=number(mark['close']), equity_usd=state['equity_usd'],
          unrealized_pnl_usd=state['unrealized_pnl_usd'], kill_switch=state['kill_switch'])


def step(state, event, config):
    """Return a new state and deterministic derived effects; never mutate inputs."""
    with localcontext() as context:
        context.prec = 34
        validate_config(config)
        if state.get('state_sha256') != state_hash(state) or state['config_sha256'] != digest(config):
            raise ValueError('Ledger state/config hash mismatch')
        kind = event['type']
        if kind not in PRIORITY:
            raise ValueError('Unknown input event')
        at = iso(event['at_utc'])
        event_id = event['event_id']
        event_hash = digest(event)
        if not isinstance(event_id, str) or not event_id:
            raise ValueError('Event ID required')
        existing = state['last_event_key'] is not None and state['last_event_key'][2] == event_id
        if existing:
            if state['last_input_sha256'] != event_hash:
                raise ValueError('Event ID reused with different input')
            return deepcopy(state), []
        key = [at, PRIORITY[kind], event_id]
        # Compare timezone-aware times, not variable-length ISO strings.
        previous = state['last_event_key']
        if utc(at) < utc(state['epoch_utc']) or (previous and (utc(at), key[1], key[2]) < (utc(previous[0]), previous[1], previous[2])):
            raise ValueError('Events must be in deterministic chronological order')
        result, outputs = deepcopy(state), []
        if kind == 'spread':
            bps = decimal(event['spread_bps'])
            if bps < 0:
                raise ValueError('Negative spread')
            result['spread'] = {'at_utc': at, 'bps': number(bps)}
        elif kind == 'funding_rate':
            if utc(at).minute or utc(at).second or utc(at).microsecond:
                raise ValueError('Funding interval must align to UTC hour')
            absolute, relative = decimal(event['absolute_rate']), decimal(event['relative_rate'])
            if absolute * relative < 0 or (absolute == 0) != (relative == 0):
                raise ValueError('Absolute/relative funding signs disagree')
            if utc(event['interval_start_utc']) != utc(at):
                raise ValueError('Funding timestamp labels interval start')
            result['funding_rate'] = {'interval_start_utc': at, 'absolute_rate': number(event['absolute_rate']),
                                     'relative_rate': number(event['relative_rate']), 'input_sha256': event_hash}
        elif kind == 'instruction':
            _apply_instruction(result, event, config, outputs)
        else:
            _apply_candle(result, event, config, outputs)
        result['last_event_key'] = key
        result['last_input_sha256'] = event_hash
        result['processed_event_count'] += 1
        if result['mark_usd'] is not None:
            _mark(result, decimal(result['mark_usd']), config)
        result['state_sha256'] = state_hash(result)
        return result, outputs


def replay(config, epoch, events):
    state = initial_state(config, epoch)
    records, seen = [], {}
    for event in events:
        if event['event_id'] in seen:
            if seen[event['event_id']] != digest(event):
                raise ValueError('Event ID reused with different input')
            continue
        seen[event['event_id']] = digest(event)
        before = state['state_sha256']
        next_state, effects = step(state, event, config)
        if next_state['state_sha256'] == before:  # Exact idempotent duplicate.
            continue
        for effect in effects:
            effect['input_sha256'] = digest(event)
        records.append({'input': deepcopy(event), 'input_sha256': digest(event), 'previous_state_sha256': before,
                        'effects': effects, 'state_sha256': next_state['state_sha256'], 'state_document_sha256': digest(next_state)})
        state = next_state
    return state, records


def performance(state, config):
    with localcontext() as context:
        context.prec = 34
        groups = {}
        for version, totals in sorted(state['strategy_totals'].items()):
            count = totals['complete_trades']
            gains, losses = decimal(totals['gains_usd']), decimal(totals['losses_usd'])
            groups[version] = {'complete_trades': count, 'funding_incomplete_trades': totals['funding_incomplete_trades'],
                               'net_pnl_usd': number(gains - losses), 'profit_factor': number(gains / losses) if losses else None,
                               'win_rate': number(decimal(totals['wins']) / count) if count else None,
                               'expectancy_r': number(decimal(totals['sum_net_r']) / count) if count else None,
                               'sample_sufficient': count >= 30}
        benchmark = None if state['buy_hold_entry_usd'] is None else decimal(config['equity_start_usd']) * (decimal(state['mark_usd']) / decimal(state['buy_hold_entry_usd']) - 1)
        return {'ledger_state_sha256': state['state_sha256'], 'equity_usd': state['equity_usd'],
                'net_pnl_usd': number(decimal(state['equity_usd']) - decimal(config['equity_start_usd'])),
                'funding_incomplete_trades': state['funding_incomplete_trade_count'],
                'net_pnl_provisional': bool(state['funding_incomplete_trade_count']) or bool((state['position'] or {}).get('funding_missing_intervals')),
                'max_drawdown_fraction': state['max_drawdown_fraction'], 'buy_hold_gross_pnl_usd': number(benchmark) if benchmark is not None else None,
                'buy_hold_costs_included': False, 'by_strategy': groups, 'kill_switch': state['kill_switch']}
