"""Synthetic account-log ID paging and cash-flow classification."""
from copy import deepcopy
import json
import pytest
from account_log import NUMERIC, capture_account_log, verify_account_log, cashflow_check
from kraken_execution import ExecutionError
from test_exchange_history import ACCOUNT, OTHER, START, END, DATE


def row(ident=1, at='2026-09-21T00:30:00.123456Z', **changes):
    result = {key: None for key in NUMERIC}
    result.update(id=ident, date=at, asset='USD', booking_uid=f'booking-{ident}', info='futures trade',
                  margin_account='flex', collateral='USD', contract='PF_DOTUSD', execution='fill-1',
                  old_balance='5000', new_balance='5000.7', fee='0.2', realized_funding='-0.1', realized_pnl='1')
    result.update(changes); return result


class Pages:
    def __init__(self, pages, account=ACCOUNT, date=DATE):
        self.pages, self.account, self.date, self.calls = pages, account, date, []
    def history(self, endpoint, params):
        assert endpoint == 'account-log'
        self.calls.append(dict(params))
        return 200, json.dumps({'accountUid': self.account, 'logs': self.pages[len(self.calls)-1]}).encode()+b' \n', {'Date': self.date}


def test_id_paging_keeps_same_timestamp_and_exact_cost_values(tmp_path):
    value = '0.12345678901234567890123456789'
    client = Pages([[row(1, fee=value)], [row(2)], []])
    root = tmp_path/'capture'; report = capture_account_log(root, client, ACCOUNT, START, END)
    assert report['coverage_complete'] and report['entry_count'] == 2
    assert [p.get('from') for p in client.calls] == [None, '2', '3']
    assert client.calls[0]['conversion_details'] is True and 'info' not in client.calls[0]
    assert report['entries'][0]['fee'] == value
    assert report['entries'][0]['at_utc'].endswith('.123456Z')
    assert report['entries'][0]['realized_funding'] == '-0.1'
    assert report['entries'][0]['funding_rate'] is None
    assert not report['actual_costs_verified'] and not report['authorizes_execution']
    assert (root/'0000.raw').read_bytes().endswith(b' \n')
    assert verify_account_log(root, ACCOUNT, START, END) == report


def test_short_page_without_empty_successor_is_incomplete(tmp_path):
    report = capture_account_log(tmp_path/'capture', Pages([[row()]]), ACCOUNT, START, END, max_pages=1)
    assert not report['coverage_complete']
    assert report['coverage_reasons'] == ['page_budget_exhausted']


@pytest.mark.parametrize('second', [row(), row(2, at='2026-09-21T00:20:00Z'), row(2, booking_uid='booking-1')])
def test_repeated_cursor_chronology_or_booking_is_rejected(tmp_path, second):
    with pytest.raises(ExecutionError):
        capture_account_log(tmp_path/'capture', Pages([[row()], [second]]), ACCOUNT, START, END)
    assert (tmp_path/'capture/0001.raw').exists()
    assert not (tmp_path/'capture/manifest.json').exists()


def test_account_time_and_request_bindings_are_verified(tmp_path):
    with pytest.raises(ExecutionError, match='different account'):
        capture_account_log(tmp_path/'wrong', Pages([[]], account=OTHER), ACCOUNT, START, END)
    with pytest.raises(ExecutionError, match='predates'):
        capture_account_log(tmp_path/'stale', Pages([[]], date='Mon, 21 Sep 2026 00:00:00 GMT'), ACCOUNT, START, END)
    root = tmp_path/'valid'; capture_account_log(root, Pages([[row()], []]), ACCOUNT, START, END)
    meta = json.loads((root/'0001.json').read_bytes()); meta['params']['from'] = '500'
    (root/'0001.json').write_text(json.dumps(meta))
    with pytest.raises(ExecutionError, match='binding changed'):
        verify_account_log(root, ACCOUNT, START, END)


def test_declared_external_or_unclassified_activity_blocks_clean_flow_claim(tmp_path):
    report = capture_account_log(tmp_path/'capture', Pages([[row(), row(2, info='deposit'),
        row(3, info='fee credit'), row(4, info='funding rate change')], []]), ACCOUNT, START, END)
    result = cashflow_check(report, START, END, [{'fill_id': 'fill-1'}])
    assert not result['verified_no_external_flows']
    assert result['issues'] == [{'kind': 'external_account_flow', 'entry_id': 2, 'info': 'deposit'},
                               {'kind': 'unclassified_account_activity', 'entry_id': 3, 'info': 'fee credit'}]
    clean = deepcopy(report); clean['entries'] = [report['entries'][0], report['entries'][3]]
    assert cashflow_check(clean, START, END, [{'fill_id': 'fill-1'}])['verified_no_external_flows']
    assert not cashflow_check(clean, START, END, [])['verified_no_external_flows']
    clean['coverage_complete'] = False
    assert cashflow_check(clean, START, END, [])['issues'][0]['kind'] == 'account_log_coverage_incomplete'
