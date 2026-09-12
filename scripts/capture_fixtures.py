"""Capture public exchange responses; no account data or authentication."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

DEST = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
ENDPOINTS = {
 'spot_ticker': ('https://api.kraken.com/0/public/Ticker', {'pair':'DOTUSD'}),
 'spot_ohlc': ('https://api.kraken.com/0/public/OHLC', {'pair':'DOTUSD','interval':60}),
 'spot_trades': ('https://api.kraken.com/0/public/Trades', {'pair':'DOTUSD'}),
 'spot_depth': ('https://api.kraken.com/0/public/Depth', {'pair':'DOTUSD','count':100}),
 'spot_spread': ('https://api.kraken.com/0/public/Spread', {'pair':'DOTUSD'}),
 'futures_ticker': ('https://futures.kraken.com/derivatives/api/v3/tickers/PF_DOTUSD', {}),
 'futures_book': ('https://futures.kraken.com/derivatives/api/v3/orderbook', {'symbol':'PF_DOTUSD'}),
 'futures_trades': ('https://futures.kraken.com/derivatives/api/v3/history', {'symbol':'PF_DOTUSD'}),
 'futures_instruments': ('https://futures.kraken.com/derivatives/api/v3/instruments', {}),
 'coingecko_global': ('https://api.coingecko.com/api/v3/global', {}),
 'coingecko_markets': ('https://api.coingecko.com/api/v3/coins/markets', {'vs_currency':'usd','ids':'ethereum,binancecoin,ripple,solana,dogecoin,cardano,chainlink,avalanche-2,polkadot','price_change_percentage':'1h,24h,7d','sparkline':'false'}),
}
manifest = {}
for name, (url, params) in ENDPOINTS.items():
 r=requests.get(url,params=params,timeout=20)
 print(name,r.status_code)
 r.raise_for_status()
 payload=r.json()
 # Keep exact public records, reducing the instrument catalogue to DOT.
 if name=='futures_instruments': payload['instruments']=[x for x in payload['instruments'] if x['symbol']=='PF_DOTUSD']
 (DEST / f'{name}.json').write_text(json.dumps(payload, separators=(',',':'))+'\n')
 manifest[name]={'url':r.url,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'anonymization':'none needed: public market data only'}
 print(str(payload)[:2800] if name.startswith('futures') else str(payload)[:300])
(DEST / 'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
