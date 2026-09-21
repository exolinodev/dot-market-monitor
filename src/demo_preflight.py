"""Published-entry and current-demo-state checks; not authority to send orders."""
from copy import deepcopy
from datetime import timedelta
from decimal import localcontext
import json
from pathlib import Path

from cycles import utc, iso
from demo_position import acquired, control_hash, position_report
from exchange_reconciliation import numeric, number
from kraken_execution import ExecutionError, entry_request
from ledger import digest, size_order
from order_executor import git, load_published


def policy(repo, head):
    path = 'config/demo_executor.json'
    if not git(repo, 'ls-tree', head, '--', path).startswith(b'100644 blob '):
        raise ExecutionError('Committed regular demo executor policy required')
    value = json.loads(git(repo, 'show', head + ':' + path))
    if (set(value) != {'version', 'environment', 'enabled', 'max_readback_age_seconds', 'max_readback_span_seconds'}
            or value['version'] != 1 or value['environment'] != 'demo' or type(value['enabled']) is not bool):
        raise ExecutionError('Invalid committed demo executor policy')
    for key in ('max_readback_age_seconds', 'max_readback_span_seconds'):
        if type(value[key]) is not int or value[key] <= 0:
            raise ExecutionError('Positive demo timing limits required')
    return value


def flex_equity(capture):
    """Kraken flex wallet: portfolioValue excludes separately reported funding."""
    wallet = capture['accounts']['flex']
    if wallet.get('type') != 'multiCollateralMarginAccount':
        raise ExecutionError('Verified multi-collateral demo wallet required')
    with localcontext() as context:
        context.prec = 34
        equity = numeric(wallet['portfolioValue']) + numeric(wallet['unrealizedFunding'])
        margin_equity = numeric(wallet['marginEquity'])
        available = numeric(wallet['availableMargin'])
        if equity <= 0 or margin_equity <= 0 or available <= 0:
            raise ExecutionError('Demo wallet has no positive usable capital')
        return min(equity, margin_equity), available


def entry_preflight(directory, repo, head, forecast_id, store, reference):
    """Recompute against a held journal lock and exact locally fetched main head.

    Checks only an unsent entry into a flat demo account. Existing positions need
    the protective-management path. Call again immediately before any future
    dispatch; this returned object is a preview, not a reusable authorization.
    """
    store._require_lock()
    directory = Path(directory)
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != head:
        raise ExecutionError('Entry preflight requires the current fetched main head')
    settings = policy(repo, head)
    bound = load_published(directory, repo, head, forecast_id)
    plan, config, event = bound['plan'], bound['config'], bound['instruction']
    if len(plan['orders']) != 1:
        raise ExecutionError('Entry preflight requires exactly one published order')
    current = store.state
    ident = current.get('latest_reconciliation')
    if not ident:
        raise ExecutionError('Current durable position reconciliation required')
    report = store._read_artifact(ident)
    if report != position_report(store) or not report['quantity_reconciled']:
        raise ExecutionError('Position reconciliation is stale or has discrepancies')
    capture = acquired(store, current['latest_capture'])
    baseline = acquired(store, current['baseline_capture'])
    at = utc(reference)
    reasons = []
    age = (at - utc(capture['readback_start_utc'])).total_seconds()
    span = (utc(capture['readback_end_utc']) - utc(capture['readback_start_utc'])).total_seconds()
    if at < utc(capture['reference_utc']) or age < 0:
        reasons.append('readback_from_future')
    if age > settings['max_readback_age_seconds']:
        reasons.append('readback_stale')
    if span > settings['max_readback_span_seconds']:
        reasons.append('readback_span_exceeded')
    if at < utc(event['at_utc']):
        reasons.append('entry_not_yet_effective')
    order = plan['orders'][0]['order']
    deadline = min(utc(order['valid_until_utc']), utc(event['publication']['at_utc']) + timedelta(minutes=config['max_order_age_minutes']))
    if at >= deadline:
        reasons.append('entry_expired')
    for path in (directory/'ledger/plans').glob('*.json'):
        other = json.loads(path.read_bytes())
        if other['forecast_id'] != forecast_id and utc(other['created_at_utc']) >= utc(plan['created_at_utc']):
            reasons.append('newer_or_ambiguous_published_plan')
            break
    if order['client_id'] in current['ownership']:
        reasons.append('entry_identity_already_used')
    if capture['open_orders'] or any(numeric(p['size']) != 0 for p in capture['positions']):
        reasons.append('demo_account_not_flat')
    quote = bound['snapshot']['markets']['DOTUSD']['execution_context']['quote']
    quote_age = (at - utc(quote['asof_utc'])).total_seconds()
    if not 0 <= quote_age <= config['execution_quote_max_age_seconds']:
        reasons.append('published_quote_stale_or_future')
    capital, available = flex_equity(capture)
    initial_capital, _ = flex_equity(baseline)
    with localcontext() as context:
        context.prec = 34
        if capital < initial_capital * numeric(config['equity_floor_fraction']):
            reasons.append('demo_equity_floor_breached')
        budget_equity = min(capital, numeric(bound['state']['equity_usd']))
    # A flat numerical sizing state evaluates the immutable quantity against
    # current paper equity and actual usable demo capital. It never resizes it.
    sizing_state = deepcopy(bound['state'])
    sizing_state.update(order=None, position=None, equity_usd=number(budget_equity))
    ceiling = None
    try:
        ceiling = size_order(order, sizing_state, config, quote)
        if numeric(plan['orders'][0]['size']['quantity']) > numeric(ceiling['quantity']):
            reasons.append('published_quantity_exceeds_current_risk_budget')
    except ValueError:
        reasons.append('current_risk_or_reward_check_failed')
    return {'mode': 'demo', 'preview_only': True, 'trusted_head': head, 'forecast_id': forecast_id,
            'plan_sha256': digest(plan), 'policy_sha256': digest(settings),
            'capture_sha256': current['latest_capture'], 'reconciliation_sha256': ident,
            'control_sha256': control_hash(current), 'checked_at_utc': iso(at), 'entry_deadline_utc': iso(deadline),
            'entry_checks_passed': not reasons, 'reasons': reasons, 'demo_enabled': settings['enabled'],
            'entry_request': entry_request(plan, config), 'budget_equity_usd': number(budget_equity),
            'available_margin_usd': number(available), 'quantity_ceiling': None if ceiling is None else ceiling['quantity'],
            'account_flows_verified': False, 'live_quote_verified': False, 'exchange_margin_requirement_verified': False,
            'authorizes_execution': False}
