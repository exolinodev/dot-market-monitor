"""Twelve bounded public calls, one immutable record per UTC quarter; no gzip writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import math
from pathlib import Path
import time
import requests
from common import dumps, write_json, utcnow
from cycles import utc, iso, metadata
from kraken import KrakenClient, SPOT_BASE, FUTURES_BASE
from perp_data import chart_request, parse_chart, candle_rows

MAX_BYTES = 25_000


def fetch(url, params):
    # Each task owns its connection; no shared requests.Session across threads.
    with requests.get(url, params=params, timeout=(2, 6), allow_redirects=False) as response:
        if 300 <= response.status_code < 400:
            raise ValueError('Unexpected public endpoint redirect')
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get('error') or payload.get('errors'):
        raise ValueError('Invalid public response')
    return payload


def book_summary(bids, asks):
    bids = sorted([(float(r[0]), float(r[1])) for r in bids], reverse=True)
    asks = sorted([(float(r[0]), float(r[1])) for r in asks])
    if not bids or not asks or not 0 < bids[0][0] < asks[0][0]:
        raise ValueError('Missing or crossed book')
    if any(not math.isfinite(v) or v < 0 for side in (bids, asks) for row in side for v in row):
        raise ValueError('Invalid book values')
    mid = (bids[0][0] + asks[0][0]) / 2
    depths = {}
    for bps in (10, 100, 200):
        buy = sum(p * q for p, q in bids if p >= mid * (1 - bps / 10000))
        sell = sum(p * q for p, q in asks if p <= mid * (1 + bps / 10000))
        depths[str(bps)] = {'bid_usd': buy, 'ask_usd': sell,
                            'imbalance': (buy - sell) / (buy + sell) if buy + sell else None}
    return {'mid': mid, 'bid': bids[0][0], 'ask': asks[0][0],
            'bid_size': bids[0][1], 'ask_size': asks[0][1],
            'spread_bps': (asks[0][0] - bids[0][0]) / mid * 10000, 'depth': depths}


def spot_rows(payload, minutes, start, end):
    result = KrakenClient._spot_result(payload)
    rows = KrakenClient._single_market_result(result)
    output = []
    for row in rows:
        stamp = int(row[0])
        if start.timestamp() <= stamp and stamp + minutes * 60 <= end.timestamp():
            values = [float(row[i]) for i in (1, 2, 3, 4, 6)]
            if not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or values[-1] < 0:
                raise ValueError('Invalid spot OHLCV')
            output.append([stamp, *values])
    return output


def tape_summary(frame, start, end, complete):
    selected = frame[(frame.time >= start) & (frame.time < end)]
    buys = float(selected.loc[selected.side == 'b', 'volume'].sum())
    sells = float(selected.loc[selected.side == 's', 'volume'].sum())
    return {'status': 'ok' if complete else 'partial', 'complete': complete,
            'start_utc': iso(start), 'end_utc': iso(end), 'trades': len(selected),
            'buy_volume': buys, 'sell_volume': sells, 'signed_volume': buys - sells,
            'large_trade_count': int(((selected.price * selected.volume) >= 10000).sum()),
            'large_trade_threshold_usd': 10000}


def archive_path(directory, boundary):
    return Path(directory) / 'intraday' / utc(boundary).strftime('%Y/%m/%d.jsonl')


def read_cycle(directory, boundary):
    path = archive_path(directory, boundary)
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['meta']['cycle_boundary_utc'] == iso(boundary):
                return row
    return None


def persist(directory, record):
    boundary = record['meta']['cycle_boundary_utc']
    previous = read_cycle(directory, boundary)
    if previous is not None:
        if previous != record:
            raise ValueError('Quarter already archived; never replace measurements')
        return
    raw = dumps(record)
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('Intraday record exceeds 25 KB')
    path = archive_path(directory, boundary)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        handle.write(raw)
    write_json(Path(directory) / 'intraday/latest.json', record)


def history(directory, boundary):
    rows = [read_cycle(directory, utc(boundary) - timedelta(minutes=15 * n)) for n in (3, 2, 1, 0)]
    return {'status': 'ok' if all(rows) else 'partial', 'quarters': rows,
            'missing_boundaries': [iso(utc(boundary) - timedelta(minutes=15 * n))
                                   for n, row in zip((3, 2, 1, 0), rows) if row is None]}


def collect(directory, kind, boundary, fetcher=fetch, clock=utcnow):
    boundary = utc(boundary)
    old = read_cycle(directory, boundary)
    if old is not None:
        return old
    started = clock()
    if started < boundary:
        raise ValueError('Collection cannot precede assigned boundary')
    timer = time.monotonic()
    start = boundary - timedelta(minutes=15)
    previous = read_cycle(directory, start)
    # Spot cursor is an explicit nanosecond boundary, avoiding loss of post-boundary
    # trades if Kraken's returned page cursor is later than our closed quarter.
    requests_ = {f'dot_{m}': (SPOT_BASE + '/OHLC', {'pair': 'DOTUSD', 'interval': m}) for m in (1, 5, 15)}
    requests_.update({
        'btc_15': (SPOT_BASE + '/OHLC', {'pair': 'XBTUSD', 'interval': 15}),
        'spot_book': (SPOT_BASE + '/Depth', {'pair': 'DOTUSD', 'count': 500}),
        'spot_tape': (SPOT_BASE + '/Trades', {'pair': 'DOTUSD', 'since': str(int(start.timestamp()) * 1_000_000_000), 'count': 1000}),
        'perp_ticker': (FUTURES_BASE + '/tickers/PF_DOTUSD', {}),
        'perp_book': (FUTURES_BASE + '/orderbook', {'symbol': 'PF_DOTUSD'}),
        'perp_tape': (FUTURES_BASE + '/history', {'symbol': 'PF_DOTUSD'}),
        'coinbase': ('https://api.exchange.coinbase.com/products/DOT-USD/book', {'level': 1}),
        'trade': chart_request('trade', 1, start, boundary),
        'mark': chart_request('mark', 1, start, boundary),
    })
    sources, values = {}, {}

    def request(item):
        key, (url, params) = item
        try:
            result = fetcher(url, params)
            return key, result, {'status': 'ok', 'received_at_utc': iso(clock())}
        except Exception as error:
            return key, None, {'status': 'unavailable', 'error': str(error)[:200], 'received_at_utc': iso(clock())}

    with ThreadPoolExecutor(max_workers=12) as pool:
        for key, payload, source in pool.map(request, requests_.items()):
            source['url'], source['params'] = requests_[key]
            sources[key] = source
            if payload is not None:
                values[key] = payload
    result = {'schema_version': 1, 'meta': {**metadata(kind, boundary, started),
              'generated_at_utc': iso(clock()), 'fresh': True, 'status': 'ok'},
              'quarter_start_utc': iso(start), 'sources': sources, 'candles': {}, 'tape': {},
              'ledger': {'status': 'unavailable', 'reason': 'Phase 2 ledger not enabled'}}

    def parse(key, function):
        if key not in values:
            return None
        try:
            return function(values[key])
        except Exception as error:
            sources[key].update(status='unavailable', error=str(error)[:200])
            return None

    for key, minutes in [('dot_1', 1), ('dot_5', 5), ('dot_15', 15), ('btc_15', 15)]:
        result['candles'][key] = parse(key, lambda p, m=minutes: spot_rows(p, m, start, boundary))
    for key in ('trade', 'mark'):
        def chart(payload, key=key):
            frame, more = parse_chart(payload, 1, sources[key]['received_at_utc'], start, boundary)
            if more or len(frame) != 15:
                sources[key]['status'] = 'partial'
                sources[key]['error'] = 'Candle coverage incomplete or pagination required'
            return candle_rows(frame)
        result['candles'][key] = parse(key, chart)
    def spot_book(payload):
        book = KrakenClient._single_market_result(KrakenClient._spot_result(payload))
        return book_summary(book['bids'], book['asks'])
    result['spot_book'] = parse('spot_book', spot_book)
    result['perp_book'] = parse('perp_book', lambda p: book_summary(p['orderBook']['bids'], p['orderBook']['asks']))
    def ticker(payload):
        item = payload['ticker']
        if payload.get('result') != 'success' or item['symbol'] != 'PF_DOTUSD':
            raise ValueError('Wrong ticker')
        if abs((utc(sources['perp_ticker']['received_at_utc']) - utc(payload['serverTime'])).total_seconds()) > 120:
            raise ValueError('Stale futures ticker')
        out = {k: float(item[k]) for k in ('markPrice', 'indexPrice', 'last', 'fundingRate', 'fundingRatePrediction', 'openInterest')}
        if not all(math.isfinite(v) for v in out.values()) or out['markPrice'] <= 0:
            raise ValueError('Invalid ticker values')
        return out
    result['perp'] = parse('perp_ticker', ticker)
    def quote(payload):
        if payload.get('auction_mode') is not False or abs((utc(sources['coinbase']['received_at_utc']) - utc(payload['time'])).total_seconds()) > 120:
            raise ValueError('Stale or auction quote')
        value = book_summary(payload['bids'], payload['asks'])
        value['product_status'] = 'not_checked_in_light_run'
        return value
    result['coinbase'] = parse('coinbase', quote)
    def spot_tape(payload):
        frame = KrakenClient.parse_spot_trades(payload)
        complete = len(frame) < 1000 or (not frame.empty and frame.time.max() >= boundary)
        return tape_summary(frame, start, boundary, complete)
    def perp_tape(payload):
        frame = KrakenClient.parse_futures_trades(payload)
        complete = not frame.empty and frame.time.min() <= start
        return tape_summary(frame, start, boundary, complete)
    result['tape']['spot'] = parse('spot_tape', spot_tape)
    result['tape']['perp'] = parse('perp_tape', perp_tape)
    result['cursors'] = {'spot_since_ns': str(int(boundary.timestamp()) * 1_000_000_000),
                         'perp_coverage_boundary_utc': iso(boundary)}
    for key in ('spot', 'perp'):
        tape = result['tape'][key]
        if tape:
            prev = ((previous or {}).get('tape') or {}).get(key) or {}
            # CVD resets when a quarter is absent/incomplete; never bridge a gap.
            tape['cvd_segment'] = ((prev.get('cvd_segment', 0) if prev.get('complete') else 0) + tape['signed_volume']) if tape['complete'] else None
            if not tape['complete']:
                sources[key + '_tape']['status'] = 'partial'
    perp = result['perp'] or {}
    prior = (previous or {}).get('perp') or {}
    result['open_interest_delta'] = perp['openInterest'] - prior['openInterest'] if 'openInterest' in perp and 'openInterest' in prior else None
    spot = result['spot_book'] or {}
    result['basis_bps'] = (perp['markPrice'] / spot['mid'] - 1) * 10000 if perp and spot else None
    closes = [r[4] for r in result['candles'].get('dot_1') or []]
    returns = [math.log(b/a) for a, b in zip(closes, closes[1:])]
    result['realized_volatility_1m_pct'] = math.sqrt(sum(x*x for x in returns)) * 100 if len(closes) == 15 else None
    result['meta'].update(status='partial' if any(v['status'] != 'ok' for v in sources.values()) else 'ok',
                          request_count=len(requests_), run_duration_seconds=round(time.monotonic()-timer, 3))
    result['meta']['fresh'] = bool(perp and (clock() - started).total_seconds() < 120)
    if not result['meta']['fresh'] or not result['perp_book'] or any(
            len(result['candles'].get(k) or []) != 15 or sources[k]['status'] != 'ok' for k in ('trade', 'mark')):
        raise ValueError('Required perp quote or closed candle coverage missing; quarter not committed')
    persist(directory, result)
    return result
