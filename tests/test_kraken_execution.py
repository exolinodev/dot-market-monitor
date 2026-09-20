"""Offline protocol tests; no credentials, accounts or exchange orders."""
import base64
import hashlib
import hmac
import json

import pytest
import requests

from kraken_execution import (DemoClient, DemoAttempts, ExecutionError,
                              OutcomeUnknown, authent, encoded_params)


SECRET = base64.b64encode(b'synthetic-secret').decode()
PARAMS = {'cliOrdId': 'forecast-1', 'orderType': 'lmt', 'symbol': 'PF_DOTUSD',
          'side': 'buy', 'size': '10', 'limitPrice': '1', 'reduceOnly': False}


def raw(**body):
    return json.dumps({'result': 'success', **body}).encode()


class Fake:
    def __init__(self, response=None, orders=None, fail=False):
        self.calls = []
        self.response = response or raw(sendStatus={'status': 'placed', 'order_id': 'synthetic'})
        self.orders = orders or []
        self.fail = fail

    def request(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        if endpoint == 'openorders':
            return 200, raw(openOrders=self.orders)
        if self.fail:
            raise OutcomeUnknown('synthetic timeout after exchange acceptance')
        return 200, self.response


def test_signs_exact_percent_encoded_payload_without_derivatives_prefix():
    payload = encoded_params({'x': 'a b/+?', 'reduceOnly': True})
    assert payload == 'reduceOnly=true&x=a%20b%2F%2B%3F'
    expected = base64.b64encode(hmac.new(b'synthetic-secret',
        hashlib.sha256(b'reduceOnly=true&x=a%20b%2F%2B%3F/api/v3/sendorder').digest(),
        hashlib.sha512).digest()).decode()
    assert authent(SECRET, payload, '/api/v3/sendorder') == expected
    with pytest.raises(ExecutionError): authent('not base64', payload, '/api/v3/sendorder')


def test_identical_restart_returns_archived_response_without_network(tmp_path):
    client = Fake()
    first = DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert [c[0] for c in client.calls] == ['openorders', 'sendorder']
    assert (tmp_path / 'forecast-1/response.raw').read_bytes() == client.response
    fresh = Fake()
    assert DemoAttempts(tmp_path, fresh).mutate('forecast-1', 'sendorder', PARAMS) == first
    assert fresh.calls == []
    with pytest.raises(ExecutionError, match='different intent'):
        DemoAttempts(tmp_path, fresh).mutate('forecast-1', 'sendorder', {**PARAMS, 'size': '11'})


def test_timeout_with_empty_openorders_never_resends(tmp_path):
    client = Fake(fail=True)
    with pytest.raises(OutcomeUnknown): DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert (tmp_path / 'forecast-1/attempt.json').exists()
    restarted = Fake()
    with pytest.raises(OutcomeUnknown, match='never resubmit'):
        DemoAttempts(tmp_path, restarted).mutate('forecast-1', 'sendorder', PARAMS)
    assert restarted.calls == []


def test_existing_exchange_id_and_incomplete_local_intent_block_sending(tmp_path):
    client = Fake(orders=[{'cliOrdId': 'forecast-1'}])
    with pytest.raises(OutcomeUnknown, match='already exists'):
        DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert len(client.calls) == 1
    (tmp_path / 'forecast-2').mkdir()
    with pytest.raises(OutcomeUnknown, match='Incomplete'):
        DemoAttempts(tmp_path, client).mutate('forecast-2', 'sendorder', {**PARAMS, 'cliOrdId': 'forecast-2'})
    assert len(client.calls) == 1


def test_rejection_is_not_reported_as_fill_and_is_not_retried(tmp_path):
    client = Fake(response=raw(sendStatus={'status': 'insufficientAvailableFunds'}))
    result = DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert result['sendStatus']['status'] == 'insufficientAvailableFunds'
    assert DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS) == result
    assert len(client.calls) == 2


def test_raw_invalid_response_is_preserved_and_blocks_retry(tmp_path):
    client = Fake(response=b'<html>unavailable</html>')
    with pytest.raises(ExecutionError, match='Non-JSON'):
        DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert (tmp_path / 'forecast-1/response.raw').read_bytes() == client.response
    with pytest.raises(ExecutionError, match='Non-JSON'):
        DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert len(client.calls) == 2


def test_tampered_response_is_rejected(tmp_path):
    client = Fake()
    journal = DemoAttempts(tmp_path, client)
    journal.mutate('forecast-1', 'sendorder', PARAMS)
    (tmp_path / 'forecast-1/response.raw').write_bytes(b'{}')
    with pytest.raises(ExecutionError, match='hash mismatch'):
        journal.mutate('forecast-1', 'sendorder', PARAMS)


def test_transport_pins_demo_host_disables_redirects_and_does_not_expose_exception(monkeypatch):
    client = DemoClient('synthetic-public', SECRET)
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        raise requests.Timeout('private-header-secret')
    monkeypatch.setattr(client._session, 'request', request)
    with pytest.raises(OutcomeUnknown) as error:
        client.request('sendorder', PARAMS)
    assert 'private-header-secret' not in str(error.value)
    method, url, opts = calls[0]
    assert method == 'POST' and url == 'https://demo-futures.kraken.com/derivatives/api/v3/sendorder'
    assert opts['allow_redirects'] is False and opts['timeout'] == (5, 20)
    assert opts['data'] == encoded_params(PARAMS)
    assert opts['headers']['Authent'] == authent(SECRET, opts['data'], '/api/v3/sendorder')
    assert not client._session.trust_env
    with pytest.raises(ExecutionError, match='Unsupported'): client.request('withdrawal')
    assert len(calls) == 1


@pytest.mark.parametrize('kind,side,wire', [('LIMIT', 'LONG', 'lmt'), ('MARKET', 'SHORT', 'mkt'), ('STOP', 'LONG', 'stp')])
def test_entry_mapping_uses_python_quantity_and_mark_trigger(kind, side, wire):
    from kraken_execution import entry_request
    from test_ledger import prepare, order
    entry = order(kind=kind, side=side)
    if kind == 'STOP': entry['entry']['price_usd'] = '1.001'
    cfg, plan, _ = prepare(entry=entry)
    params = entry_request(plan, cfg)
    assert params['size'] == plan['orders'][0]['size']['quantity']
    assert params['orderType'] == wire and params['reduceOnly'] is False
    assert params['side'] == ('buy' if side == 'LONG' else 'sell')
    assert ('limitPrice' in params) == (kind == 'LIMIT')
    assert ('stopPrice' in params) == (kind == 'STOP')
    if kind == 'STOP': assert params['triggerSignal'] == 'mark'


def test_send_id_cannot_be_changed_to_bypass_durable_attempt(tmp_path):
    with pytest.raises(ExecutionError, match='must equal'):
        DemoAttempts(tmp_path, Fake()).mutate('different-operation', 'sendorder', PARAMS)
    assert list(tmp_path.iterdir()) == []


def test_readback_preserves_exact_raw_responses_and_rejects_overwrite(tmp_path):
    from kraken_execution import capture_readback, READS
    class Readback:
        def request(self, endpoint):
            return 200, raw(**{READS[endpoint]: {} if endpoint == 'accounts' else []}) + b'  '
    manifest = capture_readback(tmp_path / 'capture', Readback())
    assert set(manifest['responses']) == set(READS)
    for endpoint, metadata in manifest['responses'].items():
        content = (tmp_path / 'capture' / (endpoint + '.raw')).read_bytes()
        assert content.endswith(b'  ')
        assert hashlib.sha256(content).hexdigest() == metadata['sha256']
        assert len(content) == metadata['bytes']
    with pytest.raises(FileExistsError): capture_readback(tmp_path / 'capture', Readback())


def test_concurrent_attempt_cannot_send_twice(tmp_path):
    class Racing(Fake):
        def request(self, endpoint, params=None):
            if endpoint == 'sendorder':
                other = Fake()
                with pytest.raises(OutcomeUnknown):
                    DemoAttempts(tmp_path, other).mutate('forecast-1', 'sendorder', PARAMS)
                assert other.calls == []
            return super().request(endpoint, params)
    client = Racing()
    DemoAttempts(tmp_path, client).mutate('forecast-1', 'sendorder', PARAMS)
    assert [c[0] for c in client.calls] == ['openorders', 'sendorder']


def test_transport_returns_raw_http_error_and_enforces_byte_cap(monkeypatch):
    import kraken_execution
    client = DemoClient('synthetic-public', SECRET)
    class Response:
        status_code = 503
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b' exact bytes '
    monkeypatch.setattr(client._session, 'request', lambda *a, **kw: Response())
    assert client.request('accounts') == (503, b' exact bytes ')
    monkeypatch.setattr(kraken_execution, 'MAX_RESPONSE_BYTES', 3)
    with pytest.raises(ExecutionError, match='byte limit'): client.request('accounts')
