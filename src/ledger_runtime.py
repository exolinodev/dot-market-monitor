"""Advance an explicitly initialized paper account using archived evidence only."""
from datetime import timedelta
import gzip
import json
from pathlib import Path

from cycles import utc, iso
from ledger import digest, PRIORITY
from ledger_market import market_events
from ledger_publication import instruction
from ledger_store import verify, append_events
from oracle_v4 import verify_forecast_plan


def advance(directory, repo, trusted_head, boundary, pending_rows=()):
    root = Path(directory)
    current = verify(root)  # Never initialize implicitly or repair changed evidence.
    epoch = current['genesis']['epoch_utc']
    cutoff = utc(boundary) - timedelta(minutes=1)
    inputs = market_events(root, epoch, boundary, pending_rows)
    seen = {e['event_id']: e for e in current['inputs']}
    for path in sorted((root / 'ledger/plans').glob('*.json')):
        plan = json.loads(path.read_text())
        if 'plan-' + plan['forecast_id'] in seen:
            continue
        forecast_path = root / 'oracle/forecasts' / utc(plan['created_at_utc']).strftime('%Y/%m/%d') / (plan['forecast_id'] + '.json')
        forecast = json.loads(forecast_path.read_text())
        snapshot = json.loads(gzip.decompress((root / 'oracle/inputs' / (forecast['snapshot_sha256'] + '.json.gz')).read_bytes()))
        if verify_forecast_plan(forecast, snapshot, root) != plan:
            raise ValueError('Runtime plan differs from published forecast')
        event = instruction(repo, trusted_head, plan)
        if utc(event['at_utc']) <= cutoff:
            inputs.append(event)
    additions = []
    late_funding = []
    previous = current['state']['last_event_key']
    watermark = (utc(previous[0]), previous[1], previous[2]) if previous else None
    for event in inputs:
        old = seen.get(event['event_id'])
        if old is not None:
            if old != event:
                raise ValueError('Previously applied market evidence changed')
        else:
            key = (utc(event['at_utc']), PRIORITY[event['type']], event['event_id'])
            if event['type'] == 'funding_rate' and watermark and key < watermark:
                # Historical rates may arrive after light runs have closed the
                # interval. Keep their source archive, but never insert them into
                # a committed journal or pretend they were known earlier. The
                # existing missing-funding markers remain provisional. Other
                # out-of-order input types must still fail in append_events.
                late_funding.append(event['interval_start_utc'])
                continue
            additions.append(event)
    additions.sort(key=lambda e: (utc(e['at_utc']), PRIORITY[e['type']], e['event_id']))
    # No current account claim if the trailing market minute itself is absent.
    last_candle = max([utc(e['at_utc']) for e in inputs if e['type'] == 'candle'] +
                      ([utc(current['state']['last_candle_utc'])] if current['state']['last_candle_utc'] else []), default=None)
    if cutoff >= utc(epoch) and last_candle != cutoff:
        raise ValueError('Ledger cannot advance without the final closed market minute')
    start = utc(current['state']['last_candle_utc']) + timedelta(minutes=1) if current['state']['last_candle_utc'] else utc(epoch).replace(second=0, microsecond=0)
    if start < utc(epoch):
        start += timedelta(minutes=1)
    stamps = {utc(e['at_utc']) for e in inputs if e['type'] == 'candle'}
    while start <= cutoff:
        if start not in stamps:
            raise ValueError('Ledger requires continuous closed market evidence')
        start += timedelta(minutes=1)
    state = append_events(root, additions) if additions else current['state']
    return {'status': 'ok', 'asof_boundary_utc': iso(boundary), 'ledger_state_sha256': digest(state),
            'equity_usd': state['equity_usd'], 'unrealized_pnl_usd': state['unrealized_pnl_usd'],
            'open_order': state['order'], 'position': state['position'], 'kill_switch': state['kill_switch'],
            'late_funding': {'count': len(late_funding), 'latest_intervals_utc': sorted(late_funding)[-24:],
                             'policy': 'archived_not_retroactively_applied'}}


def finish_quarter(directory, quarter, boundary, repo, trusted_head):
    """Prepare account and quarter together for one protected producer commit."""
    from intraday import read_cycle, persist
    previous = read_cycle(directory, boundary)
    name = 'intraday/' + utc(boundary).strftime('%Y/%m/%d.jsonl')
    summary = advance(directory, repo, trusted_head, boundary, [(name, quarter)])
    if summary['late_funding']['count']:
        print(f"Ledger funding: {summary['late_funding']['count']} late archived rates not retroactively applied")
    if previous is None:
        quarter['ledger'] = summary
        persist(directory, quarter)
    # A committed quarter is immutable even if a later writer added a plan.
    return summary


def execution_context(directory, quarter, boundary, reference):
    """Facts for order selection; the model never sets the executable quantity."""
    from decimal import localcontext
    from ledger import decimal, number
    result = verify(directory)
    state, cfg = result['state'], result['genesis']['config']
    expected = utc(boundary) - timedelta(minutes=1)
    if not state['last_candle_utc'] or utc(state['last_candle_utc']) != expected:
        return {'status': 'unavailable', 'reason': 'Account has no complete boundary mark', 'instrument': cfg['instrument']}
    source, book = quarter['sources']['perp_book'], quarter['perp_book']
    if source['status'] != 'ok' or not utc(boundary) <= utc(source['received_at_utc']) <= utc(reference):
        return {'status': 'unavailable', 'reason': 'Current boundary quote unavailable', 'instrument': cfg['instrument']}
    if (utc(reference) - utc(source['received_at_utc'])).total_seconds() > cfg['execution_quote_max_age_seconds']:
        return {'status': 'unavailable', 'reason': 'Execution quote exceeds configured age', 'instrument': cfg['instrument']}
    with localcontext() as context:
        context.prec = 34
        spread = max(decimal(cfg['spread_floor_bps']), decimal(book['spread_bps']))
        costs = 2 * decimal(cfg['fee_taker_pct']) * 100 + spread
    recent = sorted(result['trades'].values(), key=lambda t: (t['closed_at_utc'], t['trade_id']))[-5:]
    recent = [{key: trade[key] for key in ('trade_id', 'forecast_id', 'strategy_version',
               'closed_at_utc', 'status', 'net_pnl_usd', 'net_r', 'exit_reason',
               'duration_seconds', 'funding_usd', 'fees_usd', 'spread_cost_usd')} for trade in recent]
    return {'status': 'ok', 'instrument': cfg['instrument'], 'ledger_state_sha256': digest(state),
            'ledger_config_sha256': digest(cfg), 'asof_boundary_utc': iso(boundary),
            'quote': {'status': 'ok', 'bid': number(book['bid']), 'ask': number(book['ask']),
                      'asof_utc': iso(source['received_at_utc'])},
            'spread_bps': number(spread), 'estimated_taker_round_trip_bps': number(costs),
            'cost_estimate_excludes_funding': True, 'slippage_bps': cfg['slippage_bps'],
            'funding_rate_prediction': number(quarter['perp']['fundingRatePrediction']) if quarter.get('perp', {}).get('fundingRatePrediction') is not None else None,
            'funding_convention_verified': cfg['funding_convention_verified'],
            'late_funding': quarter.get('ledger', {}).get('late_funding'),
            'risk_policy': {k: cfg[k] for k in ('risk_fraction_per_trade', 'risk_tiers',
                'max_notional_multiple_of_equity', 'tick_size_usd', 'execution_quote_max_age_seconds', 'min_stop_bps', 'max_stop_bps',
                'min_net_reward_risk_t1', 'max_hold_hours', 'max_order_age_minutes')},
            'recent_closed_trades': recent, 'ledger_state': state, 'performance': {k: v for k, v in result['performance'].items()
                                                     if k != 'equity_curve'}}
