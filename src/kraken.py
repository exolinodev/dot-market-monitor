from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import pandas as pd
from common import PublicHTTP, finite
from timeframes import validate_candles
from orderflow import validate_trades, validate_book

SPOT_BASE = "https://api.kraken.com/0/public"
FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
FUTURES_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1"


class KrakenError(RuntimeError):
    pass


@dataclass
class KrakenClient:
    timeout: int = 20
    user_agent: str = "dot-market-monitor/1.0"

    def __post_init__(self) -> None:
        self.http = PublicHTTP(timeout=self.timeout)
        self.session = self.http.session
        self._pairs = None

    def _get_json(self, url: str, params: Optional[dict] = None, retries: int = 2) -> Dict[str, Any]:
        try:
            return self.http.get(url, params)
        except RuntimeError as exc:
            raise KrakenError(str(exc)) from exc

    @staticmethod
    def _spot_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        errors = payload.get("error") or []
        if errors:
            raise KrakenError(f"Kraken spot API error: {errors}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise KrakenError("Unexpected Kraken spot response")
        return result

    @staticmethod
    def _single_market_result(result: Dict[str, Any]) -> Any:
        keys = [k for k in result.keys() if k != "last"]
        if not keys:
            raise KrakenError("No market key in Kraken response")
        return result[keys[0]]

    def server_time(self) -> Dict[str, Any]:
        payload = self._get_json(f"{SPOT_BASE}/Time")
        result = self._spot_result(payload)
        unix = int(result["unixtime"])
        return {
            "unix": unix,
            "utc": datetime.fromtimestamp(unix, tz=timezone.utc).isoformat(),
            "rfc1123": result.get("rfc1123"),
        }

    def spot_ticker(self, pair: str) -> Dict[str, Any]:
        payload = self._get_json(f"{SPOT_BASE}/Ticker", params={"pair": pair})
        result = self._spot_result(payload)
        t = self._single_market_result(result)
        out = {
            "last": float(t["c"][0]),
            "last_volume": float(t["c"][1]),
            "bid": float(t["b"][0]),
            "ask": float(t["a"][0]),
            "open_today": float(t["o"]),
            "high_today": float(t["h"][0]),
            "high_24h": float(t["h"][1]),
            "low_today": float(t["l"][0]),
            "low_24h": float(t["l"][1]),
            "volume_today": float(t["v"][0]),
            "volume_24h": float(t["v"][1]),
            "vwap_today": float(t["p"][0]),
            "vwap_24h": float(t["p"][1]),
            "trades_today": int(t["t"][0]),
            "trades_24h": int(t["t"][1]),
        }
        for field in ['last','bid','ask','high_24h','low_24h']:
            if finite(out[field]) is None or out[field]<=0:
                raise KrakenError('Implausible spot ticker price')
        if out['bid']>out['ask'] or out['low_24h']>out['high_24h']:
            raise KrakenError('Implausible spot ticker range')
        return out

    @staticmethod
    def parse_spot_trades(payload):
        result=KrakenClient._spot_result(payload)
        rows=KrakenClient._single_market_result(result)
        records=[]
        for row in rows:
            if len(row)<6: raise KrakenError('Invalid spot trade row')
            records.append({'price':float(row[0]),'volume':float(row[1]),
                            'time':pd.to_datetime(float(row[2]),unit='s',utc=True),'side':row[3],
                            'order_type':row[4],'misc':row[5],
                            'trade_id':str(row[6]) if len(row)>6 else repr(row)})
        return validate_trades(pd.DataFrame(records,columns=['price','volume','time','side','order_type','misc','trade_id']).sort_values('time'))

    def recent_trades(self, pair: str) -> pd.DataFrame:
        return self.parse_spot_trades(self._get_json(f"{SPOT_BASE}/Trades", params={"pair":pair}))

    def trade_tape(self, pair, now, hours=4, max_pages=30):
        start=pd.Timestamp(now)-pd.Timedelta(hours=hours)
        cursor=str(int(start.timestamp()*1_000_000_000))
        frames=[]
        complete=False
        pagination_error=None
        for _ in range(max_pages):
            try:
                payload=self._get_json(f"{SPOT_BASE}/Trades",params={'pair':pair,'since':cursor,'count':1000})
                result=self._spot_result(payload)
                frame=self.parse_spot_trades(payload)
            except Exception as exc:
                if not frames: raise
                pagination_error=str(exc)
                break
            frames.append(frame)
            next_cursor=str(result['last'])
            if len(frame)<1000 or (not frame.empty and frame.time.max()>=pd.Timestamp(now)):
                complete=True
                break
            if next_cursor==cursor: break
            cursor=next_cursor
        tape=pd.concat(frames).drop_duplicates('trade_id').sort_values('time').reset_index(drop=True)
        tape.attrs={'coverage_start':start.isoformat(),
                    'coverage_end':pd.Timestamp(now).isoformat() if complete else (tape.time.max().isoformat() if not tape.empty else start.isoformat()),
                    'pagination_complete':complete,'pages':len(frames),'pagination_error':pagination_error}
        return tape

    def spread(self, pair: str) -> pd.DataFrame:
        payload = self._get_json(f"{SPOT_BASE}/Spread", params={"pair": pair})
        result = self._spot_result(payload)
        rows = self._single_market_result(result)
        records = [
            {
                "time": pd.to_datetime(int(r[0]), unit="s", utc=True),
                "bid": float(r[1]),
                "ask": float(r[2]),
            }
            for r in rows
        ]
        return pd.DataFrame(records).sort_values("time") if records else pd.DataFrame(columns=["time", "bid", "ask"])

    def order_book(self, pair: str, count: int = 100) -> Dict[str, Any]:
        payload = self._get_json(f"{SPOT_BASE}/Depth", params={"pair": pair, "count": count})
        result = self._spot_result(payload)
        book = self._single_market_result(result)
        bids = [{"price": float(x[0]), "volume": float(x[1]), "time": int(x[2])} for x in book["bids"]]
        asks = [{"price": float(x[0]), "volume": float(x[1]), "time": int(x[2])} for x in book["asks"]]
        return validate_book({"bids": bids, "asks": asks})

    def ohlc(self, pair: str, interval: int) -> pd.DataFrame:
        payload = self._get_json(f"{SPOT_BASE}/OHLC", params={"pair": pair, "interval": interval})
        result = self._spot_result(payload)
        rows = self._single_market_result(result)
        columns = ["time", "open", "high", "low", "close", "vwap", "volume", "trade_count"]
        df = pd.DataFrame(rows, columns=columns)
        if df.empty:
            raise KrakenError(f"No OHLC data for {pair} interval={interval}")
        df["time"] = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
        for c in ["open", "high", "low", "close", "vwap", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["trade_count"] = pd.to_numeric(df["trade_count"], errors="coerce").astype("Int64")
        return validate_candles(df.set_index("time").sort_index())

    def asset_pairs(self) -> Dict[str, Any]:
        if self._pairs is None:
            self._pairs = self._spot_result(self._get_json(f"{SPOT_BASE}/AssetPairs"))
        return self._pairs

    def resolve_altname(self, wsname: str) -> str:
        target = wsname.upper()
        pairs = self.asset_pairs()
        for _, meta in pairs.items():
            if str(meta.get("wsname", "")).upper() == target:
                return str(meta.get("altname"))
        raise KrakenError(f"Could not resolve Kraken pair for {wsname}")

    def futures_ticker(self, symbol: str = 'PF_DOTUSD'):
        payload=self._get_json(f'{FUTURES_BASE}/tickers/{symbol}')
        if payload.get('result')!='success' or not isinstance(payload.get('ticker'),dict):
            raise KrakenError('Invalid futures ticker envelope')
        ticker=payload['ticker']
        if ticker.get('symbol')!=symbol: raise KrakenError('Wrong futures symbol')
        # This map contains only fields observed in the fixture and official schema.
        fields={'mark_price':'markPrice','last':'last','index_price':'indexPrice','bid':'bid','ask':'ask',
                'bid_size':'bidSize','ask_size':'askSize','high_24h':'high24h','low_24h':'low24h',
                'volume_24h':'vol24h','volume_quote_24h':'volumeQuote','open_interest':'openInterest',
                'open_24h':'open24h','vwap_24h':'vwap24h','last_size':'lastSize','funding_rate':'fundingRate',
                'funding_rate_prediction':'fundingRatePrediction','change_24h_pct':'change24h'}
        out={name:None if ticker.get(key) is None else float(ticker[key]) for name,key in fields.items()}
        if any(value is not None and finite(value) is None for value in out.values()):
            raise KrakenError('Nonfinite futures ticker field')
        if finite(out['mark_price']) is None or out['mark_price']<=0: raise KrakenError('Invalid markPrice')
        out.update({'symbol':symbol,'server_time_utc':pd.Timestamp(payload['serverTime']).isoformat(),
                    'last_time_utc':ticker.get('lastTime'),'suspended':ticker.get('suspended'),
                    'post_only':ticker.get('postOnly'),'tag':ticker.get('tag'),'pair':ticker.get('pair'),
                    'field_status':{name:'ok' if ticker.get(key) is not None else 'unavailable' for name,key in fields.items()},
                    'funding_rate_unit':'absolute API rate; not a percentage',
                    'observed_api_fields':sorted(ticker),'raw':ticker})
        return out

    def futures_instrument(self, symbol='PF_DOTUSD'):
        payload=self._get_json(f'{FUTURES_BASE}/instruments')
        result=next((x for x in payload['instruments'] if x['symbol']==symbol),None)
        if result is None: raise KrakenError('Futures instrument unavailable')
        if result.get('type')!='flexible_futures' or result.get('base')!='DOT' or result.get('quote')!='USD' or result.get('contractSize')!=1:
            raise KrakenError('Unsupported contract units')
        return result

    def futures_orderbook(self, symbol='PF_DOTUSD'):
        payload=self._get_json(f'{FUTURES_BASE}/orderbook',params={'symbol':symbol})
        book=payload['orderBook']
        parsed={side:[{'price':float(x[0]),'volume':float(x[1])} for x in book[side]] for side in ['bids','asks']}
        parsed['server_time_utc']=payload['serverTime']
        return validate_book(parsed)

    @staticmethod
    def parse_futures_trades(payload):
        if payload.get('result')!='success' or not isinstance(payload.get('history'),list):
            raise KrakenError('Invalid futures trade envelope')
        records=[]
        for x in payload['history']:
            # uid is stable; observed trade_id is a page-relative index.
            if x['side'] not in ['buy','sell']: raise KrakenError('Unknown futures taker side')
            records.append({'price':float(x['price']),'volume':float(x['size']),
                            'time':pd.Timestamp(x['time']), 'side':'b' if x['side']=='buy' else 's',
                            'trade_id':x['uid'],'type':x['type']})
        return validate_trades(pd.DataFrame(records,columns=['price','volume','time','side','trade_id','type']).sort_values('time'))

    def futures_trade_tape(self, now, symbol='PF_DOTUSD', hours=4, max_pages=30):
        start=pd.Timestamp(now)-pd.Timedelta(hours=hours)
        frames=[]
        last_time=None
        complete=False
        pagination_error=None
        for _ in range(max_pages):
            params={'symbol':symbol}
            if last_time is not None: params['lastTime']=last_time
            try:
                payload=self._get_json(f'{FUTURES_BASE}/history',params=params)
                frame=self.parse_futures_trades(payload)
            except Exception as exc:
                if not frames: raise
                pagination_error=str(exc)
                break
            frames.append(frame)
            if frame.empty: break
            oldest=frame.time.min()
            if oldest<=start:
                complete=True
                break
            next_time=oldest.isoformat()
            if next_time==last_time: break
            last_time=next_time
        tape=pd.concat(frames).drop_duplicates('trade_id').sort_values('time').reset_index(drop=True)
        tape.attrs={'coverage_start':start.isoformat() if complete else (tape.time.min().isoformat() if not tape.empty else pd.Timestamp(now).isoformat()),
                    'coverage_end':pd.Timestamp(now).isoformat(),'pagination_complete':complete,
                    'pages':len(frames),'pagination_error':pagination_error}
        return tape
