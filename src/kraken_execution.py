"""Demo-only private REST transport and durable, at-most-once mutation attempts.

This is infrastructure, not a trading runner. It does not authorize a forecast,
manage brackets, reconcile positions, or enable live execution.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, quote

import requests

DEMO_URL = 'https://demo-futures.kraken.com/derivatives'
READS = {'openorders': 'openOrders', 'fills': 'fills',
         'openpositions': 'openPositions', 'accounts': 'accounts'}
READS_WITH_PREFERENCES = {**READS, 'leveragepreferences': 'leveragePreferences'}
MUTATIONS = {'sendorder': 'sendStatus', 'cancelorder': 'cancelStatus', 'editorder': 'editStatus'}
MAX_RESPONSE_BYTES = 2_000_000


class ExecutionError(ValueError):
    pass


class OutcomeUnknown(ExecutionError):
    """A recorded attempt must be reconciled; absence is never permission to retry."""


def encoded_params(params):
    """Hash exactly the form bytes sent, including URL-encoding (post-2025 auth)."""
    pairs = []
    for key, value in sorted(params.items()):
        if not isinstance(key, str) or not isinstance(value, (str, bool)):
            raise ExecutionError('REST parameters must be explicit strings or booleans')
        pairs.append((key, str(value).lower() if isinstance(value, bool) else value))
    return urlencode(pairs, quote_via=quote)


def authent(secret, encoded, endpoint_path):
    if not endpoint_path.startswith(('/api/v3/', '/api/history/v3/')):
        raise ExecutionError('Invalid signing path')
    try:
        key = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        raise ExecutionError('Invalid API secret encoding') from None
    if not key:
        raise ExecutionError('Empty API secret')
    # Nonce is optional in Futures REST; omit it to avoid cross-process counters.
    digest = hashlib.sha256((encoded + endpoint_path).encode()).digest()
    return base64.b64encode(hmac.new(key, digest, hashlib.sha512).digest()).decode()


class DemoClient:
    """Fixed demo host, no redirects/retries, bounded raw response, no secret logs."""
    def __init__(self, api_key, api_secret):
        if not api_key or not api_secret:
            raise ExecutionError('Demo credentials required')
        self._key, self._secret = api_key, api_secret
        self._session = requests.Session()
        self._session.trust_env = False

    def request(self, endpoint, params=None):
        if endpoint not in READS_WITH_PREFERENCES and endpoint not in MUTATIONS:
            raise ExecutionError('Unsupported private endpoint')
        status, raw, _ = self._request('/api/v3/' + endpoint, params or {},
                                      'GET' if endpoint in READS_WITH_PREFERENCES else 'POST', DEMO_URL)
        return status, raw

    def market(self, endpoint):
        from demo_market import DemoMarketClient
        return DemoMarketClient().market(endpoint)

    def history(self, endpoint, params):
        if endpoint not in ('executions', 'orders', 'triggers', 'account-log'):
            raise ExecutionError('Unsupported history endpoint')
        return self._request('/api/history/v3/' + endpoint, params, 'GET',
                             'https://demo-futures.kraken.com')

    def _request(self, path, params, method, base):
        encoded = encoded_params(params)
        headers = {'APIKey': self._key, 'Authent': authent(self._secret, encoded, path),
                   'Content-Type': 'application/x-www-form-urlencoded'}
        url = base + path
        if method == 'GET' and encoded:
            url += '?' + encoded
        try:
            with self._session.request(method, url, headers=headers,
                    data=encoded if method == 'POST' else None, stream=True,
                    allow_redirects=False, timeout=(5, 20)) as response:
                raw = bytearray()
                for block in response.iter_content(65536):
                    raw.extend(block)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ExecutionError('Private response exceeded byte limit')
                selected = {k: v for k in ('Next-Continuation-Token', 'Date')
                            if (v := getattr(response, 'headers', {}).get(k)) is not None}
                return response.status_code, bytes(raw), selected
        except requests.RequestException:
            # Request exceptions may include headers/URLs; never surface them.
            raise OutcomeUnknown('Demo request failed; reconcile any mutation attempt') from None


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create(path, raw):
    """Exclusive creation and fsync; a partial file on a crash blocks reuse safely."""
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def _response(status, raw, field):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ExecutionError('Non-JSON demo response; raw evidence retained') from None
    if status != 200 or not isinstance(value, dict) or value.get('result') != 'success' or field not in value:
        raise ExecutionError('Demo response failed; inspect retained evidence')
    return value


class DemoAttempts:
    """Single-account durable journal; retain it across restarts and key rotation.

    Every logical mutation has a stable operation_id. Callers must keep the same
    ID after timeout or crash. There is no retry/overwrite/reset option. Empty
    openorders is never evidence that an earlier request was not executed.
    """
    def __init__(self, directory, client):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client

    def mutate(self, operation_id, endpoint, params):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise ExecutionError('Invalid stable operation ID')
        if endpoint not in MUTATIONS:
            raise ExecutionError('Unsupported mutation')
        client_id = params.get('cliOrdId')
        if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', client_id):
            raise ExecutionError('Mutation requires a stable cliOrdId')
        if endpoint == 'sendorder' and operation_id != client_id:
            raise ExecutionError('Send operation ID must equal cliOrdId')
        intent = {'version': 1, 'environment': 'demo', 'endpoint': endpoint,
                  'params': params, 'encoded_params': encoded_params(params)}
        directory = self.root / operation_id
        try:
            directory.mkdir()
        except FileExistsError:
            if directory.is_symlink() or not directory.is_dir():
                raise ExecutionError('Attempt must be a regular directory')
            try:
                original = (directory / 'intent.json').read_bytes()
            except OSError:
                raise OutcomeUnknown('Incomplete durable attempt; reconcile before proceeding') from None
            if original != _json_bytes(intent):
                raise ExecutionError('Operation ID reused with different intent')
            if not (directory / 'response.json').exists() or not (directory / 'response.raw').exists():
                raise OutcomeUnknown('Prior attempt has unknown outcome; never resubmit')
            metadata = json.loads((directory / 'response.json').read_bytes())
            raw = (directory / 'response.raw').read_bytes()
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ExecutionError('Archived response hash mismatch')
            return _response(metadata['http_status'], raw, MUTATIONS[endpoint])
        _sync_directory(self.root)
        _create(directory / 'intent.json', _json_bytes(intent))
        # This also provides a raw preflight record for later reconciliation.
        # Any failure consumes this operation ID conservatively.
        status, raw = self.client.request('openorders')
        _create(directory / 'openorders.raw', raw)
        _create(directory / 'openorders.json', _json_bytes({'http_status': status,
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}))
        opened = _response(status, raw, 'openOrders')['openOrders']
        if not isinstance(opened, list) or any(not isinstance(o, dict) for o in opened):
            raise ExecutionError('Malformed open order list')
        if endpoint == 'sendorder' and any(o.get('cliOrdId') == client_id for o in opened):
            raise OutcomeUnknown('Client order already exists; reconcile without sending')
        _create(directory / 'attempt.json', _json_bytes({'intent_sha256': hashlib.sha256(_json_bytes(intent)).hexdigest()}))
        status, raw = self.client.request(endpoint, params)
        _create(directory / 'response.raw', raw)
        _create(directory / 'response.json', _json_bytes({'http_status': status,
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}))
        # result=success only means assessed; callers MUST inspect operation status.
        return _response(status, raw, MUTATIONS[endpoint])


def entry_request(validated_plan, validated_config):
    """Translate the writer's order and quantity; never size from model output.

    The orchestrator must verify the persisted plan and publication evidence
    before using this pure adapter. No bracket or activation is implied.
    """
    if validated_config['instrument'] != 'PF_DOTUSD' or len(validated_plan['orders']) != 1:
        raise ExecutionError('Exactly one validated PF_DOTUSD entry required')
    item = validated_plan['orders'][0]
    order = item['order']
    kind = order['entry']['type']
    if kind not in ('LIMIT', 'MARKET', 'STOP') or order['side'] not in ('LONG', 'SHORT'):
        raise ExecutionError('Unsupported entry')
    params = {'cliOrdId': order['client_id'], 'symbol': 'PF_DOTUSD',
              'side': 'buy' if order['side'] == 'LONG' else 'sell',
              'size': item['size']['quantity'], 'reduceOnly': False,
              'orderType': {'LIMIT': 'lmt', 'MARKET': 'mkt', 'STOP': 'stp'}[kind]}
    if kind == 'LIMIT':
        params['limitPrice'] = order['entry']['price_usd']
    elif kind == 'STOP':
        params.update(stopPrice=order['entry']['price_usd'], triggerSignal='mark')
    encoded_params(params)
    return params


def capture_readback(directory, client, *, include_preferences=False):
    """Create-only raw snapshot, not a complete fills history or reconciliation.

    The default fills endpoint returns at most 100 recent fills. The eventual
    runner must paginate and prove coverage before declaring reconciliation.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    _sync_directory(root.parent)
    manifest = {'version': 2 if include_preferences else 1, 'environment': 'demo', 'responses': {}}
    fields = READS_WITH_PREFERENCES if include_preferences else READS
    for endpoint, field in fields.items():
        status, raw = client.request(endpoint)
        _create(root / (endpoint + '.raw'), raw)
        metadata = {'http_status': status, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        _create(root / (endpoint + '.json'), _json_bytes(metadata))
        value = _response(status, raw, field)
        if not isinstance(value[field], dict if endpoint == 'accounts' else list):
            raise ExecutionError('Malformed readback collection')
        manifest['responses'][endpoint] = metadata
    _create(root / 'manifest.json', _json_bytes(manifest))
    return manifest
