"""Account-bound raw account-log capture; ID pagination, no invented USD totals."""
from datetime import timezone, timedelta
from email.utils import parsedate_to_datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile

from cycles import iso, utc
from exchange_history import account_id, milliseconds
from exchange_reconciliation import exchange_time, numeric, number
from kraken_execution import ExecutionError, _create, _json_bytes, _sync_directory

NUMERIC = ('fee', 'funding_rate', 'mark_price', 'new_average_entry_price', 'old_average_entry_price',
           'realized_funding', 'realized_pnl', 'trade_price', 'conversion_spread_percentage', 'liquidation_fee')
TEXT = ('asset', 'booking_uid', 'info', 'margin_account')
NULLABLE_TEXT = ('collateral', 'contract', 'execution')


def normalize(row):
    if type(row['id']) is not int or not 1 <= row['id'] < 2**63:
        raise ExecutionError('Invalid account-log ID')
    item = {'id': row['id'], 'at_utc': iso(exchange_time(row['date']))}
    for key in TEXT + NULLABLE_TEXT:
        value = row[key]
        if value is None and key in NULLABLE_TEXT:
            item[key] = None
        elif not isinstance(value, str) or not value:
            raise ExecutionError('Invalid account-log identity/type')
        else: item[key] = value
    for key in ('old_balance', 'new_balance'):
        item[key] = number(numeric(row[key]))
    for key in NUMERIC:
        item[key] = None if row[key] is None else number(numeric(row[key]))
    # Conversion fields are optional; never silently assume USD or no fees.
    for key in ('exchange_rate', 'conversion_fee'):
        if key in row: item[key] = number(numeric(row[key]))
    if 'exchange_rate_from' in row:
        if not isinstance(row['exchange_rate_from'], str) or not row['exchange_rate_from']:
            raise ExecutionError('Invalid account-log conversion currency')
        item['exchange_rate_from'] = row['exchange_rate_from']
    return item


def capture_account_log(directory, client, expected_account, since, through, *, max_pages=100):
    account_id(expected_account)
    start, end = milliseconds(since), milliseconds(through)
    if not 1 <= start <= end or type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ExecutionError('Invalid account-log window/page budget')
    root = Path(directory); root.mkdir(parents=True, exist_ok=False); _sync_directory(root.parent)
    params = {'since': str(start-1), 'before': str(end+1), 'sort': 'asc', 'count': '500', 'conversion_details': True}
    pages, entries, seen_bookings = [], [], set()
    previous_id, previous_time, complete = 0, None, False
    for index in range(max_pages):
        status, raw, headers = client.history('account-log', params)
        selected = {k: headers[k] for k in ('Date',) if k in headers}
        meta = {'http_status': status, 'params': dict(params), 'headers': selected,
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        _create(root/f'{index:04d}.raw', raw); _create(root/f'{index:04d}.json', _json_bytes(meta))
        body = json.loads(raw, parse_float=Decimal)
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get('logs'), list):
            raise ExecutionError('Failed account-log response')
        if account_id(body.get('accountUid')) != expected_account:
            raise ExecutionError('Account log belongs to a different account')
        try: received = parsedate_to_datetime(selected['Date'])
        except (KeyError, ValueError, TypeError):
            raise ExecutionError('Account log needs a valid response Date') from None
        if received.tzinfo is None or received.astimezone(timezone.utc) < exchange_time(through):
            raise ExecutionError('Account-log response predates requested end')
        if len(body['logs']) > 500:
            raise ExecutionError('Account-log page exceeds requested count')
        pages.append(meta)
        if not body['logs']:
            complete = True; break
        for row in body['logs']:
            item = normalize(row); at = exchange_time(item['at_utc'])
            if item['id'] <= previous_id or (previous_time is not None and at < previous_time):
                raise ExecutionError('Account-log cursor or chronology did not advance')
            if item['booking_uid'] in seen_bookings:
                raise ExecutionError('Repeated account-log booking identity')
            if not utc(since)-timedelta(milliseconds=1) <= at <= utc(through)+timedelta(milliseconds=1):
                raise ExecutionError('Account-log entry outside requested window')
            previous_id, previous_time = item['id'], at
            seen_bookings.add(item['booking_uid'])
            if utc(since) <= at <= utc(through): entries.append(item)
        if previous_id == 2**63-1:
            raise ExecutionError('Account-log cursor exhausted int64 range')
        # from is documented inclusive; next ID avoids shared-timestamp loss.
        # Even short pages are followed until an explicit empty response.
        params = {**params, 'from': str(previous_id+1)}
    report = {'version': 1, 'environment': 'demo', 'source': 'account_log', 'account_uid': expected_account,
              'since_utc': iso(since), 'through_utc': iso(through), 'coverage_complete': complete,
              'coverage_reasons': [] if complete else ['page_budget_exhausted'], 'pages': pages,
              'entries': entries, 'entry_count': len(entries), 'actual_costs_verified': False,
              'authorizes_execution': False}
    _create(root/'manifest.json', _json_bytes(report))
    return report


def verify_account_log(directory, expected_account, since, through):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir(): raise ExecutionError('Regular account-log capture required')
    paths = list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in paths): raise ExecutionError('Irregular account-log evidence')
    report = json.loads((root/'manifest.json').read_bytes()); count = len(report['pages'])
    if report['source'] != 'account_log' or not 1 <= count <= 100:
        raise ExecutionError('Invalid account-log manifest')
    if {p.name for p in paths} != {'manifest.json'} | {f'{i:04d}.{ext}' for i in range(count) for ext in ('raw', 'json')}:
        raise ExecutionError('Unexpected account-log artifacts')
    class Recorded:
        def __init__(self): self.index = 0
        def history(self, endpoint, params):
            index = self.index; self.index += 1
            if index >= count: raise ExecutionError('Missing account-log page')
            meta = json.loads((root/f'{index:04d}.json').read_bytes()); raw = (root/f'{index:04d}.raw').read_bytes()
            if (endpoint != 'account-log' or params != meta['params'] or len(raw) != meta['bytes']
                    or hashlib.sha256(raw).hexdigest() != meta['sha256']):
                raise ExecutionError('Account-log request/evidence binding changed')
            return meta['http_status'], raw, meta['headers']
    recorded = Recorded()
    with tempfile.TemporaryDirectory(prefix='oracle-account-log-') as temp:
        rebuilt = capture_account_log(Path(temp)/'capture', recorded, expected_account, since, through, max_pages=count)
    if rebuilt != report or recorded.index != count: raise ExecutionError('Account-log manifest differs from raw replay')
    return rebuilt


def cashflow_check(log, baseline, reference, fills):
    """Classify declared account activity; do not sum fees across wallet/position rows."""
    start, end = utc(baseline), utc(reference)
    if not log['coverage_complete'] or utc(log['since_utc']) > start or utc(log['through_utc']) < end:
        return {'verified_no_external_flows': False, 'issues': [{'kind': 'account_log_coverage_incomplete'}]}
    fill_ids = {f['fill_id'] for f in fills}
    external = {'deposit', 'withdrawal', 'admin transfer', 'transfer', 'subaccount transfer',
                'cross-exchange transfer', 'loan transfer', 'position transfer',
                'external custody delegation', 'external custody undelegation', 'external custody unbind'}
    issues = []
    for row in log['entries']:
        if not start < utc(row['at_utc']) <= end: continue
        info = row['info']
        if info in external:
            kind = 'external_account_flow'
        elif info == 'futures trade' and row['execution'] in fill_ids and row['contract'] == 'PF_DOTUSD':
            continue
        elif info == 'funding rate change' and row['contract'] == 'PF_DOTUSD':
            continue
        else: kind = 'unclassified_account_activity'
        issues.append({'kind': kind, 'entry_id': row['id'], 'info': info})
    return {'verified_no_external_flows': not issues, 'issues': issues}
