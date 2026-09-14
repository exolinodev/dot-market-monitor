import copy
import hashlib
import json
from pathlib import Path
import pandas as pd
import pytest
from coinbase import CoinbaseClient
from event_calendar import scheduled_events
from observations import cross_venue

FIXTURES = Path(__file__).parent/'fixtures'
ROOT = Path(__file__).parents[1]


def fixture(name):
    return json.loads((FIXTURES/(name+'.json')).read_text())


def test_real_coinbase_response_and_instrument_units():
    product, book = fixture('coinbase_product'), fixture('coinbase_book')
    quote = CoinbaseClient.parse_quote(product, book)
    assert quote['market_type'] == 'spot'
    assert quote['quote'] == 'USD'
    assert quote['midprice_usd'] == pytest.approx((float(book['bids'][0][0])+float(book['asks'][0][0]))/2)
    assert pd.Timestamp(quote['source_timestamp_utc']) == pd.Timestamp(book['time'])


@pytest.mark.parametrize('field,value', [('id', 'DOT-USDT'), ('quote_currency', 'USDT'),
    ('trading_disabled', True), ('auction_mode', True), ('status', 'offline')])
def test_coinbase_wrong_instrument_or_disabled_product_is_rejected(field, value):
    product = fixture('coinbase_product')
    product[field] = value
    with pytest.raises(ValueError): CoinbaseClient.parse_quote(product, fixture('coinbase_book'))


def test_coinbase_bad_book_timestamp_and_crossed_prices_are_rejected():
    product, book = fixture('coinbase_product'), fixture('coinbase_book')
    book['time'] = '2026-09-14T18:00:00'
    with pytest.raises(ValueError): CoinbaseClient.parse_quote(product, book)
    book = fixture('coinbase_book')
    book['asks'][0][0] = book['bids'][0][0]
    with pytest.raises(ValueError): CoinbaseClient.parse_quote(product, book)


def test_cross_venue_midpoints_require_fresh_comparable_observations():
    stamp = '2026-09-14T20:00:00Z'
    data = {'generated_at_utc': stamp, 'sources': {'DOTUSD.depth': {'fresh': True, 'received_at_utc': stamp}},
        'markets': {'DOTUSD': {'orderbook': {'best_bid': 9, 'best_ask': 11, 'midprice': 10,
            'spread_bps': 2000, 'best_bid_size': 2, 'best_ask_size': 3}}}}
    quote = CoinbaseClient.parse_quote(fixture('coinbase_product'), fixture('coinbase_book'))
    quote.update(midprice_usd=10.01, source_timestamp_utc=stamp)
    result = cross_venue(data, quote)
    assert result['data']['coinbase_minus_kraken_mid_bps'] == pytest.approx(10)
    quote['source_timestamp_utc'] = '2026-09-14T19:58:59Z'
    unaligned = cross_venue(data, quote)
    assert unaligned['data']['coinbase_minus_kraken_mid_bps'] is None
    assert unaligned['data']['timestamp_skew_seconds'] == 61
    quote['source_timestamp_utc'] = '2026-09-14T19:55:00Z'
    assert cross_venue(data, quote)['data']['coinbase'] is None


def test_fixture_hashes_and_verified_event_dates_have_official_source_evidence():
    manifest = fixture('observation_sources_manifest')
    for entry in manifest:
        assert hashlib.sha256((FIXTURES/entry['file']).read_bytes()).hexdigest() == entry['sha256']
    calendar = fixture('fomc_calendar_2026')
    assert 'September 15-16' in calendar['excerpt']
    assert 'October 27-28' in calendar['excerpt']
    assert 'December 8-9' in calendar['excerpt']
    assert calendar['source_url'].startswith('https://www.federalreserve.gov/')


def test_date_only_events_never_get_invented_instants_or_minute_countdowns():
    config = json.loads((ROOT/'config/scheduled_events.json').read_text())
    before = scheduled_events(config, '2026-09-15T03:59:59Z')['data'][0]
    during = scheduled_events(config, '2026-09-15T04:00:00Z')['data'][0]
    assert before['state'] == 'upcoming'
    assert during['state'] == 'active'
    assert during['event_time_utc'] is None
    assert during['minutes_until_event'] is None
    assert scheduled_events(config, '2026-09-14T18:00:00Z')['status'] == 'unavailable'


def test_exact_event_times_use_offsets_and_reference_without_wall_clock():
    config = {'version': 1, 'scope': 'fixture', 'events': [{
        'id': 'example', 'name': 'Example release', 'event_type': 'release', 'precision': 'instant',
        'event_time_utc': '2026-09-15T20:00:00+02:00', 'verified_at_utc': '2026-09-14T00:00:00Z',
        'source_url': 'https://example.test/calendar'}]}
    row = scheduled_events(config, '2026-09-15T17:30:00Z')['data'][0]
    assert row['event_time_utc'] == '2026-09-15T18:00:00Z'
    assert row['minutes_until_event'] == 30
    assert row['minutes_since_event'] is None
    assert scheduled_events(config, '2026-09-15T18:00:00Z')['data'][0]['state'] == 'active'
