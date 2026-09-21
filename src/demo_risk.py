"""Derive a latched entry kill switch from acquired, append-only demo evidence."""
from decimal import localcontext

from demo_position import acquired
from exchange_reconciliation import numeric, number
from kraken_execution import ExecutionError
from ledger import digest


def usable_capital(capture):
    """Finite conservative capital, including zero/negative distressed wallets.

    Available margin is deliberately not a condition for cancelling entries or
    protecting an existing position. This is not an exchange margin formula.
    """
    wallet = capture['accounts']['flex']
    if wallet.get('type') != 'multiCollateralMarginAccount':
        raise ExecutionError('Verified multi-collateral demo wallet required')
    with localcontext() as context:
        context.prec = 34
        equity = numeric(wallet['portfolioValue']) + numeric(wallet['unrealizedFunding'])
        return min(equity, numeric(wallet['marginEquity']))


def account_risk(store, config):
    """Replay all acquired observations since the fixed baseline, without writes.

    A later balance recovery cannot erase a breach. Missing/corrupted acquired
    evidence raises rather than returning a healthy account. No resetting or
    adjustment for external flows is inferred from balances.
    """
    store._require_lock()
    state = store.state
    baseline = state.get('baseline_capture')
    if baseline is None:
        raise ExecutionError('Account risk requires an acquired flat baseline')
    capital = usable_capital(acquired(store, baseline))
    fraction = numeric(config['equity_floor_fraction'], positive=True)
    if capital <= 0 or fraction > 1:
        raise ExecutionError('Positive baseline and equity floor at most one required')
    with localcontext() as context:
        context.prec = 34
        floor = capital * fraction
    refs, after_baseline = [], False
    for event in store._events:
        if event['type'] == 'baseline':
            if event['payload']['capture_sha256'] != baseline:
                raise ExecutionError('Account risk baseline differs from journal')
            refs.append(baseline)
            after_baseline = True
        elif after_baseline and event['type'] == 'capture':
            refs.append(event['payload']['artifact_sha256'])
    if not refs or refs[-1] != state['latest_capture']:
        raise ExecutionError('Account risk requires the complete capture sequence')
    breach, minimum, current = None, capital, capital
    for ref in refs:
        capture = acquired(store, ref)
        current = usable_capital(capture)
        minimum = min(minimum, current)
        if current < floor and breach is None:
            breach = {'capture_sha256': ref, 'reference_utc': capture['reference_utc'],
                      'usable_capital_usd': number(current)}
    return {'config_sha256': digest(config), 'baseline_capture_sha256': baseline,
            'latest_capture_sha256': refs[-1], 'observation_count': len(refs),
            'equity_floor_usd': number(floor), 'minimum_observed_capital_usd': number(minimum),
            'current_usable_capital_usd': number(current), 'kill_switch': breach is not None,
            'first_breach': breach}
