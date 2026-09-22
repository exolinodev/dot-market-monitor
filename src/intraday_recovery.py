"""Recover absent quarters from historical candles, never from current quotes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time

from common import utcnow
from cycles import utc, iso, metadata, resolve
from intraday import fetch, read_cycle, persist
from ledger_store import verify
from perp_data import chart_request, parse_chart, candle_rows

MAX_QUARTERS = 192  # Bound automatic recovery to 48 hours and 384 public calls.


def historical_quarter(boundary, fetcher=fetch, clock=utcnow):
    boundary = utc(boundary)
    started = clock()
    kind, boundary = resolve(started, boundary=iso(boundary))
    if boundary > started:
        raise ValueError('Recovery requires a closed quarter')
    start = boundary - timedelta(minutes=15)
    timer = time.monotonic()
    sources, candles = {}, {k: None for k in ('dot_1', 'dot_5', 'dot_15', 'btc_15')}
    for kind_ in ('trade', 'mark'):
        url, params = chart_request(kind_, 1, start, boundary)
        payload = fetcher(url, params)
        received = iso(clock())
        frame, more = parse_chart(payload, 1, received, start, boundary)
        rows = candle_rows(frame)
        expected = list(range(int(start.timestamp()), int(boundary.timestamp()), 60))
        if more or [r[0] for r in rows] != expected:
            raise ValueError('Historical recovery requires all 15 closed trade and mark minutes')
        sources[kind_] = {'status': 'ok', 'received_at_utc': received, 'url': url, 'params': params}
        candles[kind_] = rows
    received = iso(clock())
    for name in ('dot_1', 'dot_5', 'dot_15', 'btc_15', 'spot_book', 'spot_tape',
                 'perp_ticker', 'perp_book', 'perp_tape', 'coinbase'):
        sources[name] = {'status': 'unavailable', 'received_at_utc': received,
                         'error': 'Historical recovery contains only trade and mark candles'}
    return {'schema_version': 1, 'quarter_start_utc': iso(start),
            'meta': {**metadata(kind, boundary, started), 'generated_at_utc': received,
                     'fresh': False, 'status': 'partial', 'request_count': 2,
                     'run_duration_seconds': round(time.monotonic() - timer, 3),
                     'recovery': 'historical_candles_only'},
            'sources': sources, 'candles': candles, 'tape': {'spot': None, 'perp': None},
            'cursors': {}, 'perp_book': None, 'spot_book': None, 'perp': None,
            'coinbase': None, 'basis_bps': None, 'open_interest_delta': None,
            'realized_volatility_1m_pct': None,
            'ledger': {'status': 'unavailable', 'reason': 'Historical evidence; account advances at current boundary'}}


def recover_missing_quarters(directory, boundary, fetcher=fetch, clock=utcnow):
    """Append missing prior quarters only after the entire bounded fetch succeeds.

    Existing evidence and the ledger stay untouched. The normal runtime then
    verifies and replays all minutes, including orders and their original timing.
    """
    boundary = utc(boundary)
    current = verify(directory)
    last = current['state']['last_candle_utc']
    first = utc(last) + timedelta(minutes=1) if last else utc(current['genesis']['epoch_utc'])
    point = first.replace(minute=first.minute // 15 * 15, second=0, microsecond=0) + timedelta(minutes=15)
    if boundary - point > timedelta(minutes=15 * MAX_QUARTERS):
        raise ValueError('Market gap exceeds the 48-hour automatic recovery limit')
    missing = []
    while point < boundary:
        if read_cycle(directory, point) is None:
            missing.append(point)
        point += timedelta(minutes=15)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda point: historical_quarter(point, fetcher, clock), missing))
    for row in rows:
        persist(directory, row)
    return len(rows)
