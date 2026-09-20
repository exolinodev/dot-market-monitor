"""Fixture-backed perpetual candles and append-only funding observations."""
from decimal import Decimal
import json
from pathlib import Path
import pandas as pd
from common import dumps
from cycles import utc, iso
from timeframes import validate_candles
from kraken import FUTURES_CHARTS_BASE

FUNDING_URL = 'https://futures.kraken.com/derivatives/api/v4/historicalfundingrates'


def chart_request(kind, minutes, start, end):
    resolution = {1: '1m', 5: '5m', 15: '15m', 60: '1h'}[minutes]
    if kind not in ('trade', 'mark'):
        raise ValueError('Unknown candle kind')
    step = minutes * 60
    # Include the containing bucket and overlap one earlier bucket defensively.
    return (f'{FUTURES_CHARTS_BASE}/{kind}/PF_DOTUSD/{resolution}',
            {'from': int(utc(start).timestamp()) // step * step - step,
             'to': int(utc(end).timestamp())})


def parse_chart(payload, minutes, received, start=None, end=None):
    if not isinstance(payload.get('more_candles'), bool):
        raise ValueError('Missing chart pagination flag')
    rows = payload['candles']
    if not rows:
        raise ValueError('Empty chart response')
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.pop('time'), unit='ms', utc=True)
    for key in ('open', 'high', 'low', 'close', 'volume'):
        frame[key] = pd.to_numeric(frame[key], errors='raise')
    validate_candles(frame)
    if any(int(t.timestamp()) % (minutes * 60) for t in frame.index):
        raise ValueError('Misaligned chart timestamp')
    cutoff = min(pd.Timestamp(received), pd.Timestamp(end)) if end else pd.Timestamp(received)
    frame = frame[frame.index + pd.Timedelta(minutes=minutes) <= cutoff]
    if start:
        frame = frame[frame.index >= pd.Timestamp(start)]
    return frame, payload['more_candles']


def candle_rows(frame):
    return [[int(t.timestamp()), *[float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')]]
            for t, row in frame.iterrows()]


def candle_frame(rows):
    frame = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    frame.index = pd.to_datetime(frame.pop('time'), unit='s', utc=True)
    return frame


def parse_funding(payload):
    if payload.get('result') != 'success' or not payload.get('rates'):
        raise ValueError('Invalid funding envelope')
    previous = None
    for row in payload['rates']:
        stamp = utc(row['timestamp'])
        if stamp.minute or stamp.second or stamp.microsecond or (previous and stamp <= previous):
            raise ValueError('Unordered or misaligned funding timestamp')
        for key in ('fundingRate', 'relativeFundingRate'):
            if not Decimal(str(row[key])).is_finite():
                raise ValueError('Nonfinite funding rate')
        previous = stamp
    return payload['rates']


def append_funding(directory, payload):
    """Append new dates only. Reject changed historical rates instead of rewriting evidence."""
    root = Path(directory) / 'funding'
    rates = parse_funding(payload)
    groups = {}
    for row in rates:
        groups.setdefault(utc(row['timestamp']).strftime('%Y/%m.jsonl'), []).append(row)
    additions = []
    for name, rows in groups.items():
        path = root / name
        existing = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        by_time = {r['timestamp']: r for r in existing}
        new = []
        for row in rows:
            if row['timestamp'] in by_time:
                if row != by_time[row['timestamp']]:
                    raise ValueError('Historical funding correction requires explicit reconciliation')
            else:
                if existing and utc(row['timestamp']) <= utc(existing[-1]['timestamp']):
                    raise ValueError('Funding backfill requires explicit reconciliation')
                new.append(row)
        if new:
            additions.append((path, new))
    for path, rows in additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as handle:
            for row in rows:
                handle.write(dumps(row))
    gaps = [{'after': a['timestamp'], 'before': b['timestamp']}
            for a, b in zip(rates, rates[1:])
            if (utc(b['timestamp']) - utc(a['timestamp'])).total_seconds() != 3600]
    return {'status': 'partial' if gaps else 'ok', 'latest': rates[-1], 'gaps': gaps,
            'sign_convention': 'unverified', 'accounting_enabled': False,
            'new_records': sum(len(rows) for _, rows in additions)}


def enrich_hourly(data, directory, boundary, pending_quarter=None):
    """Full-run additions: bounded closed candle cache and incremental funding archive."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from intraday import fetch, history
    from common import utcnow
    from timeframes import CandleCache
    boundary = utc(boundary)
    cache = CandleCache(Path(directory) / 'raw/ohlc_cache.json.gz')
    requests_ = {f'{kind}.{minutes}': chart_request(kind, minutes, boundary - timedelta(hours=1), boundary)
                 for kind in ('trade', 'mark') for minutes in (1, 5, 15, 60)}
    requests_['funding'] = (FUNDING_URL, {'symbol': 'PF_DOTUSD'})
    summaries = {}

    def request(item):
        name, (url, params) = item
        try:
            return name, fetch(url, params), utcnow(), None
        except Exception as error:
            return name, None, utcnow(), str(error)

    with ThreadPoolExecutor(max_workers=9) as pool:
        for name, payload, received, error in pool.map(request, requests_.items()):
            try:
                if error:
                    raise ValueError(error)
                if name == 'funding':
                    summaries[name] = append_funding(directory, payload)
                    continue
                kind, minutes = name.split('.')
                frame, more = parse_chart(payload, int(minutes), received,
                                          boundary - timedelta(hours=1), boundary)
                # Cache columns absent from Charts stay null, never invented data.
                frame['vwap'] = float('nan')
                frame['trade_count'] = float('nan')
                if not frame.empty:
                    cache.merge('DOTPERP.' + name, frame, limit=120)
                summaries[name] = {'status': 'partial' if more or len(frame) != 60 // int(minutes) else 'ok',
                                   'closed_candles': len(frame), 'more_candles': more,
                                   'received_at_utc': iso(received)}
            except Exception as exc:
                summaries[name] = {'status': 'unavailable', 'error': str(exc)[:200]}
                data['errors'].append({'source_id': 'DOTPERP.' + name, 'error': str(exc)[:200]})
    quarters = history(directory, boundary)
    if pending_quarter is not None:
        if utc(pending_quarter['meta']['cycle_boundary_utc']) != boundary:
            raise ValueError('Pending quarter differs from full boundary')
        quarters['quarters'][-1] = pending_quarter
        quarters['missing_boundaries'] = [b for b in quarters['missing_boundaries'] if utc(b) != boundary]
        quarters['status'] = 'partial' if quarters['missing_boundaries'] else 'ok'
    for row in quarters['quarters']:
        if row:
            for kind in ('trade', 'mark'):
                rows = row['candles'].get(kind)
                if rows:
                    frame = candle_frame(rows)
                    frame['vwap'] = frame['trade_count'] = float('nan')
                    cache.merge('DOTPERP.' + kind + '.1', frame, limit=120)
    cache.save()
    # Snapshot carries the four-quarter trend, not duplicate raw candle arrays.
    quarters['quarters'] = [{k: v for k, v in row.items() if k not in ('candles', 'sources')} if row else None
                             for row in quarters['quarters']]
    data['markets']['DOTUSD']['intraday'] = quarters
    data['markets']['DOTUSD']['perp']['historical_data'] = summaries
    if any(s['status'] != 'ok' for s in summaries.values()):
        data['status'] = 'partial'
