from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from common import PublicHTTP, finite
from analytics import breadth_statistics


@dataclass
class CoinGeckoClient:
    timeout: int = 12
    base_url: str = 'https://api.coingecko.com/api/v3'

    def __post_init__(self):
        self.http=PublicHTTP(timeout=self.timeout)
        self.session=self.http.session
        key=os.getenv('COINGECKO_API_KEY')
        if key: self.session.headers.update({'x-cg-demo-api-key':key})

    def _get(self,path,params=None):
        return self.http.get(self.base_url+path,params)

    def global_market(self):
        data=self._get('/global')['data']
        total=float(data['total_market_cap']['usd'])
        btc=float(data['market_cap_percentage']['btc'])
        eth=float(data['market_cap_percentage']['eth'])
        if not (total>0 and 0<=btc<=100 and 0<=eth<=100 and btc+eth<=100):
            raise ValueError('Implausible global market response')
        return {'source_timestamp_utc':datetime.fromtimestamp(data['updated_at'],tz=timezone.utc).isoformat(),
                'btc_dominance':btc,'eth_dominance':eth,'total_market_cap_usd':total,
                'total_volume_usd':float(data['total_volume']['usd']),
                'btc_market_cap_proxy_usd':total*btc/100,'eth_market_cap_proxy_usd':total*eth/100,
                'total3_proxy_usd':total*(1-(btc+eth)/100),
                'market_cap_change_24h_pct':float(data['market_cap_change_percentage_24h_usd']),
                'active_cryptocurrencies':int(data['active_cryptocurrencies'])}

    def breadth(self):
        ids=['ethereum','binancecoin','ripple','solana','dogecoin','cardano','chainlink','avalanche-2','polkadot']
        rows=self._get('/coins/markets',{'vs_currency':'usd','ids':','.join(ids),
                                        'price_change_percentage':'1h,24h,7d','sparkline':'false'})
        if not isinstance(rows,list): raise ValueError('Expected coin market rows')
        coins={}
        for row in rows:
            if row['id'] not in ids: continue
            price=finite(row['current_price'])
            if price is None or price<=0: raise ValueError('Invalid coin price')
            symbol=row['symbol'].upper()
            coins[symbol]={'price_usd':price,'source_timestamp_utc':row['last_updated'],
                           'market_cap_usd':finite(row.get('market_cap')),'volume_24h_usd':finite(row.get('total_volume')),
                           'returns_pct':{p:finite(row.get(f'price_change_percentage_{p}_in_currency')) for p in ['1h','24h','7d']}}
        return breadth_statistics(coins)
