"""One real public redirect fixture; successful quotes remain synthetic."""
import json
from pathlib import Path
import pytest
from demo_market import BASE, DemoMarketClient, capture_market, verify_market
from demo_evidence import capture_bundle
from kraken_execution import ExecutionError
from test_demo_evidence import Client, KEY, market_response
from test_exchange_history import ACCOUNT, START, END

FIXTURE = Path(__file__).parent/'fixtures/demo_market_redirect'


def test_real_public_redirect_fixture_replays_without_following_location():
    report = verify_market(FIXTURE)
    assert not report['available'] and report['reason'] == 'http_status_301'
    assert list(report['responses']) == ['tickers']
    assert report['responses']['tickers']['requested_url'] == BASE+'tickers'
    assert report['responses']['tickers']['headers']['Location'].startswith('https://www.kraken.com/')


def test_redirect_aborts_before_any_private_request(tmp_path):
    class Redirect(Client):
        def market(self, endpoint):
            self.calls.append(endpoint)
            meta = json.loads((FIXTURE/'tickers.json').read_bytes())
            return meta['http_status'], (FIXTURE/'tickers.raw').read_bytes(), meta['headers']
        def request(self, endpoint, params=None):
            pytest.fail('Private request must not follow failed public probe')
        def history(self, endpoint, params):
            pytest.fail('History request must not follow failed public probe')
    client = Redirect()
    with pytest.raises(ExecutionError, match='private requests not attempted'):
        capture_bundle(tmp_path/'bundle', client, ACCOUNT, KEY, START, END)
    assert client.calls == ['tickers']
    assert not (tmp_path/'bundle/bundle.json').exists()
    assert verify_market(tmp_path/'bundle/market')['reason'] == 'http_status_301'


def test_synthetic_market_replays_and_detects_tampered_raw(tmp_path):
    report = capture_market(tmp_path/'market', Client())
    assert report['available'] and report['market']['bid'] == '0.9998'
    assert verify_market(tmp_path/'market') == report
    (tmp_path/'market/tickers.raw').write_bytes(b'{}')
    with pytest.raises(ExecutionError, match='changed'):
        verify_market(tmp_path/'market')


@pytest.mark.parametrize('change', ['crossed', 'missing', 'duplicate', 'nan', 'html'])
def test_bad_public_payload_retains_evidence_without_available_claim(tmp_path, change):
    class Broken(Client):
        def market(self, endpoint):
            status, raw, headers = market_response(endpoint)
            if endpoint != 'tickers': return status, raw, headers
            if change == 'html': return status, b'<html>unavailable</html>', headers
            value = json.loads(raw)
            if change == 'crossed': value['tickers'][0]['bid'] = '2'
            if change == 'missing': value['tickers'] = []
            if change == 'duplicate': value['tickers'] *= 2
            if change == 'nan': value['tickers'][0]['ask'] = 'NaN'
            return status, json.dumps(value).encode(), headers
    root = tmp_path/'market'; report = capture_market(root, Broken())
    assert not report['available'] and (root/'tickers.raw').exists()
    assert verify_market(root) == report


def test_public_transport_never_attaches_credentials_or_follows_redirect(monkeypatch):
    calls = []
    class Response:
        status_code = 301
        headers = {'Location': 'https://www.kraken.com/gb/features/futures'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'redirect'
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert self.trust_env is False
            calls.append((url, kwargs))
            return Response()
    monkeypatch.setattr('demo_market.requests.Session', Session)
    assert DemoMarketClient().market('tickers')[0] == 301
    assert calls == [(BASE+'tickers', {'stream': True, 'allow_redirects': False, 'timeout': (5, 20)})]
    with pytest.raises(ExecutionError, match='Unsupported'):
        DemoMarketClient().market('sendorder')
