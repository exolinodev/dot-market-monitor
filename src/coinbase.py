"""Public, source-timestamped Coinbase DOT/USD level-one quote; no credentials."""
from common import PublicHTTP, finite
from observation_common import utc, iso

BASE = 'https://api.exchange.coinbase.com'
QUOTE_URL = BASE + '/products/DOT-USD/book?level=1'
PRODUCT_URL = BASE + '/products/DOT-USD'


class CoinbaseClient:
    def __init__(self, http=None):
        self.http = http or PublicHTTP(timeout=8, retries=1)

    @staticmethod
    def parse_quote(product, book):
        if (product.get('id') != 'DOT-USD' or product.get('base_currency') != 'DOT'
                or product.get('quote_currency') != 'USD' or product.get('status') != 'online'
                or product.get('trading_disabled') is not False or product.get('auction_mode') is not False
                or book.get('auction_mode') is not False):
            raise ValueError('Active DOT/USD spot product required')
        bid, bid_size = (float(x) for x in book['bids'][0][:2])
        ask, ask_size = (float(x) for x in book['asks'][0][:2])
        if (any(finite(x) is None for x in (bid, ask, bid_size, ask_size))
                or not 0 < bid < ask or min(bid_size, ask_size) < 0):
            raise ValueError('Invalid Coinbase quote')
        mid = (bid + ask) / 2
        return {'venue': 'coinbase', 'market': 'DOTUSD', 'market_type': 'spot',
                'base': 'DOT', 'quote': 'USD', 'bid_usd': bid, 'ask_usd': ask,
                'bid_size_dot': bid_size, 'ask_size_dot': ask_size, 'midprice_usd': mid,
                'spread_bps': (ask - bid) / mid * 10000,
                'source_timestamp_utc': iso(utc(book['time'])),
                'instrument_source_url': PRODUCT_URL}

    def quote(self):
        product = self.http.get(PRODUCT_URL)
        book = self.http.get(QUOTE_URL)
        return self.parse_quote(product, book)
