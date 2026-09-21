"""Synthetic API-shaped evidence; no authenticated exchange fixture claims."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from cycles import utc, iso
from exchange_reconciliation import capture_fills, deduplicate_fills, reconcile
from kraken_execution import ExecutionError

START = '2026-09-21T00:00:00Z'
END = '2026-09-21T01:00:00Z'


def fill(ident='f1', **changes):
    row = {'fill_id': ident, 'order_id': 'entry-exchange', 'cliOrdId': 'entry',
           'fillTime': '2026-09-21T00:30:00.123Z', 'symbol': 'PF_DOTUSD',
           'side': 'buy', 'size': '2', 'price': '1.1', 'fillType': 'maker'}
    row.update(changes)
    return row


class Pages:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
    def request(self, endpoint, params):
        self.calls.append((endpoint, params))
        return 200, json.dumps({'result': 'success', 'serverTime': END, 'fills': self.pages[len(self.calls)-1]}).encode() + b'\n '


def saturated():
    # Oldest boundary is inside the requested window, including milliseconds.
    return [fill('f' + str(i), fillTime=iso(utc('2026-09-21T00:30:00.123Z') + timedelta(seconds=i))) for i in range(100)]


def known():
    return {'entry': {'symbol': 'PF_DOTUSD', 'side': 'buy', 'size': '10',
             'exchange_order_id': 'entry-exchange', 'paper_fill_price_usd': '1', 'reduceOnly': False},
            'exit': {'symbol': 'PF_DOTUSD', 'side': 'sell', 'size': '10',
             'exchange_order_id': 'exit-exchange', 'reduceOnly': True}}


def capture(rows, complete=True):
    return {'fills': rows, 'coverage_complete': complete}


def test_short_page_proves_window_and_keeps_raw_bytes(tmp_path):
    client = Pages([[fill(), fill('old', fillTime='2026-09-20T23:00:00Z')]])
    report = capture_fills(tmp_path / 'capture', client, START, END)
    assert report['coverage_complete'] and report['fill_count'] == 1
    assert report['fills'][0]['fillTime'].endswith('.123000Z')
    raw = (tmp_path / 'capture/0000.raw').read_bytes()
    assert raw.endswith(b'\n ')
    assert hashlib.sha256(raw).hexdigest() == report['pages'][0]['sha256']
    assert len(raw) == report['pages'][0]['bytes']
    with pytest.raises(FileExistsError): capture_fills(tmp_path / 'capture', client, START, END)
    assert len(client.calls) == 1


def test_paginated_timestamp_boundary_stays_incomplete_even_after_reaching_start(tmp_path):
    client = Pages([saturated(), [fill('older', fillTime='2026-09-20T23:59:59Z')]])
    report = capture_fills(tmp_path / 'capture', client, START, END)
    assert client.calls[1] == ('fills', {'lastFillTime': '2026-09-21T00:30:00.123000Z'})
    assert report['fill_count'] == 100
    assert not report['coverage_complete']
    assert report['coverage_reasons'] == ['timestamp_boundary_ambiguous']


def test_full_page_already_crossing_window_needs_no_ambiguous_cursor(tmp_path):
    rows = saturated()
    rows[0]['fillTime'] = '2026-09-20T23:59:59Z'
    client = Pages([rows])
    report = capture_fills(tmp_path / 'capture', client, START, END)
    assert report['coverage_complete'] and len(client.calls) == 1


def test_page_budget_and_nonprogress_are_not_complete(tmp_path):
    report = capture_fills(tmp_path / 'budget', Pages([saturated()]), START, END, max_pages=1)
    assert 'page_budget_exhausted' in report['coverage_reasons']
    assert not report['coverage_complete']
    with pytest.raises(ExecutionError, match='before-cursor'):
        capture_fills(tmp_path / 'stalled', Pages([saturated(), saturated()]), START, END)
    assert not (tmp_path / 'stalled/manifest.json').exists()
    assert (tmp_path / 'stalled/0001.raw').exists()


def test_response_must_cover_requested_end_and_not_contain_future_fills(tmp_path):
    with pytest.raises(ExecutionError, match='predates'):
        capture_fills(tmp_path / 'stale', Pages([[]]), START, '2026-09-21T02:00:00Z')
    with pytest.raises(ExecutionError, match='newer than response'):
        capture_fills(tmp_path / 'future', Pages([[fill(fillTime='2026-09-21T02:00:00Z')]]), START, END)


def test_fill_dedup_ignores_optional_historical_pnl_but_rejects_price_change():
    first = fill(realized_pnl='4')
    historical = fill(price='1.1000', realized_pnl=None, cliOrdId=None)
    assert deduplicate_fills([first, historical]) == deduplicate_fills([first])
    with pytest.raises(ExecutionError, match='Conflicting fill identity'):
        deduplicate_fills([first, fill(price='1.2')])
    with pytest.raises(ExecutionError, match='client ID'):
        deduplicate_fills([first, fill(cliOrdId='another')])


def test_partial_fills_weighted_average_exit_and_baseline_reconcile():
    rows = [fill(size='2', price='1'), fill('f2', size='3', price='1.2'),
            fill('f3', order_id='exit-exchange', cliOrdId='exit', side='sell', size='1', price='1.3')]
    report = reconcile(capture(rows), known(), [], [{'symbol': 'PF_DOTUSD', 'side': 'long', 'size': '6'}], starting_quantity='2')
    assert report['quantity_reconciled']
    assert report['expected_signed_quantity'] == '6'
    assert report['orders']['entry']['filled_quantity'] == '5'
    assert report['orders']['entry']['average_fill_price_usd'] == '1.12'
    assert report['orders']['entry']['paper_adverse_difference_bps'] == '1200'
    assert not report['actual_costs_verified'] and report['exchange_net_pnl_usd'] is None
    assert not report['authorizes_execution']


def test_unknown_fill_or_order_cannot_hide_behind_matching_position():
    rows = [fill(cliOrdId='external')]
    opened = [{'symbol': 'PF_DOTUSD', 'cliOrdId': 'manual', 'order_id': 'manual-id'}]
    report = reconcile(capture(rows), known(), opened, [{'symbol': 'PF_DOTUSD', 'side': 'long', 'size': '2'}], starting_quantity='0')
    assert {i['kind'] for i in report['issues']} == {'unowned_fill', 'orphan_open_order'}
    assert not report['quantity_reconciled']


def test_missing_client_id_resolves_only_from_known_exchange_order_id():
    report = reconcile(capture([fill(cliOrdId=None)]), known(), [], [{'symbol': 'PF_DOTUSD', 'side': 'long', 'size': '2'}], starting_quantity='0')
    assert report['quantity_reconciled']
    assert report['orders']['entry']['fill_ids'] == ['f1']


def test_overfill_wrong_exchange_id_and_incomplete_coverage_are_reported():
    rows = [fill(size='11', order_id='other-exchange')]
    report = reconcile(capture(rows, False), known(), [], [], starting_quantity='0')
    assert {i['kind'] for i in report['issues']} == {'fill_intent_mismatch', 'filled_quantity_exceeds_intent',
        'position_quantity_mismatch', 'fill_coverage_incomplete'}


def test_wrong_reduce_only_flag_and_duplicate_open_orders_fail():
    opened = {'symbol': 'PF_DOTUSD', 'cliOrdId': 'exit', 'order_id': 'exit-exchange',
              'side': 'sell', 'unfilledSize': '10', 'filledSize': '0', 'reduceOnly': False}
    report = reconcile(capture([]), known(), [opened, deepcopy(opened)], [], starting_quantity='0')
    assert {i['kind'] for i in report['issues']} == {'open_order_intent_mismatch', 'duplicate_open_client_id'}


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-1', True])
def test_invalid_fill_sizes_rejected(bad):
    with pytest.raises(ExecutionError): deduplicate_fills([fill(size=bad)])


def test_cursor_precision_is_never_silently_truncated():
    with pytest.raises(ExecutionError, match='precision'):
        deduplicate_fills([fill(fillTime='2026-09-21T00:30:00.123456789Z')])
