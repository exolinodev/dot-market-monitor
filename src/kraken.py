from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import requests

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
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "application/json"})

    def _get_json(self, url: str, params: Optional[dict] = None, retries: int = 2) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise KrakenError(f"GET failed for {url}: {last_exc}")

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
        return {
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

    def recent_trades(self, pair: str) -> pd.DataFrame:
        payload = self._get_json(f"{SPOT_BASE}/Trades", params={"pair": pair})
        result = self._spot_result(payload)
        rows = self._single_market_result(result)
        records = []
        for row in rows:
            records.append(
                {
                    "price": float(row[0]),
                    "volume": float(row[1]),
                    "time": pd.to_datetime(float(row[2]), unit="s", utc=True),
                    "side": row[3],
                    "order_type": row[4],
                    "misc": row[5],
                    "trade_id": row[6] if len(row) > 6 else None,
                }
            )
        if not records:
            return pd.DataFrame(columns=["price", "volume", "time", "side", "order_type", "misc", "trade_id"])
        df = pd.DataFrame(records).sort_values("time")
        return df

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
        return {"bids": bids, "asks": asks}

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
        return df.set_index("time").sort_index()

    def asset_pairs(self) -> Dict[str, Any]:
        payload = self._get_json(f"{SPOT_BASE}/AssetPairs")
        return self._spot_result(payload)

    def resolve_altname(self, wsname: str) -> str:
        target = wsname.upper()
        pairs = self.asset_pairs()
        for _, meta in pairs.items():
            if str(meta.get("wsname", "")).upper() == target:
                return str(meta.get("altname"))
        raise KrakenError(f"Could not resolve Kraken pair for {wsname}")

    def futures_ticker(self, symbol: str = "PF_DOTUSD") -> Dict[str, Any]:
        try:
            payload = self._get_json(f"{FUTURES_BASE}/tickers/{symbol}")
            tickers = payload.get("tickers")
            if isinstance(tickers, list) and tickers:
                ticker = tickers[0]
            elif isinstance(payload.get("ticker"), dict):
                ticker = payload["ticker"]
            else:
                ticker = payload
        except KrakenError:
            payload = self._get_json(f"{FUTURES_BASE}/tickers")
            tickers = payload.get("tickers") or []
            matches = [x for x in tickers if x.get("symbol") == symbol]
            if not matches:
                raise KrakenError(f"Futures symbol not found: {symbol}")
            ticker = matches[0]

        server_time = payload.get("serverTime") or ticker.get("serverTime")
        if not server_time:
            raise KrakenError("Futures ticker missing serverTime")
        ts = pd.Timestamp(server_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")

        def f(name: str) -> Optional[float]:
            value = ticker.get(name)
            return None if value is None else float(value)

        return {
            "symbol": ticker.get("symbol", symbol),
            "server_time_utc": ts.isoformat(),
            "server_time_unix": ts.timestamp(),
            "mark_price": f("markPrice"),
            "last": f("last"),
            "bid": f("bid"),
            "ask": f("ask"),
            "high_24h": f("high24h"),
            "low_24h": f("low24h"),
            "volume_24h": f("vol24h") if ticker.get("vol24h") is not None else f("volume24h"),
            "open_24h": f("open24h"),
            "change_24h_pct": f("change24h"),
            "index_price": f("indexPrice"),
            "open_interest": f("openInterest"),
            "funding_rate": f("fundingRate"),
            "funding_rate_prediction": f("fundingRatePrediction"),
            "raw": ticker,
        }

    def futures_mark_candles(self, symbol: str = "PF_DOTUSD", resolution: str = "1m") -> pd.DataFrame:
        payload = self._get_json(f"{FUTURES_CHARTS_BASE}/mark/{symbol}/{resolution}")
        candles = payload.get("candles") or payload.get("data") or []
        if not candles:
            raise KrakenError("No futures mark candles returned")
        records = []
        for c in candles:
            if isinstance(c, dict):
                ts = c.get("time") or c.get("timestamp")
                records.append(
                    {
                        "time": pd.to_datetime(ts, unit="ms" if float(ts) > 10_000_000_000 else "s", utc=True),
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                    }
                )
            else:
                ts = c[0]
                records.append(
                    {
                        "time": pd.to_datetime(ts, unit="ms" if float(ts) > 10_000_000_000 else "s", utc=True),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                    }
                )
        return pd.DataFrame(records).set_index("time").sort_index()


def data_age_seconds(ts_utc: Any, now_utc: Optional[datetime] = None) -> float:
    now = now_utc or datetime.now(timezone.utc)
    ts = pd.Timestamp(ts_utc)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return max(0.0, now.timestamp() - ts.timestamp())


def trade_flow(
    df: pd.DataFrame, windows_minutes: Iterable[int] = (5, 15, 60), now_utc: Optional[datetime] = None
) -> Dict[str, Any]:
    if df.empty:
        return {}
    latest = df["time"].max()
    earliest = df["time"].min()
    now = pd.Timestamp(now_utc or datetime.now(timezone.utc))
    out: Dict[str, Any] = {
        "asof_utc": now.isoformat(),
        "earliest_trade_utc": earliest.isoformat(),
        "latest_trade_utc": latest.isoformat(),
        "latest_price": float(df.loc[df["time"].idxmax(), "price"]),
    }
    for minutes in windows_minutes:
        start = now - pd.Timedelta(minutes=minutes)
        w = df[(df["time"] >= start) & (df["time"] <= now)]
        buy = float(w.loc[w["side"] == "b", "volume"].sum())
        sell = float(w.loc[w["side"] == "s", "volume"].sum())
        total = buy + sell
        out[f"{minutes}m"] = {
            "buy_volume": buy,
            "sell_volume": sell,
            "delta": buy - sell,
            "buy_share": None if total == 0 else buy / total,
            "trade_count": int(len(w)),
            "window_complete": bool(earliest <= start),
        }
    return out


def orderbook_metrics(book: Dict[str, Any], mid: Optional[float] = None) -> Dict[str, Any]:
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return {}
    best_bid = max(x["price"] for x in bids)
    best_ask = min(x["price"] for x in asks)
    midpoint = mid or (best_bid + best_ask) / 2

    out: Dict[str, Any] = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": (best_ask - best_bid) / midpoint * 10_000,
    }

    for pct in (0.25, 0.5, 1.0, 2.0):
        lower = midpoint * (1 - pct / 100)
        upper = midpoint * (1 + pct / 100)
        bid_levels = [x for x in bids if x["price"] >= lower]
        ask_levels = [x for x in asks if x["price"] <= upper]
        bid_vol = sum(x["volume"] for x in bid_levels)
        ask_vol = sum(x["volume"] for x in ask_levels)
        total = bid_vol + ask_vol
        out[f"depth_{pct:g}pct"] = {
            "bid_volume": bid_vol,
            "ask_volume": ask_vol,
            "imbalance": None if total == 0 else (bid_vol - ask_vol) / total,
        }

    near_bids = [x for x in bids if x["price"] >= midpoint * 0.98]
    near_asks = [x for x in asks if x["price"] <= midpoint * 1.02]
    out["largest_bid_wall_within_2pct"] = max(near_bids, key=lambda x: x["volume"], default=None)
    out["largest_ask_wall_within_2pct"] = max(near_asks, key=lambda x: x["volume"], default=None)
    return out
