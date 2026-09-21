"""Evidence-preserving demo fill capture and deterministic quantity reconciliation.

These helpers do not authorize sends or infer actual fees/funding from prices.
Timestamp-only pagination can be ambiguous; such gaps are never called complete.
"""
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re

from cycles import utc, iso
from kraken_execution import ExecutionError, _create, _json_bytes, _sync_directory


CORE = ('fill_id', 'order_id', 'fillTime', 'symbol', 'side', 'size', 'price', 'fillType')


def exchange_time(value):
    # Never silently round the exclusive pagination cursor to microseconds.
    extra = re.search(r'\.\d{6}(\d+)', value) if isinstance(value, str) else None
    if extra and any(d != '0' for d in extra.group(1)):
        raise ExecutionError('Exchange timestamp exceeds supported precision')
    return utc(value)


def numeric(value, *, positive=False):
    if isinstance(value, bool):
        raise ExecutionError('Boolean is not an exchange quantity')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ExecutionError('Invalid exchange number') from None
    if not result.is_finite() or (positive and result <= 0):
        raise ExecutionError('Invalid exchange quantity or price')
    return result


def number(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def normalized_fill(row):
    if not isinstance(row, dict) or any(k not in row for k in CORE):
        raise ExecutionError('Incomplete fill evidence')
    out = {k: row[k] for k in CORE}
    for name in ('fill_id', 'order_id', 'symbol', 'fillType'):
        if not isinstance(out[name], str) or not out[name]:
            raise ExecutionError('Invalid fill identity')
    if out['side'] not in ('buy', 'sell'):
        raise ExecutionError('Invalid fill side')
    out['fillTime'] = iso(exchange_time(out['fillTime']))
    for name in ('size', 'price'):
        out[name] = number(numeric(out[name], positive=True))
    client_id = row.get('cliOrdId')
    if client_id is not None and (not isinstance(client_id, str) or not client_id):
        raise ExecutionError('Invalid fill client ID')
    out['cliOrdId'] = client_id
    return out


def deduplicate_fills(rows):
    indexed = {}
    for row in rows:
        item = normalized_fill(row)
        old = indexed.get(item['fill_id'])
        if old:
            # Historical pages may omit realized_pnl and other optional fields.
            if any(old[k] != item[k] for k in CORE):
                raise ExecutionError('Conflicting fill identity')
            if old['cliOrdId'] and item['cliOrdId'] and old['cliOrdId'] != item['cliOrdId']:
                raise ExecutionError('Conflicting fill client ID')
            old['cliOrdId'] = old['cliOrdId'] or item['cliOrdId']
        else:
            indexed[item['fill_id']] = item
    return sorted(indexed.values(), key=lambda r: (utc(r['fillTime']), r['fill_id']))


def capture_fills(directory, client, since, through, *, max_pages=100):
    """Capture all pages available for [since, through], with honest coverage.

    Kraken provides only a timestamp cursor. A full page whose oldest timestamp
    is inside the required window might truncate a group of simultaneous fills;
    paging before that timestamp cannot recover the omitted group. Flag this
    even if subsequent pages reach the requested start.
    """
    start, end = exchange_time(since), exchange_time(through)
    if start > end or isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ExecutionError('Invalid capture window/page budget')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    _sync_directory(root.parent)
    pages, all_rows, reasons = [], [], set()
    cursor, reached_start = None, False
    for index in range(max_pages):
        params = {} if cursor is None else {'lastFillTime': iso(cursor)}
        status, raw = client.request('fills', params)
        name = f'{index:04d}'
        _create(root / (name + '.raw'), raw)
        metadata = {'http_status': status, 'params': params, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        _create(root / (name + '.json'), _json_bytes(metadata))
        try:
            body = json.loads(raw, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise ExecutionError('Invalid fill response; raw evidence retained') from None
        if status != 200 or not isinstance(body, dict) or body.get('result') != 'success' or not isinstance(body.get('fills'), list):
            raise ExecutionError('Failed fill response; raw evidence retained')
        if exchange_time(body['serverTime']) < end:
            raise ExecutionError('Fill response predates requested capture end')
        rows = [normalized_fill(r) for r in body['fills']]
        if len(rows) > 100:
            raise ExecutionError('Unexpected fill page size')
        stamps = [utc(r['fillTime']) for r in rows]
        if any(t > exchange_time(body['serverTime']) for t in stamps):
            raise ExecutionError('Fill is newer than response')
        if cursor is not None and any(t >= cursor for t in stamps):
            raise ExecutionError('Historical page did not respect before-cursor contract')
        pages.append(metadata)
        all_rows.extend(rows)
        if not rows or min(stamps) < start or len(rows) < 100:
            reached_start = True
            break
        reasons.add('timestamp_boundary_ambiguous')
        cursor = min(stamps)
    if not reached_start:
        reasons.add('page_budget_exhausted')
    fills = [r for r in deduplicate_fills(all_rows) if start <= utc(r['fillTime']) <= end]
    report = {'version': 1, 'environment': 'demo', 'since_utc': iso(start), 'through_utc': iso(end),
              'coverage_complete': reached_start and not reasons, 'coverage_reasons': sorted(reasons),
              'pages': pages, 'fills': fills, 'fill_count': len(fills),
              'actual_costs_verified': False}
    _create(root / 'manifest.json', _json_bytes(report))
    return report


def reconcile(capture, known_orders, open_orders, positions, *, starting_quantity, symbol='PF_DOTUSD'):
    """Compare signed fills and resting orders with a trusted account baseline.

    known_orders maps exchange client IDs to persisted intent facts: symbol,
    side, size; optional exchange_order_id and paper_fill_price_usd. Callers
    must prove account identity, baseline time, and readback consistency. This
    function reports discrepancies and never repairs/cancels/sends anything.
    """
    with localcontext() as context:
        context.prec = 34
        baseline = numeric(starting_quantity)
        expected = baseline
        issues, grouped, exchange_ids = [], {}, {}
        for client_id, intent in known_orders.items():
            if not isinstance(client_id, str) or not client_id or intent['symbol'] != symbol or intent['side'] not in ('buy', 'sell'):
                raise ExecutionError('Invalid known order')
            numeric(intent['size'], positive=True)
            if intent.get('exchange_order_id'):
                order_id = intent['exchange_order_id']
                if order_id in exchange_ids:
                    raise ExecutionError('Exchange order ID assigned twice')
                exchange_ids[order_id] = client_id
        for fill in deduplicate_fills(capture['fills']):
            if fill['symbol'] != symbol:
                continue
            qty = numeric(fill['size'], positive=True)
            expected += qty if fill['side'] == 'buy' else -qty
            client_id = fill['cliOrdId'] or exchange_ids.get(fill['order_id'])
            known = known_orders.get(client_id)
            if known is None:
                issues.append({'kind': 'unowned_fill', 'fill_id': fill['fill_id']})
                continue
            if known['side'] != fill['side'] or (known.get('exchange_order_id') and known['exchange_order_id'] != fill['order_id']):
                issues.append({'kind': 'fill_intent_mismatch', 'fill_id': fill['fill_id']})
            group = grouped.setdefault(client_id, {'quantity': Decimal(0), 'notional': Decimal(0), 'ids': [], 'order_ids': set()})
            group['quantity'] += qty
            group['notional'] += qty * numeric(fill['price'], positive=True)
            group['ids'].append(fill['fill_id'])
            group['order_ids'].add(fill['order_id'])
        fills_report = {}
        for client_id, group in sorted(grouped.items()):
            intent = known_orders[client_id]
            if group['quantity'] > numeric(intent['size']):
                issues.append({'kind': 'filled_quantity_exceeds_intent', 'client_id': client_id})
            if len(group['order_ids']) != 1:
                issues.append({'kind': 'client_id_multiple_exchange_orders', 'client_id': client_id})
            average = group['notional'] / group['quantity']
            reference = intent.get('paper_fill_price_usd')
            adverse = None if reference is None else (average / numeric(reference, positive=True) - 1) * 10000 * (1 if intent['side'] == 'buy' else -1)
            fills_report[client_id] = {'filled_quantity': number(group['quantity']), 'average_fill_price_usd': number(average),
                'paper_adverse_difference_bps': None if adverse is None else number(adverse), 'fill_ids': sorted(group['ids'])}
        seen_open = set()
        for order in open_orders:
            if order['symbol'] != symbol:
                continue
            client_id = order.get('cliOrdId')
            if not client_id or client_id not in known_orders:
                issues.append({'kind': 'orphan_open_order', 'order_id': order['order_id']})
                continue
            if client_id in seen_open:
                issues.append({'kind': 'duplicate_open_client_id', 'client_id': client_id})
            seen_open.add(client_id)
            intent = known_orders[client_id]
            remaining = numeric(order['unfilledSize'])
            filled = numeric(order['filledSize'])
            if remaining < 0 or filled < 0:
                raise ExecutionError('Negative open order quantities')
            if (order['side'] != intent['side'] or remaining + filled > numeric(intent['size'])
                    or (intent.get('exchange_order_id') and order['order_id'] != intent['exchange_order_id'])
                    or ('reduceOnly' in intent and order.get('reduceOnly') != intent['reduceOnly'])):
                issues.append({'kind': 'open_order_intent_mismatch', 'client_id': client_id})
        matching = [p for p in positions if p['symbol'] == symbol]
        if len(matching) > 1:
            raise ExecutionError('Ambiguous net exchange position')
        actual = Decimal(0)
        if matching:
            position = matching[0]
            if position['side'] not in ('long', 'short'):
                raise ExecutionError('Invalid position side')
            qty = numeric(position['size'])
            if qty < 0:
                raise ExecutionError('Negative position size')
            actual = qty if position['side'] == 'long' else -qty
        if actual != expected:
            issues.append({'kind': 'position_quantity_mismatch'})
        if not capture['coverage_complete']:
            issues.append({'kind': 'fill_coverage_incomplete'})
        return {'version': 1, 'symbol': symbol, 'quantity_reconciled': not issues,
                'expected_signed_quantity': number(expected), 'exchange_signed_quantity': number(actual),
                'orders': fills_report, 'issues': issues, 'actual_costs_verified': False,
                'exchange_net_pnl_usd': None, 'authorizes_execution': False}
