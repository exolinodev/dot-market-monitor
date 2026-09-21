"""Account-bound, cursor-paginated execution/order history from Kraken demo."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
from uuid import UUID

from cycles import iso
from exchange_reconciliation import exchange_time, deduplicate_fills
from kraken_execution import ExecutionError, _create, _json_bytes, _sync_directory

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def milliseconds(value):
    point = exchange_time(value)
    if point.microsecond % 1000:
        raise ExecutionError('History window must align to milliseconds')
    delta = point - EPOCH
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def stamp(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionError('History timestamp must be integer milliseconds')
    return iso(EPOCH + timedelta(milliseconds=value))


def account_id(value):
    try:
        result = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ExecutionError('Invalid history account UID') from None
    if value != result:
        raise ExecutionError('Canonical history account UID required')
    return result


def execution_fill(element, expected_account):
    """Normalize the documented nested execution object, retaining exact decimals."""
    execution = element['event']['execution']['execution']
    order = execution['order']
    if account_id(order['accountUid']) != expected_account:
        raise ExecutionError('Execution belongs to a different account')
    if order['direction'] not in ('Buy', 'Sell'):
        raise ExecutionError('Unknown execution direction')
    if execution['executionType'] not in ('maker', 'taker'):
        raise ExecutionError('Unknown execution type')
    return {'fill_id': execution['uid'], 'order_id': order['uid'],
            'cliOrdId': order.get('clientId') or None, 'fillTime': stamp(execution['timestamp']),
            'symbol': order['tradeable'], 'side': 'buy' if order['direction'] == 'Buy' else 'sell',
            'size': execution['quantity'], 'price': execution['price'], 'fillType': execution['executionType']}


def normalized_order(order, expected_account):
    from exchange_reconciliation import numeric, number
    if account_id(order['accountUid']) != expected_account:
        raise ExecutionError('Historical order belongs to a different account')
    if not isinstance(order['uid'], str) or not order['uid'] or type(order['reduceOnly']) is not bool:
        raise ExecutionError('Invalid historical order identity/policy')
    return {'order_id': order['uid'], 'account_uid': expected_account,
            'client_id': order['clientId'] or None, 'symbol': order['tradeable'],
            'direction': order['direction'], 'order_type': order['orderType'],
            'quantity': number(numeric(order['quantity'])), 'filled': number(numeric(order['filled'])),
            'limit_price': order['limitPrice'], 'reduce_only': order['reduceOnly'],
            'created_at_utc': stamp(order['timestamp']), 'updated_at_utc': stamp(order['lastUpdateTimestamp'])}


def order_event(element, expected_account):
    event = element['event']
    kinds = ('OrderPlaced', 'OrderUpdated', 'OrderRejected', 'OrderCancelled', 'OrderNotFound', 'OrderEditRejected')
    if not isinstance(event, dict) or len(event) != 1 or next(iter(event)) not in kinds:
        raise ExecutionError('Unknown or ambiguous historical order event')
    kind = next(iter(event)); detail = event[kind]
    result = {'event_id': element['uid'], 'at_utc': stamp(element['timestamp']), 'kind': kind,
              'account_uid': expected_account}
    if kind == 'OrderNotFound':
        if account_id(detail['accountUid']) != expected_account:
            raise ExecutionError('Order-not-found event belongs to a different account')
        result['order_id'] = detail['orderId']
    elif kind == 'OrderUpdated':
        result['old_order'] = normalized_order(detail['oldOrder'], expected_account)
        result['order'] = normalized_order(detail['newOrder'], expected_account)
        if result['old_order']['order_id'] != result['order']['order_id']:
            raise ExecutionError('Order update changed exchange identity')
    elif kind == 'OrderEditRejected':
        result['order'] = normalized_order(detail['oldOrder'], expected_account)
        result['attempted_order'] = normalized_order(detail['attemptedOrder'], expected_account)
    else:
        result['order'] = normalized_order(detail['order'], expected_account)
    for field in ('reason', 'orderError'):
        if field in detail: result[field] = detail[field]
    return result


def capture_executions(directory, client, expected_account, since, through, *, max_pages=100):
    return _capture_history(directory, client, expected_account, since, through, max_pages=max_pages, endpoint='executions')


def capture_orders(directory, client, expected_account, since, through, *, max_pages=100):
    return _capture_history(directory, client, expected_account, since, through, max_pages=max_pages, endpoint='orders')


def _capture_history(directory, client, expected_account, since, through, *, max_pages, endpoint):
    """Capture and normalize a fixed historical window; do not place any orders.

    Request one extra millisecond on both edges, then filter locally. The API
    documentation does not specify inclusivity of since/before. Tokens are
    opaque and may be provided in the response header or body; disagreement,
    repeated tokens, account changes and conflicting duplicates fail closed.
    """
    expected_account = account_id(expected_account)
    start, end = milliseconds(since), milliseconds(through)
    if start < 1 or start > end or isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ExecutionError('Invalid execution history window/page budget')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    _sync_directory(root.parent)
    params = {'since': str(start - 1), 'before': str(end + 1), 'sort': 'asc', 'count': '1000'}
    if endpoint == 'orders':
        params.update(opened=True, closed=True)
    pages, fills, seen_tokens, seen_events = [], [], set(), {}
    complete, last_stamp = False, None
    for index in range(max_pages):
        status, raw, headers = client.history(endpoint, params)
        name = f'{index:04d}'
        _create(root / (name + '.raw'), raw)
        # Archive only documented pagination/date headers, never authentication.
        selected = {key: headers[key] for key in ('Next-Continuation-Token', 'Date') if key in headers}
        metadata = {'http_status': status, 'params': dict(params), 'headers': selected,
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        _create(root / (name + '.json'), _json_bytes(metadata))
        try:
            body = json.loads(raw, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise ExecutionError('Invalid history response; raw evidence retained') from None
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get('elements'), list):
            raise ExecutionError('Failed execution history response')
        if account_id(body.get('accountUid')) != expected_account:
            raise ExecutionError('History belongs to a different account')
        elements = body['elements']
        if type(body.get('len')) is not int or body['len'] != len(elements) or len(elements) > 1000:
            raise ExecutionError('History response length mismatch')
        try:
            received = exchange_time(parsedate_to_datetime(selected['Date']))
        except (KeyError, ValueError, TypeError):
            raise ExecutionError('History needs a valid response Date') from None
        if received < exchange_time(through):
            raise ExecutionError('History response predates requested end')
        for element in elements:
            uid = element['uid']
            if not isinstance(uid, str) or not uid:
                raise ExecutionError('Missing history event identity')
            event_stamp = milliseconds(stamp(element['timestamp']))
            if not start - 1 <= event_stamp <= end + 1:
                raise ExecutionError('Execution event outside requested window')
            normalized = execution_fill(element, expected_account) if endpoint == 'executions' else order_event(element, expected_account)
            fill_stamp = milliseconds(normalized['fillTime'] if endpoint == 'executions' else normalized['at_utc'])
            if not start - 1 <= fill_stamp <= end + 1:
                raise ExecutionError('Execution fill outside requested window')
            # Preserve the full raw event fingerprint, including optional fields.
            fingerprint = json.dumps(element, sort_keys=True, separators=(',', ':'), default=str)
            if uid in seen_events:
                if seen_events[uid] != fingerprint:
                    raise ExecutionError('Conflicting execution history event')
                continue
            if last_stamp is not None and event_stamp < last_stamp:
                raise ExecutionError('History is not ascending')
            last_stamp = event_stamp
            seen_events[uid] = fingerprint
            if start <= fill_stamp <= end:
                fills.append(normalized)
        pages.append(metadata)
        header_token, body_token = selected.get('Next-Continuation-Token'), body.get('continuationToken')
        for token in (header_token, body_token):
            if token is not None and (not isinstance(token, str) or not token):
                raise ExecutionError('Invalid continuation token')
        if header_token is not None and body_token is not None and header_token != body_token:
            raise ExecutionError('Conflicting continuation tokens')
        token = header_token or body_token
        if token is None:
            complete = True
            break
        if token in seen_tokens:
            raise ExecutionError('Repeated continuation token')
        seen_tokens.add(token)
        params = {**params, 'continuation_token': token}
    report = {'version': 1, 'environment': 'demo', 'source': 'execution_history' if endpoint == 'executions' else 'order_history',
              'account_uid': expected_account, 'since_utc': iso(exchange_time(since)),
              'through_utc': iso(exchange_time(through)), 'coverage_complete': complete,
              'coverage_reasons': [] if complete else ['page_budget_exhausted'],
              'pages': pages,
              'actual_costs_verified': False, 'authorizes_execution': False}
    if endpoint == 'executions':
        report['fills'] = deduplicate_fills(fills)
        report['fill_count'] = len(report['fills'])
    else:
        report['order_events'] = fills
        report['event_count'] = len(fills)
    _create(root / 'manifest.json', _json_bytes(report))
    return report


def verify_capture(directory, expected_account, since, through, *, source='execution_history'):
    """Rebuild a capture from raw pages, checking caller-supplied account/window.

    This verifies local integrity, not exchange authenticity. The runner still
    needs a trusted persisted capture hash and authenticated acquisition chain.
    """
    import tempfile
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ExecutionError('History capture must be a regular directory')
    paths = list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise ExecutionError('History artifacts must be regular files')
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if source not in ('execution_history', 'order_history') or manifest.get('source') != source:
        raise ExecutionError('History source differs from requested verification')
    expected_endpoint = 'executions' if source == 'execution_history' else 'orders'
    page_count = len(manifest['pages'])
    if not page_count:
        raise ExecutionError('History capture has no pages')
    names = {'manifest.json'} | {f'{i:04d}.{ext}' for i in range(page_count) for ext in ('raw', 'json')}
    if {p.name for p in paths} != names:
        raise ExecutionError('Unexpected history capture artifacts')

    class Recorded:
        def __init__(self): self.index = 0
        def history(self, endpoint, params):
            index = self.index
            self.index += 1
            if index >= page_count:
                raise ExecutionError('Missing recorded history page')
            metadata = json.loads((root / f'{index:04d}.json').read_bytes())
            raw = (root / f'{index:04d}.raw').read_bytes()
            if (endpoint != expected_endpoint or metadata['params'] != params
                    or metadata['sha256'] != hashlib.sha256(raw).hexdigest()
                    or metadata['bytes'] != len(raw)):
                raise ExecutionError('History evidence or request binding changed')
            return metadata['http_status'], raw, metadata['headers']

    recorded = Recorded()
    with tempfile.TemporaryDirectory(prefix='oracle-history-verify-') as temporary:
        rebuilt = _capture_history(Path(temporary) / 'capture', recorded, expected_account,
                                   since, through, max_pages=page_count, endpoint=expected_endpoint)
    if rebuilt != manifest or recorded.index != page_count:
        raise ExecutionError('History manifest differs from raw replay')
    return rebuilt
