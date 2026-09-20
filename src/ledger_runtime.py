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
    for event in inputs:
        old = seen.get(event['event_id'])
        if old is not None:
            if old != event:
                raise ValueError('Previously applied market evidence changed')
        else:
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
            'open_order': state['order'], 'position': state['position'], 'kill_switch': state['kill_switch']}
