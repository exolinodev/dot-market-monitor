from copy import deepcopy
from datetime import timedelta
import json
import pytest
from cycles import utc, iso
from ledger import replay
from ledger_market import market_events, row_events, verify_market_input
from test_ledger import config, EPOCH


def quarter(start, end, quote_at, bps):
    stamps = range(int(utc(start).timestamp()), int(utc(end).timestamp()), 60)
    bars = [[stamp, 1, 1.002, .9998, 1, 0] for stamp in stamps]
    return {'schema_version': 1, 'quarter_start_utc': start,
            'meta': {'cycle_boundary_utc': end},
            'sources': {'perp_book': {'status': 'ok', 'received_at_utc': quote_at}},
            'perp_book': {'spread_bps': bps}, 'candles': {'trade': bars, 'mark': deepcopy(bars)},
            'ledger': {'status': 'unavailable'}}


def archive(tmp_path):
    epoch = '2026-09-20T20:00:08Z'
    rows = [quarter('2026-09-20T19:45:00Z', EPOCH, epoch, 3),
            quarter(EPOCH, '2026-09-20T20:15:00Z', '2026-09-20T20:15:08Z', 99)]
    intraday = tmp_path / 'intraday/2026/09/20.jsonl'
    intraday.parent.mkdir(parents=True)
    intraday.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    funding = tmp_path / 'funding/2026/09.jsonl'
    funding.parent.mkdir(parents=True)
    funding.write_text(json.dumps({'timestamp': EPOCH, 'fundingRate': '.00006', 'relativeFundingRate': '.00006'}) + '\n')
    return epoch, rows


def test_quote_after_boundary_never_reprices_preceding_candles(tmp_path):
    epoch, _ = archive(tmp_path)
    events = market_events(tmp_path, epoch, '2026-09-20T20:15:00Z')
    assert [e['type'] for e in events[:2]] == ['funding_rate', 'spread']
    spreads = [e for e in events if e['type'] == 'spread']
    assert len(spreads) == 1 and spreads[0]['spread_bps'] == '3'
    assert events[-1]['at_utc'] == '2026-09-20T20:14:00Z'
    state, _ = replay(config(), epoch, events)
    assert state['last_candle_utc'] == events[-1]['at_utc']
    assert state['funding_rate']['interval_start_utc'] == EPOCH
    for event in events:
        verify_market_input(tmp_path, epoch, event)
    later = market_events(tmp_path, epoch, '2026-09-20T20:30:00Z')
    assert [e['spread_bps'] for e in later if e['type'] == 'spread'] == ['3', '99']


def test_changed_price_cannot_hide_behind_valid_source_hash(tmp_path):
    epoch, _ = archive(tmp_path)
    event = market_events(tmp_path, epoch, '2026-09-20T20:15:00Z')[-1]
    event['trade']['close'] = '1.001'
    with pytest.raises(ValueError, match='differs from archived'):
        verify_market_input(tmp_path, epoch, event)


def test_account_summary_does_not_change_market_input_hash_or_create_cycle(tmp_path):
    epoch, rows = archive(tmp_path)
    before = row_events('intraday/2026/09/20.jsonl', rows[1], epoch)
    rows[1]['ledger'] = {'status': 'ok', 'state_sha256': 'a' * 64, 'equity_usd': '5000'}
    assert row_events('intraday/2026/09/20.jsonl', rows[1], epoch) == before
    rows[1]['candles']['mark'].pop()
    with pytest.raises(ValueError, match='not aligned'):
        row_events('intraday/2026/09/20.jsonl', rows[1], epoch)
