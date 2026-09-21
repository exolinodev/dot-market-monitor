"""Bind published management and acquired account evidence to one protective action."""
from copy import deepcopy
from pathlib import Path
import json

from cycles import iso, utc
from demo_edits import effective_params
from demo_position import acquired, position_report, control_hash
from demo_preflight import policy
from demo_risk import account_risk
from exchange_actions import next_action
from exchange_brackets import requirements
from exchange_reconciliation import numeric
from kraken_execution import ExecutionError, entry_request
from ledger import digest, tick, validate_targets
from order_executor import git, load_published


def management_terms(plan, config, instructions, fills, owners, reference):
    """Apply verified publications chronologically; CLOSE/CANCEL remain latched."""
    order = plan['orders'][0]['order']
    ident = order['client_id']
    stop, targets = order['stop_usd'], deepcopy(order['targets'])
    close, cancel, used = False, False, []
    reverse = {o['exchange_order_id']: k for k, o in owners.items() if o.get('exchange_order_id')}
    for event in sorted(instructions, key=lambda e: (utc(e['at_utc']), e['event_id'])):
        if utc(event['at_utc']) > utc(reference): continue
        for action in event['plan']['management']:
            if action.get('client_id', action.get('position_id')) != ident: continue
            if event['plan']['config_sha256'] != plan['config_sha256']:
                raise ExecutionError('Management configuration differs from original entry')
            used.append({'forecast_id': event['plan']['forecast_id'], 'publication': event['publication'],
                         'effective_at_utc': event['at_utc'], 'action': deepcopy(action)})
            if 'client_id' in action:
                if action['action'] not in ('HOLD', 'CANCEL'):
                    raise ExecutionError('Unsupported published entry management')
                cancel = cancel or action['action'] == 'CANCEL'
            elif action['action'] == 'CLOSE':
                if action.get('type') != 'MARKET': raise ExecutionError('Unsupported published close')
                close = True
            elif action['action'] == 'MODIFY':
                if 'stop_usd' in action:
                    updated = tick(action['stop_usd'], config)
                    if (updated - numeric(stop)) * (1 if order['side'] == 'LONG' else -1) < 0:
                        raise ExecutionError('Published management loosens recovered protection')
                    stop = action['stop_usd']
                if 'targets' in action:
                    for fill in fills:
                        owner = owners.get(fill['cliOrdId'] or reverse.get(fill['order_id']))
                        if owner and owner['role'] != 'ENTRY' and utc(fill['fillTime']) <= utc(event['at_utc']):
                            raise ExecutionError('Target change follows actual partial exit')
                    validate_targets(action['targets'], order['side'], order['entry']['price_usd'], config)
                    targets = deepcopy(action['targets'])
            elif action['action'] != 'HOLD':
                raise ExecutionError('Unsupported published position management')
    return {'plan_sha256': digest(plan), 'stop_usd': stop, 'targets': targets,
            'close_requested': close, 'close_reason': 'management_close' if close else None}, cancel, used


def protection_preview(directory, repo, head, forecast_id, store, reference):
    """Read-only integration. Output is not a durable intent or permission to send."""
    store._require_lock()
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != head:
        raise ExecutionError('Protection requires the current fetched main head')
    settings = policy(repo, head)
    bound = load_published(directory, repo, head, forecast_id)
    plan, config, publication = bound['plan'], bound['config'], bound['instruction']
    if len(plan['orders']) != 1:
        raise ExecutionError('Protection requires the original single-entry plan')
    at = utc(reference)
    if at < utc(publication['at_utc']): raise ExecutionError('Entry publication is not yet effective')
    state = store.state
    report = position_report(store)
    if (not state.get('latest_reconciliation') or not report['quantity_reconciled']
            or store._read_artifact(state['latest_reconciliation']) != report):
        raise ExecutionError('Fresh durable position reconciliation required for protection')
    capture = acquired(store, state['latest_capture'])
    age = (at - utc(capture['readback_start_utc'])).total_seconds()
    span = (utc(capture['readback_end_utc']) - utc(capture['readback_start_utc'])).total_seconds()
    if (at < utc(capture['reference_utc']) or not 0 <= age <= settings['max_readback_age_seconds']
            or span > settings['max_readback_span_seconds']):
        raise ExecutionError('Protection readback is stale, from the future or spans too long')
    ident, plan_hash = plan['orders'][0]['order']['client_id'], digest(plan)
    owner = state['ownership'][ident]
    origin = state['operations'][owner['origin_operation_id']]
    if (origin['action']['plan_sha256'] != plan_hash or origin['action']['role'] != 'ENTRY'
            or origin.get('outcome') == 'not_dispatched'
            or effective_params(state, ident) != entry_request(plan, config)):
        raise ExecutionError('Demo entry differs from published order and quantity')
    owners = {}
    for client_id, value in state['ownership'].items():
        operation = state['operations'][value['origin_operation_id']]
        if operation.get('outcome') == 'not_dispatched': continue
        if operation['action']['plan_sha256'] == plan_hash:
            git(repo, 'merge-base', '--is-ancestor', operation['action']['trusted_head'], head)
            owners[client_id] = deepcopy(value)
        elif value['status'] != 'terminal':
            raise ExecutionError('Another active trade prevents isolated protection')
    reverse = {o['exchange_order_id']: k for k, o in owners.items() if o.get('exchange_order_id')}
    history = store._read_artifact(capture['execution_history_sha256'])
    fills = [f for f in history['fills'] if f['cliOrdId'] in owners or f['order_id'] in reverse]
    orders = [o for o in capture['open_orders'] if o.get('cliOrdId') in owners or o['order_id'] in reverse]
    instructions = []
    for path in sorted((Path(directory)/'ledger/plans').glob('*.json')):
        candidate = json.loads(path.read_bytes())
        if any(a.get('client_id', a.get('position_id')) == ident for a in candidate['management']):
            instructions.append(load_published(directory, repo, head, candidate['forecast_id'])['instruction'])
    terms, cancel, used = management_terms(plan, config, instructions, fills, owners, at)
    risk = account_risk(store, config)
    cancel = cancel or risk['kill_switch']
    status = owner['terminal_reason'] if owner['status'] == 'terminal' else owner['status']
    desired = requirements(plan, config, fills, owners, orders, report['exchange_signed_quantity'], at,
                           publication['publication']['at_utc'], status, coverage_complete=True,
                           prior_terms=terms, cancel_entry=cancel)
    action = next_action(desired, owners, orders, state['latest_capture'])
    if action:
        action.update(plan_sha256=plan_hash, trusted_head=head)
    return {'mode': 'demo', 'preview_only': True, 'authorizes_execution': False,
            'trusted_head': head, 'forecast_id': forecast_id, 'plan_sha256': plan_hash,
            'capture_sha256': state['latest_capture'], 'reconciliation_sha256': state['latest_reconciliation'],
            'control_sha256': control_hash(state), 'checked_at_utc': iso(at),
            'management_publications': used, 'account_risk': risk, 'requirements': desired, 'next_action': action,
            'demo_enabled': settings['enabled'], 'stop_market_edit_verified': False}
