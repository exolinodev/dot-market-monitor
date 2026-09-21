"""Credential-free demo market acquisition; redirects never reach private reads."""
import hashlib
import json
from pathlib import Path
import tempfile

import requests
from cycles import iso
from exchange_reconciliation import numeric, number, exchange_time
from kraken_execution import ExecutionError, MAX_RESPONSE_BYTES, _create, _json_bytes, _sync_directory

BASE = 'https://demo-futures.kraken.com/derivatives/api/v3/'
ENDPOINTS = ('tickers', 'instruments')


class DemoMarketClient:
    def market(self, endpoint):
        if endpoint not in ENDPOINTS:
            raise ExecutionError('Unsupported public demo endpoint')
        with requests.Session() as session:
            session.trust_env = False
            try:
                with session.get(BASE + endpoint, stream=True, allow_redirects=False, timeout=(5, 20)) as response:
                    raw = bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ExecutionError('Demo market response exceeded byte limit')
                    headers = {k: response.headers[k] for k in ('Date', 'Location', 'Content-Type') if k in response.headers}
                    return response.status_code, bytes(raw), headers
            except requests.RequestException:
                raise ExecutionError('Public demo market request failed') from None


def normalize(payloads):
    selected = {}
    for name in ENDPOINTS:
        body = payloads[name]
        if body.get('result') != 'success' or not isinstance(body.get(name), list):
            raise ExecutionError('Invalid public demo response')
        items = [r for r in body[name] if isinstance(r, dict) and str(r.get('symbol', '')).upper() == 'PF_DOTUSD']
        if len(items) != 1:
            raise ExecutionError('Unique PF_DOTUSD market required')
        selected[name] = items[0]
    ticker, instrument = selected['tickers'], selected['instruments']
    bid, ask = numeric(ticker['bid'], positive=True), numeric(ticker['ask'], positive=True)
    if bid > ask or type(ticker['suspended']) is not bool or type(ticker['postOnly']) is not bool or type(instrument['tradeable']) is not bool:
        raise ExecutionError('Invalid demo quote or trading status')
    return {'symbol': 'PF_DOTUSD', 'bid': number(bid), 'ask': number(ask),
            'mark_price': number(numeric(ticker['markPrice'], positive=True)),
            'quote_at_utc': iso(exchange_time(payloads['tickers']['serverTime'])),
            'instrument_at_utc': iso(exchange_time(payloads['instruments']['serverTime'])),
            'suspended': ticker['suspended'], 'post_only': ticker['postOnly'], 'tradeable': instrument['tradeable'],
            'tick_size': number(numeric(instrument['tickSize'], positive=True)),
            'contract_size': number(numeric(instrument['contractSize'], positive=True))}


def capture_market(directory, client):
    root = Path(directory); root.mkdir(mode=0o700, parents=False, exist_ok=False)
    _sync_directory(root.parent)
    report = {'version': 1, 'environment': 'demo', 'source': 'public_demo_market', 'responses': {},
              'available': False, 'authorizes_execution': False}
    payloads = {}
    for endpoint in ENDPOINTS:
        status, raw, headers = client.market(endpoint)
        _create(root/(endpoint+'.raw'), raw)
        metadata = {'requested_url': BASE+endpoint, 'http_status': status, 'headers': headers,
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        _create(root/(endpoint+'.json'), _json_bytes(metadata))
        report['responses'][endpoint] = metadata
        if status != 200:
            report['reason'] = 'http_status_' + str(status)
            break
        try:
            payloads[endpoint] = json.loads(raw, parse_float=str)
        except (ValueError, UnicodeError):
            report['reason'] = 'invalid_json'
            break
    else:
        try:
            report['market'] = normalize(payloads)
            report['available'] = True
        except (ValueError, KeyError, TypeError, AttributeError):
            report['reason'] = 'invalid_market_contract'
    _create(root/'manifest.json', _json_bytes(report))
    return report


def verify_market(directory):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ExecutionError('Regular demo market capture required')
    paths = list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise ExecutionError('Irregular demo market evidence')
    report = json.loads((root/'manifest.json').read_bytes())
    names = {'manifest.json'} | {endpoint+ext for endpoint in report['responses'] for ext in ('.raw', '.json')}
    if {p.name for p in paths} != names:
        raise ExecutionError('Unexpected demo market evidence')
    class Recorded:
        def market(self, endpoint):
            meta = json.loads((root/(endpoint+'.json')).read_bytes())
            raw = (root/(endpoint+'.raw')).read_bytes()
            if (meta['requested_url'] != BASE+endpoint or meta['bytes'] != len(raw)
                    or meta['sha256'] != hashlib.sha256(raw).hexdigest()):
                raise ExecutionError('Demo market evidence changed')
            return meta['http_status'], raw, meta['headers']
    with tempfile.TemporaryDirectory(prefix='oracle-demo-market-') as temp:
        rebuilt = capture_market(Path(temp)/'capture', Recorded())
    if rebuilt != report:
        raise ExecutionError('Demo market manifest differs from replay')
    return rebuilt
