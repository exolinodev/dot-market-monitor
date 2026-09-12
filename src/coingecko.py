from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import requests


@dataclass
class CoinGeckoClient:
    timeout: int = 20
    base_url: str = "https://api.coingecko.com/api/v3"

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "dot-market-monitor/1.0", "Accept": "application/json"})
        key = os.getenv("COINGECKO_API_KEY")
        if key:
            self.session.headers.update({"x-cg-demo-api-key": key})

    def _get(self, path: str, params: Optional[dict] = None, retries: int = 2) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"CoinGecko request failed for {path}: {last_exc}")

    def global_market(self) -> Dict[str, Any]:
        payload = self._get("/global")
        data = payload["data"]
        return {
            "btc_dominance": float(data["market_cap_percentage"]["btc"]),
            "eth_dominance": float(data["market_cap_percentage"]["eth"]),
            "total_market_cap_usd": float(data["total_market_cap"]["usd"]),
            "total_volume_usd": float(data["total_volume"]["usd"]),
            "market_cap_change_24h_pct": float(data["market_cap_change_percentage_24h_usd"]),
            "active_cryptocurrencies": int(data["active_cryptocurrencies"]),
        }

    def breadth(self) -> Dict[str, Any]:
        ids = [
            "ethereum",
            "binancecoin",
            "ripple",
            "solana",
            "dogecoin",
            "cardano",
            "chainlink",
            "avalanche-2",
            "polkadot",
        ]
        rows = self._get(
            "/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "price_change_percentage": "24h,7d",
                "sparkline": "false",
            },
        )
        coins: Dict[str, Any] = {}
        peers_24: list[float] = []
        peers_7d: list[float] = []
        dot_24 = None
        dot_7d = None

        for row in rows:
            sym = str(row["symbol"]).upper()
            ch24 = row.get("price_change_percentage_24h_in_currency")
            ch7 = row.get("price_change_percentage_7d_in_currency")
            ch24 = None if ch24 is None else float(ch24)
            ch7 = None if ch7 is None else float(ch7)
            coins[sym] = {
                "price_usd": float(row["current_price"]),
                "change_24h_pct": ch24,
                "change_7d_pct": ch7,
                "market_cap": row.get("market_cap"),
                "volume_24h": row.get("total_volume"),
            }
            if sym == "DOT":
                dot_24, dot_7d = ch24, ch7
            else:
                if ch24 is not None:
                    peers_24.append(ch24)
                if ch7 is not None:
                    peers_7d.append(ch7)

        med24 = None if not peers_24 else float(np.median(peers_24))
        med7 = None if not peers_7d else float(np.median(peers_7d))

        def classify(dot: Optional[float], median: Optional[float]) -> Optional[str]:
            if dot is None or median is None:
                return None
            spread = dot - median
            if spread >= 5:
                return "leader"
            if spread >= 1:
                return "outperformer"
            if spread <= -5:
                return "clear_relative_weakness"
            if spread <= -1:
                return "laggard"
            return "inline"

        return {
            "coins": coins,
            "peer_median_24h_pct": med24,
            "peer_median_7d_pct": med7,
            "dot_vs_peer_median_24h_pp": None if dot_24 is None or med24 is None else dot_24 - med24,
            "dot_vs_peer_median_7d_pp": None if dot_7d is None or med7 is None else dot_7d - med7,
            "dot_relative_strength_24h": classify(dot_24, med24),
            "dot_relative_strength_7d": classify(dot_7d, med7),
            "event_outliers_24h": [
                sym for sym, v in coins.items() if sym != "DOT" and v["change_24h_pct"] is not None and abs(v["change_24h_pct"]) >= 10
            ],
        }
