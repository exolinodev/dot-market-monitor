"""Deterministic protective-order requirements from reconciled actual fills.

This planner neither sends requests nor invents exchange order identities. The
executor must reconcile durable attempts before realizing these desired roles.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR, localcontext

from cycles import utc, iso
from exchange_reconciliation import deduplicate_fills, numeric, number
from kraken_execution import ExecutionError
from ledger import digest, tick, validate_targets

ROLES = ('ENTRY', 'STOP', 'T1', 'T2', 'T3', 'CLOSE')
TERMINAL = ('filled', 'cancelled', 'rejected')


def requirements(plan, config, fills, owned_orders, open_orders, signed_position,
                 reference, published_at, entry_status, *, management=None,
                 cancel_entry=False, coverage_complete=False, prior_terms=None):
    """Compute desired reduce-only roles and owned orders requiring cancellation.

    Inputs must cover the whole trade from a flat baseline in one verified demo
    account. owned_orders maps cliOrdId to {role, exchange_order_id?}; callers
    derive this map from persisted, verified intents, never exchange name guesses.
    Multiple historical client IDs may belong to one role (edited/replaced exits).
    Every input readback must be fresh and from the same reconciliation cycle.
    """
    with localcontext() as context:
        context.prec = 34
        if not coverage_complete:
            raise ExecutionError('Complete trade-lifetime fill coverage required')
        if plan['config_sha256'] != digest(config) or config['instrument'] != 'PF_DOTUSD':
            raise ExecutionError('Bracket configuration differs from validated plan')
        if len(plan['orders']) != 1:
            raise ExecutionError('Bracket planner needs the original entry plan')
        if entry_status not in ('open', 'pending', 'unknown', *TERMINAL):
            raise ExecutionError('Invalid entry lifecycle status')
        if entry_status == 'unknown':
            raise ExecutionError('Resolve unknown entry attempt before bracket planning')
        at, publication = utc(reference), utc(published_at)
        if at < publication or publication < utc(plan['created_at_utc']):
            raise ExecutionError('Invalid publication/reference chronology')
        item = plan['orders'][0]
        order = item['order']
        original_id = order['client_id']
        if set(i['role'] for i in owned_orders.values()) - set(ROLES):
            raise ExecutionError('Unknown owned order role')
        entry_ids = [ident for ident, value in owned_orders.items() if value['role'] == 'ENTRY']
        if entry_ids != [original_id]:
            raise ExecutionError('Trade must have exactly its original entry identity')
        exchange_ids = {}
        for ident, value in owned_orders.items():
            if value.get('exchange_order_id'):
                exchange_id = value['exchange_order_id']
                if exchange_id in exchange_ids:
                    raise ExecutionError('Exchange identity assigned twice')
                exchange_ids[exchange_id] = ident
        side = 'buy' if order['side'] == 'LONG' else 'sell'
        if order['side'] not in ('LONG', 'SHORT'):
            raise ExecutionError('Invalid trade side')
        exit_side = 'sell' if side == 'buy' else 'buy'
        quantities = {role: Decimal(0) for role in ROLES}
        first_fill = None
        for fill in deduplicate_fills(fills):
            if fill['symbol'] != config['instrument']:
                continue
            ident = fill['cliOrdId'] or exchange_ids.get(fill['order_id'])
            owner = owned_orders.get(ident)
            if owner is None or (owner.get('exchange_order_id') and owner['exchange_order_id'] != fill['order_id']):
                raise ExecutionError('Unowned or mismatched trade fill')
            role = owner['role']
            if fill['side'] != (side if role == 'ENTRY' else exit_side):
                raise ExecutionError('Fill direction does not match trade role')
            stamp = utc(fill['fillTime'])
            if stamp < publication or stamp > at:
                raise ExecutionError('Fill outside published trade lifetime')
            quantities[role] += numeric(fill['size'], positive=True)
            if role == 'ENTRY':
                first_fill = min(first_fill, stamp) if first_fill else stamp
        entered = quantities['ENTRY']
        exited = sum(quantities[r] for r in ROLES if r != 'ENTRY')
        remaining = entered - exited
        expected = remaining if side == 'buy' else -remaining
        if entered > numeric(item['size']['quantity']) or remaining < 0:
            raise ExecutionError('Trade fill quantities exceed authorized exposure')
        if numeric(signed_position) != expected:
            raise ExecutionError('Exchange position differs from owned trade fills')
        step = numeric(config['quantity_step'], positive=True)
        if any(q % step for q in quantities.values()):
            raise ExecutionError('Fill quantity is off configured step')
        if entry_status == 'filled' and entered != numeric(item['size']['quantity']):
            raise ExecutionError('Filled entry status differs from actual quantity')
        if entry_status == 'pending' and entered:
            raise ExecutionError('Pending entry already has fills')
        if entry_status == 'rejected' and entered:
            raise ExecutionError('Rejected entry has fills; reconcile its lifecycle')
        live = {}
        for current in open_orders:
            if current['symbol'] != config['instrument']:
                continue
            ident = current.get('cliOrdId')
            owner = owned_orders.get(ident)
            if owner is None or ident in live:
                raise ExecutionError('Unowned or duplicate resting order')
            if owner.get('exchange_order_id') and owner['exchange_order_id'] != current['order_id']:
                raise ExecutionError('Resting exchange identity differs from owned intent')
            if current['side'] != (side if owner['role'] == 'ENTRY' else exit_side):
                raise ExecutionError('Resting order has wrong side')
            if owner['role'] != 'ENTRY' and current.get('reduceOnly') is not True:
                raise ExecutionError('Protective order is not reduce-only')
            live[ident] = current
        entry_open = original_id in live
        if (entry_status == 'open') != entry_open:
            raise ExecutionError('Entry lifecycle differs from open order readback')
        if any(owner['role'] == 'ENTRY' for ident, owner in owned_orders.items() if ident in live) and entry_status in TERMINAL:
            raise ExecutionError('Terminal entry is still open')
        action = deepcopy(management or {'action': 'HOLD'})
        if action['action'] not in ('HOLD', 'CLOSE', 'MODIFY'):
            raise ExecutionError('Unsupported position management')
        if action['action'] == 'CLOSE' and action.get('type') != 'MARKET':
            raise ExecutionError('Only market close supported')
        if 'position_id' in action and action['position_id'] != original_id:
            raise ExecutionError('Management refers to another position')
        stop = tick(order['stop_usd'], config)
        targets = deepcopy(order['targets'])
        close_latched = False
        if prior_terms is not None:
            if prior_terms['plan_sha256'] != digest(plan) or type(prior_terms['close_requested']) is not bool:
                raise ExecutionError('Persisted bracket terms differ from original plan')
            previous = tick(prior_terms['stop_usd'], config)
            if (previous - stop) * (1 if side == 'buy' else -1) < 0:
                raise ExecutionError('Persisted stop increases original risk')
            stop = previous
            validate_targets(prior_terms['targets'], order['side'], order['entry']['price_usd'], config)
            targets = deepcopy(prior_terms['targets'])
            close_latched = prior_terms['close_requested']
            if close_latched and prior_terms.get('close_reason') not in ('management_close', 'finish_partial_close', 'max_hold'):
                raise ExecutionError('Persisted close reason is invalid')
        if action['action'] == 'MODIFY':
            if not any(key in action for key in ('stop_usd', 'targets')):
                raise ExecutionError('MODIFY requires numerical changes')
            if 'stop_usd' in action:
                updated = tick(action['stop_usd'], config)
                if (updated - stop) * (1 if side == 'buy' else -1) < 0:
                    raise ExecutionError('Stop modification increases original risk')
                stop = updated
            if 'targets' in action:
                if exited:
                    raise ExecutionError('Target modification after partial exit is unsupported')
                validate_targets(action['targets'], order['side'], order['entry']['price_usd'], config)
                targets = deepcopy(action['targets'])
        deadline = min(utc(order['valid_until_utc']), publication + timedelta(minutes=config['max_order_age_minutes']))
        close_reason = None
        if close_latched: close_reason = prior_terms['close_reason']
        elif action['action'] == 'CLOSE': close_reason = 'management_close'
        elif quantities['STOP'] or quantities['CLOSE']: close_reason = 'finish_partial_close'
        elif first_fill and at >= first_fill + timedelta(hours=config['max_hold_hours']): close_reason = 'max_hold'
        cancel_pending = cancel_entry or at >= deadline or exited > 0 or close_reason is not None
        cancel_ids = {original_id} if entry_open and cancel_pending else set()
        allocations = {}
        for target in targets[:2]:
            allocations[target['id']] = (entered * numeric(target['fraction']) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        allocations['T3'] = entered - allocations['T1'] - allocations['T2']
        if any(quantities[role] > allocations[role] for role in ('T1', 'T2', 'T3')):
            raise ExecutionError('Target executions exceed cumulative allocation')
        # A completed T1 tightens the stop only after the entry is terminal;
        # otherwise further entry fills could change its allocation.
        if (entry_status in TERMINAL and allocations['T1'] > 0 and quantities['T1'] >= allocations['T1']
                and order.get('stop_after_t1_usd') is not None):
            trail = tick(order['stop_after_t1_usd'], config)
            stop = max(stop, trail) if side == 'buy' else min(stop, trail)
        # Existing tighter protection may not be silently loosened on a rerun.
        for ident, current in live.items():
            if owned_orders[ident]['role'] == 'STOP':
                previous = tick(current['stopPrice'], config)
                stop = max(stop, previous) if side == 'buy' else min(stop, previous)
        desired = []
        if remaining:
            common = {'symbol': config['instrument'], 'side': exit_side, 'reduceOnly': True}
            desired.append({'role': 'STOP', 'params': {**common, 'orderType': 'stp',
                            'size': number(remaining), 'stopPrice': number(stop), 'triggerSignal': 'mark'}})
            if close_reason:
                desired.append({'role': 'CLOSE', 'params': {**common, 'orderType': 'mkt', 'size': number(remaining)}})
            else:
                for target in targets:
                    amount = allocations[target['id']] - quantities[target['id']]
                    if amount:
                        desired.append({'role': target['id'], 'params': {**common, 'orderType': 'lmt',
                                        'size': number(amount), 'limitPrice': target['price_usd']}})
        wanted_roles = {item['role'] for item in desired}
        for ident in live:
            role = owned_orders[ident]['role']
            if role != 'ENTRY' and role not in wanted_roles:
                cancel_ids.add(ident)
        return {'version': 1, 'entry_client_id': original_id, 'asof_utc': iso(at),
                'entered_quantity': number(entered), 'remaining_quantity': number(remaining),
                'entry_deadline_utc': iso(deadline), 'entry_cancel_required': bool(cancel_pending and entry_status not in TERMINAL),
                'cancel_client_ids': sorted(cancel_ids), 'close_reason': close_reason,
                'desired_orders': desired,
                'effective_terms': {'plan_sha256': digest(plan), 'stop_usd': number(stop),
                                    'targets': targets, 'close_requested': close_reason is not None, 'close_reason': close_reason},
                'authorizes_execution': False}
