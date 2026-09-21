"""Synthetic history API protocol cases; never use exchange credentials."""
from copy import deepcopy
import json

import pytest

from exchange_history import capture_executions, milliseconds
from kraken_execution import DemoClient, ExecutionError, authent, encoded_params
from test_kraken_execution import SECRET

ACCOUNT = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
START, END = '2026-09-21T00:00:00Z', '2026-09-21T01:00:00Z'
DATE = 'Mon, 21 Sep 2026 01:00:05 GMT'


def event(uid='e1', at='2026-09-21T00:30:00.123Z', account=ACCOUNT):
    stamp = milliseconds(at)
    return {'uid': uid, 'timestamp': stamp, 'event': {'execution': {'takerReducedQuantity': '',
        'execution': {'uid': 'fill-' + uid, 'timestamp': stamp, 'quantity': '2.00000000',
            'price': '1.12345678901234567890123456789', 'executionType': 'maker',
            'order': {'uid': 'order-1', 'accountUid': account, 'clientId': 'entry-1',
                      'tradeable': 'PF_DOTUSD', 'direction': 'Buy'}}}}}


def body(elements, token=None, account=ACCOUNT):
    result = {'accountUid': account, 'len': len(elements), 'elements': elements}
    if token is not None: result['continuationToken'] = token
    return result


class History:
    def __init__(self, pages): self.pages, self.calls = pages, []
    def history(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        contents, extra = self.pages[len(self.calls)-1]
        return 200, json.dumps(contents).encode() + b'  \n', {'Date': DATE, **extra}


def test_cursor_crosses_simultaneous_fills_with_exact_account_and_decimal_binding(tmp_path):
    client = History([(body([event('e1')], 'opaque+/='), {}), (body([event('e2')]), {})])
    report = capture_executions(tmp_path / 'capture', client, ACCOUNT, START, END)
    assert report['coverage_complete'] and report['fill_count'] == 2
    assert report['account_uid'] == ACCOUNT
    assert report['fills'][0]['price'] == '1.12345678901234567890123456789'
    assert report['fills'][0]['size'] == '2'
    assert client.calls[0] == ('executions', {'since': str(milliseconds(START)-1),
        'before': str(milliseconds(END)+1), 'sort': 'asc', 'count': '1000'})
    assert client.calls[1][1] == {**client.calls[0][1], 'continuation_token': 'opaque+/='}
    assert (tmp_path / 'capture/0000.raw').read_bytes().endswith(b'  \n')
    assert not report['actual_costs_verified'] and not report['authorizes_execution']


def test_header_token_and_overlap_are_supported_without_double_counting(tmp_path):
    client = History([(body([event('e1')]), {'Next-Continuation-Token': 'token'}),
                      (body([event('e1'), event('e2')]), {})])
    report = capture_executions(tmp_path / 'capture', client, ACCOUNT, START, END)
    assert report['fill_count'] == 2 and report['coverage_complete']


@pytest.mark.parametrize('where', ['page', 'nested'])
def test_account_substitution_fails_and_leaves_raw_evidence(tmp_path, where):
    contents = body([event(account=OTHER if where == 'nested' else ACCOUNT)], account=OTHER if where == 'page' else ACCOUNT)
    with pytest.raises(ExecutionError, match='different account'):
        capture_executions(tmp_path / 'capture', History([(contents, {})]), ACCOUNT, START, END)
    assert (tmp_path / 'capture/0000.raw').exists()
    assert not (tmp_path / 'capture/manifest.json').exists()


def test_token_conflict_and_cycle_fail_closed(tmp_path):
    with pytest.raises(ExecutionError, match='Conflicting continuation'):
        capture_executions(tmp_path / 'conflict', History([(body([], 'a'), {'Next-Continuation-Token': 'b'})]), ACCOUNT, START, END)
    client = History([(body([], 'a'), {}), (body([], 'a'), {})])
    with pytest.raises(ExecutionError, match='Repeated continuation'):
        capture_executions(tmp_path / 'cycle', client, ACCOUNT, START, END)


def test_budget_exhaustion_never_claims_complete_history(tmp_path):
    report = capture_executions(tmp_path / 'capture', History([(body([event()], 'a'), {})]), ACCOUNT, START, END, max_pages=1)
    assert not report['coverage_complete']
    assert report['coverage_reasons'] == ['page_budget_exhausted']


def test_conflicting_duplicate_and_descending_history_fail(tmp_path):
    changed = event(); changed['event']['execution']['execution']['price'] = '5'
    with pytest.raises(ExecutionError, match='Conflicting execution'):
        capture_executions(tmp_path / 'changed', History([(body([event()], 'a'), {}), (body([changed]), {})]), ACCOUNT, START, END)
    client = History([(body([event('later', '2026-09-21T00:40:00Z'), event('earlier')]), {})])
    with pytest.raises(ExecutionError, match='not ascending'):
        capture_executions(tmp_path / 'descending', client, ACCOUNT, START, END)


def test_window_edges_are_expanded_then_filtered_inclusively(tmp_path):
    rows = [event('before', '2026-09-20T23:59:59.999Z'), event('start', START),
            event('end', END), event('after', '2026-09-21T01:00:00.001Z')]
    report = capture_executions(tmp_path / 'capture', History([(body(rows), {})]), ACCOUNT, START, END)
    assert [f['fill_id'] for f in report['fills']] == ['fill-start', 'fill-end']
    with pytest.raises(ExecutionError, match='outside requested'):
        capture_executions(tmp_path / 'outside', History([(body([event(at='2026-09-21T02:00:00Z')]), {})]), ACCOUNT, START, END)


def test_stale_response_and_nonmillisecond_window_fail(tmp_path):
    with pytest.raises(ExecutionError, match='predates'):
        capture_executions(tmp_path / 'stale', History([(body([]), {'Date': 'Mon, 21 Sep 2026 00:59:00 GMT'})]), ACCOUNT, START, END)
    with pytest.raises(ExecutionError, match='align to milliseconds'):
        capture_executions(tmp_path / 'precision', History([]), ACCOUNT, START, '2026-09-21T01:00:00.000001Z')


def test_history_transport_signs_actual_history_path_and_preserves_selected_headers(monkeypatch):
    client = DemoClient('synthetic-public', SECRET)
    calls = []
    class Response:
        status_code = 200
        headers = {'Next-Continuation-Token': 'opaque+/=', 'Date': DATE, 'Secret-Header': 'do-not-store'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'raw history'
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs)); return Response()
    monkeypatch.setattr(client._session, 'request', request)
    params = {'continuation_token': 'opaque+/=', 'sort': 'asc'}
    status, raw, headers = client.history('executions', params)
    assert status == 200 and raw == b'raw history'
    assert headers == {'Next-Continuation-Token': 'opaque+/=', 'Date': DATE}
    method, url, kwargs = calls[0]
    encoded = encoded_params(params)
    assert method == 'GET' and url == 'https://demo-futures.kraken.com/api/history/v3/executions?' + encoded
    assert kwargs['headers']['Authent'] == authent(SECRET, encoded, '/api/history/v3/executions')
    assert kwargs['data'] is None and not kwargs['allow_redirects']
    with pytest.raises(ExecutionError): client.history('transfer', {})
    assert len(calls) == 1


def test_capture_replays_raw_evidence_against_independent_account_and_window(tmp_path):
    from exchange_history import verify_capture
    root = tmp_path / 'capture'
    original = capture_executions(root, History([(body([event()]), {})]), ACCOUNT, START, END)
    assert verify_capture(root, ACCOUNT, START, END) == original
    with pytest.raises(ExecutionError, match='different account'):
        verify_capture(root, OTHER, START, END)
    with pytest.raises(ExecutionError, match='request binding'):
        verify_capture(root, ACCOUNT, START, '2026-09-21T00:59:00Z')
    (root / '0000.raw').write_bytes(b'{}')
    with pytest.raises(ExecutionError, match='evidence or request'):
        verify_capture(root, ACCOUNT, START, END)


def test_derived_manifest_tamper_and_extra_artifacts_fail_replay(tmp_path):
    from exchange_history import verify_capture
    root = tmp_path / 'capture'
    original = capture_executions(root, History([(body([event()]), {})]), ACCOUNT, START, END)
    changed = deepcopy(original); changed['fills'][0]['size'] = '999'
    (root / 'manifest.json').write_text(json.dumps(changed))
    with pytest.raises(ExecutionError, match='differs from raw replay'):
        verify_capture(root, ACCOUNT, START, END)
    (root / 'manifest.json').write_text(json.dumps(original))
    (root / 'unexpected.json').write_text('{}')
    with pytest.raises(ExecutionError, match='Unexpected'):
        verify_capture(root, ACCOUNT, START, END)
