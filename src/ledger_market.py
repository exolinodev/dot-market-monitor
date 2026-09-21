"""Deterministic ledger inputs from retained quarter/funding evidence.

Quotes are timed at source reception, not at the preceding candle boundary.
Only events through the last closed minute's OPEN enter a replay batch; newer
quotes remain in the market archive until a later batch can order them safely.
"""
from datetime import timedelta, timezone, datetime
import json
from pathlib import Path
import re

from cycles import utc, iso
from ledger import digest, number, PRIORITY


SOURCE_PATH = r'(intraday/[0-9]{4}/[0-9]{2}/[0-9]{2}|funding/[0-9]{4}/[0-9]{2})\.jsonl'


def evidence_hash(row, kind):
    # A later account summary must not create a circular market-input/state hash.
    return digest({k: v for k, v in row.items() if k != 'ledger'} if kind == 'intraday' else row)


def row_events(path, row, epoch):
    if not re.fullmatch(SOURCE_PATH, path):
        raise ValueError('Invalid ledger market evidence path')
    epoch = utc(epoch)
    kind = path.split('/')[0]
    source = {'path': path, 'row_sha256': evidence_hash(row, kind)}
    if kind == 'funding':
        interval = utc(row['timestamp'])
        if interval + timedelta(hours=1) <= epoch:
            return []
        return [{'type': 'funding_rate', 'event_id': 'funding-' + iso(interval),
                 'at_utc': iso(max(interval, epoch)), 'interval_start_utc': iso(interval),
                 'absolute_rate': number(row['fundingRate']),
                 'relative_rate': number(row['relativeFundingRate']), 'source': source}]
    events = []
    quote = row['sources']['perp_book']
    if quote['status'] == 'ok':
        at = utc(quote['received_at_utc'])
        if at >= epoch:
            events.append({'type': 'spread', 'event_id': 'spread-' + iso(at),
                           'at_utc': iso(at), 'spread_bps': number(row['perp_book']['spread_bps']), 'source': source})
    trade, mark = row['candles']['trade'], row['candles']['mark']
    if len({r[0] for r in trade}) != len(trade) or len({r[0] for r in mark}) != len(mark):
        raise ValueError('Duplicate ledger candle evidence')
    marks = {r[0]: r for r in mark}
    if {r[0] for r in trade} != set(marks):
        raise ValueError('Trade/mark candle evidence is not aligned')
    for bar in trade:
        at = datetime.fromtimestamp(bar[0], timezone.utc)
        if at < epoch:
            continue
        if not utc(row['quarter_start_utc']) <= at < utc(row['meta']['cycle_boundary_utc']):
            raise ValueError('Candle outside immutable quarter')
        events.append({'type': 'candle', 'event_id': 'candle-' + iso(at), 'at_utc': iso(at),
            'trade': {k: number(v) for k, v in zip(('open', 'high', 'low', 'close'), bar[1:5])},
            'mark': {k: number(v) for k, v in zip(('open', 'high', 'low', 'close'), marks[bar[0]][1:5])}, 'source': source})
    return events


def market_events(directory, epoch, boundary, pending_rows=()):
    root = Path(directory)
    boundary = utc(boundary)
    if boundary.second or boundary.microsecond:
        raise ValueError('Market watermark must align to a minute')
    cutoff = boundary - timedelta(minutes=1)
    events = {}
    evidence = list(pending_rows)
    for pattern in ('intraday/*/*/*.jsonl', 'funding/*/*.jsonl'):
        for path in sorted(root.glob(pattern)):
            if path.is_symlink():
                raise ValueError('Market evidence must be regular files')
            evidence.extend((path.relative_to(root).as_posix(), json.loads(line)) for line in path.read_text().splitlines())
    for name, row in evidence:
        for event in row_events(name, row, epoch):
            if utc(event['at_utc']) > cutoff:
                continue
            ident = event['event_id']
            if ident in events and events[ident] != event:
                raise ValueError('Conflicting ledger market evidence')
            events[ident] = event
    return sorted(events.values(), key=lambda e: (utc(e['at_utc']), PRIORITY[e['type']], e['event_id']))


def verify_market_input(directory, epoch, event, cache=None):
    source = event.get('source') or {}
    path = source.get('path', '')
    if not re.fullmatch(SOURCE_PATH, path):
        raise ValueError('Ledger input needs immutable market evidence')
    kind = path.split('/')[0]
    cache = {} if cache is None else cache
    if path not in cache:
        rows = [json.loads(line) for line in (Path(directory) / path).read_text().splitlines()]
        indexed = {}
        for row in rows:
            key = evidence_hash(row, kind)
            if key in indexed:
                raise ValueError('Duplicate market evidence row')
            indexed[key] = row
        cache[path] = indexed
    row = cache[path].get(source.get('row_sha256'))
    if row is None or event not in row_events(path, row, epoch):
        raise ValueError('Ledger input differs from archived market evidence')
