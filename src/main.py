from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from coingecko import CoinGeckoClient
from indicators import IndicatorConfig, snapshot
from kraken import KrakenClient, KrakenError, data_age_seconds, orderbook_metrics, trade_flow

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST = DATA_DIR / "latest.json"
SUMMARY = DATA_DIR / "latest.md"
HISTORY = DATA_DIR / "history.json"

TIMEFRAMES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}

BTC_TIMEFRAMES = {"1h": 60, "4h": 240, "1d": 1440}
DOTBTC_TIMEFRAMES = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return json_safe(obj.item())
    except Exception:
        pass
    return obj


def tf_snapshot(client: KrakenClient, pair: str, interval: int, cfg: IndicatorConfig) -> Dict[str, Any]:
    df = client.ohlc(pair, interval)
    include_vwap = interval < 1440
    live = snapshot(df, cfg, include_vwap)
    closed = snapshot(df.iloc[:-1], cfg, include_vwap) if len(df) > 1 else None
    return {
        "interval_minutes": interval,
        "live": live,
        "last_closed": closed,
        "latest_candle_is_open": True,
        "source_rows": len(df),
    }


def resolve_spot_price(
    ticker: Dict[str, Any], trades: pd.DataFrame, spread: pd.DataFrame, now: datetime
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ticker_last": ticker["last"],
        "ticker_bid": ticker["bid"],
        "ticker_ask": ticker["ask"],
        "verified_price": None,
        "verification": "not_verified",
    }
    if not trades.empty:
        latest = trades.iloc[-1]
        trade_age = data_age_seconds(latest["time"], now)
        out["latest_trade_utc"] = latest["time"].isoformat()
        out["latest_trade_age_seconds"] = trade_age
        out["latest_trade_price"] = float(latest["price"])
        if trade_age <= 120:
            out["verified_price"] = float(latest["price"])
            out["verification"] = "latest_trade_le_120s"
            return out

    if not spread.empty:
        latest_s = spread.iloc[-1]
        spread_age = data_age_seconds(latest_s["time"], now)
        out["latest_spread_utc"] = latest_s["time"].isoformat()
        out["latest_spread_age_seconds"] = spread_age
        out["latest_spread_bid"] = float(latest_s["bid"])
        out["latest_spread_ask"] = float(latest_s["ask"])
        if spread_age <= 120 and float(latest_s["bid"]) <= ticker["last"] <= float(latest_s["ask"]):
            out["verified_price"] = ticker["last"]
            out["verification"] = "ticker_inside_fresh_spread"
            return out

    out["verification"] = "ticker_unverified_freshness"
    return out


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return []


def nearest_history(history: list[dict], now_ts: float, age_hours: float) -> Optional[dict]:
    if not history:
        return None
    target = now_ts - age_hours * 3600
    closest = min(history, key=lambda x: abs(float(x["unix"]) - target))
    # Do not label a one-hour-old point as a 24h or 7d comparison.
    return closest if abs(float(closest["unix"]) - target) <= 90 * 60 else None


def update_history(now: datetime, global_market: Optional[dict], breadth: Optional[dict]) -> Dict[str, Any]:
    history = load_history()
    entry = {
        "unix": now.timestamp(),
        "generated_at_utc": now.isoformat(),
        "btc_dominance": None if not global_market else global_market.get("btc_dominance"),
        "dot_peer_median_24h_pp": None if not breadth else breadth.get("dot_vs_peer_median_24h_pp"),
    }
    history.append(entry)
    cutoff = now.timestamp() - 8 * 24 * 3600
    history = [x for x in history if float(x.get("unix", 0)) >= cutoff]
    history.sort(key=lambda x: float(x["unix"]))
    HISTORY.write_text(json.dumps(json_safe(history), indent=2, allow_nan=False), encoding="utf-8")

    cur = entry.get("btc_dominance")
    h24 = nearest_history(history[:-1], now.timestamp(), 24)
    h7d = nearest_history(history[:-1], now.timestamp(), 24 * 7)

    def delta(old: Optional[dict]) -> Optional[float]:
        if cur is None or not old or old.get("btc_dominance") is None:
            return None
        return float(cur) - float(old["btc_dominance"])

    return {
        "btc_dominance_change_24h_pp": delta(h24),
        "btc_dominance_change_7d_pp": delta(h7d),
        "history_points": len(history),
    }


def markdown_summary(data: Dict[str, Any]) -> str:
    def fmt(v: Any, n: int = 6) -> str:
        if v is None:
            return "n/a"
        if isinstance(v, float):
            return f"{v:.{n}f}"
        return str(v)

    dot = data["markets"]["DOTUSD"]
    btc = data["markets"]["BTCUSD"]
    perp = dot.get("perp", {})
    breadth = data.get("breadth") or {}
    global_market = data.get("global_market") or {}
    dom_hist = data.get("dominance_history") or {}

    lines = [
        "# DOT Market Snapshot",
        "",
        f"Generated UTC: {data['generated_at_utc']}",
        "",
        "## Live market",
        f"DOT spot verified: {fmt(dot.get('spot', {}).get('verified_price'), 6)} USD",
        f"DOT perp mark: {fmt(perp.get('mark_price'), 6)} USD",
        f"BTC spot verified: {fmt(btc.get('spot', {}).get('verified_price'), 2)} USD",
        f"DOT/BTC spot: {fmt(data.get('markets', {}).get('DOTBTC', {}).get('spot_last'), 10)} BTC",
        "",
        "## Market regime",
        f"BTC dominance: {fmt(global_market.get('btc_dominance'), 3)}%",
        f"BTC dominance 24h delta: {fmt(dom_hist.get('btc_dominance_change_24h_pp'), 3)} pp",
        f"BTC dominance 7d delta: {fmt(dom_hist.get('btc_dominance_change_7d_pp'), 3)} pp",
        f"Alt peer median 24h: {fmt(breadth.get('peer_median_24h_pct'), 2)}%",
        f"Alt peer median 7d: {fmt(breadth.get('peer_median_7d_pct'), 2)}%",
        f"DOT relative strength 24h: {breadth.get('dot_relative_strength_24h', 'n/a')}",
        f"DOT relative strength 7d: {breadth.get('dot_relative_strength_7d', 'n/a')}",
        "",
        "## DOT timeframes",
        "| TF | Close | RSI14 | Stoch K | MACD hist | EMA20 | EMA50 | EMA200 | Structure | Vol x20 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for tf, item in dot.get("timeframes", {}).items():
        s = item.get("live")
        if not s:
            lines.append(f"| {tf} | n/a | n/a | n/a | n/a | n/a | n/a | n/a | unavailable | n/a |")
            continue
        ind = s["indicators"]
        lines.append(
            f"| {tf} | {fmt(s['close'], 5)} | {fmt(ind['rsi14'], 1)} | {fmt(ind['stoch_rsi_k'], 1)} | "
            f"{fmt(ind['macd_hist'], 6)} | {fmt(ind['ema20'], 5)} | {fmt(ind['ema50'], 5)} | "
            f"{fmt(ind['ema200'], 5)} | {s['structure']['trend']} | {fmt(ind['volume_ratio20'], 2)} |"
        )

    lines.extend(
        [
            "",
            "## Flow and order book",
            f"5m trade delta: {fmt(dot.get('trade_flow', {}).get('5m', {}).get('delta'), 2)} DOT",
            f"15m trade delta: {fmt(dot.get('trade_flow', {}).get('15m', {}).get('delta'), 2)} DOT",
            f"1% book imbalance: {fmt(dot.get('orderbook', {}).get('depth_1pct', {}).get('imbalance'), 3)}",
            "",
            "Machine-readable source: `data/latest.json`",
            "",
        ]
    )
    if data.get("errors"):
        lines.extend(["## Data availability", ""])
        lines.extend(f"- {err['component']}: {err['error']}" for err in data["errors"])
        lines.append("")
    return "\n".join(lines)


def validate_core_data(data: Dict[str, Any]) -> None:
    """Never replace a usable snapshot with missing primary market data."""
    missing = []
    for market, timeframes in (("DOTUSD", TIMEFRAMES), ("BTCUSD", BTC_TIMEFRAMES)):
        values = data["markets"][market]
        price = values.get("spot", {}).get("verified_price")
        if price is None or not math.isfinite(price) or price <= 0:
            missing.append(f"{market}.spot.verified_price")
        for tf in timeframes:
            item = values.get("timeframes", {}).get(tf, {})
            if not item.get("live") or not item.get("last_closed"):
                missing.append(f"{market}.timeframes.{tf}")
    if missing:
        detail = "; ".join(f"{e['component']}: {e['error']}" for e in data["errors"])
        raise RuntimeError(f"Core market data unavailable: {', '.join(missing)}. {detail}")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    cfg = IndicatorConfig()
    kraken = KrakenClient()
    errors: list[dict] = []

    data: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": now.isoformat(),
        "generated_at_unix": now.timestamp(),
        "markets": {"DOTUSD": {}, "BTCUSD": {}, "DOTBTC": {}},
        "errors": errors,
    }

    try:
        data["kraken_time"] = kraken.server_time()
        data["kraken_time"]["age_seconds"] = data_age_seconds(data["kraken_time"]["utc"], now)
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "kraken_time", "error": str(exc)})

    # DOT spot, trades, spread, order book
    try:
        dot_ticker = kraken.spot_ticker("DOTUSD")
        dot_trades = kraken.recent_trades("DOTUSD")
        dot_spread = kraken.spread("DOTUSD")
        dot_spot = resolve_spot_price(dot_ticker, dot_trades, dot_spread, datetime.now(timezone.utc))
        dot_spot["ticker"] = dot_ticker
        data["markets"]["DOTUSD"]["spot"] = dot_spot
        data["markets"]["DOTUSD"]["trade_flow"] = trade_flow(dot_trades)
        book = kraken.order_book("DOTUSD", 100)
        data["markets"]["DOTUSD"]["orderbook"] = orderbook_metrics(book, dot_spot.get("verified_price") or dot_ticker["last"])
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "dot_spot", "error": str(exc)})

    # DOT perp
    try:
        perp = kraken.futures_ticker("PF_DOTUSD")
        perp["age_seconds"] = data_age_seconds(perp["server_time_utc"])
        perp["fresh_le_120s"] = perp["age_seconds"] <= 120
        if not perp["fresh_le_120s"]:
            try:
                candles = kraken.futures_mark_candles("PF_DOTUSD", "1m")
                last_ts = candles.index[-1]
                last_close = float(candles.iloc[-1]["close"])
                mark_age = data_age_seconds(last_ts)
                perp["fallback_1m_mark"] = {
                    "asof_utc": last_ts.isoformat(),
                    "age_seconds": mark_age,
                    "close": last_close,
                    "fresh_le_120s": mark_age <= 120,
                }
                if mark_age <= 120:
                    perp["mark_price"] = last_close
                    perp["price_source"] = "futures_1m_mark_fallback"
                    perp["mark_fresh_le_120s"] = True
                else:
                    perp["price_source"] = "stale"
            except Exception as fallback_exc:  # noqa: BLE001
                errors.append({"component": "dot_perp_fallback", "error": str(fallback_exc)})
        else:
            perp["price_source"] = "futures_ticker_markPrice"
        perp.setdefault("mark_fresh_le_120s", perp["fresh_le_120s"])
        if not perp["mark_fresh_le_120s"]:
            perp["mark_price"] = None
            errors.append({"component": "dot_perp_freshness", "error": "No fresh mark price available"})
        data["markets"]["DOTUSD"]["perp"] = perp
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "dot_perp", "error": str(exc)})

    # DOT multi-timeframe
    dot_tfs: Dict[str, Any] = {}
    for name, interval in TIMEFRAMES.items():
        try:
            dot_tfs[name] = tf_snapshot(kraken, "DOTUSD", interval, cfg)
        except Exception as exc:  # noqa: BLE001
            dot_tfs[name] = {"error": str(exc)}
            errors.append({"component": f"dot_ohlc_{name}", "error": str(exc)})
    data["markets"]["DOTUSD"]["timeframes"] = dot_tfs

    # BTC spot and selected timeframes
    try:
        btc_ticker = kraken.spot_ticker("XBTUSD")
        btc_trades = kraken.recent_trades("XBTUSD")
        btc_spread = kraken.spread("XBTUSD")
        btc_spot = resolve_spot_price(btc_ticker, btc_trades, btc_spread, datetime.now(timezone.utc))
        btc_spot["ticker"] = btc_ticker
        data["markets"]["BTCUSD"]["spot"] = btc_spot
        data["markets"]["BTCUSD"]["trade_flow"] = trade_flow(btc_trades)
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "btc_spot", "error": str(exc)})

    btc_tfs: Dict[str, Any] = {}
    for name, interval in BTC_TIMEFRAMES.items():
        try:
            btc_tfs[name] = tf_snapshot(kraken, "XBTUSD", interval, cfg)
        except Exception as exc:  # noqa: BLE001
            btc_tfs[name] = {"error": str(exc)}
            errors.append({"component": f"btc_ohlc_{name}", "error": str(exc)})
    data["markets"]["BTCUSD"]["timeframes"] = btc_tfs

    # DOT/BTC direct pair when Kraken exposes it
    try:
        dotbtc_pair = kraken.resolve_altname("DOT/XBT")
        data["markets"]["DOTBTC"]["kraken_pair"] = dotbtc_pair
        dotbtc_ticker = kraken.spot_ticker(dotbtc_pair)
        dotbtc_trades = kraken.recent_trades(dotbtc_pair)
        dotbtc_spread = kraken.spread(dotbtc_pair)
        dotbtc_spot = resolve_spot_price(dotbtc_ticker, dotbtc_trades, dotbtc_spread, datetime.now(timezone.utc))
        dotbtc_spot["ticker"] = dotbtc_ticker
        data["markets"]["DOTBTC"]["spot"] = dotbtc_spot
        data["markets"]["DOTBTC"]["spot_last"] = dotbtc_spot.get("verified_price")
        data["markets"]["DOTBTC"]["trade_flow"] = trade_flow(dotbtc_trades)
        dotbtc_tfs: Dict[str, Any] = {}
        for name, interval in DOTBTC_TIMEFRAMES.items():
            try:
                dotbtc_tfs[name] = tf_snapshot(kraken, dotbtc_pair, interval, cfg)
            except Exception as exc:  # noqa: BLE001
                dotbtc_tfs[name] = {"error": str(exc)}
                errors.append({"component": f"dotbtc_ohlc_{name}", "error": str(exc)})
        data["markets"]["DOTBTC"]["timeframes"] = dotbtc_tfs
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "dotbtc", "error": str(exc)})
        try:
            dot_px = data["markets"]["DOTUSD"]["spot"]["verified_price"]
            btc_px = data["markets"]["BTCUSD"]["spot"]["verified_price"]
            if dot_px is not None and btc_px is not None:
                data["markets"]["DOTBTC"]["spot_last_derived"] = dot_px / btc_px
        except Exception:
            pass

    # Cross-check spot/perp basis
    try:
        spot_px = float(data["markets"]["DOTUSD"]["spot"]["verified_price"])
        mark_px = float(data["markets"]["DOTUSD"]["perp"]["mark_price"])
        data["markets"]["DOTUSD"]["spot_perp_basis_pct"] = (mark_px / spot_px - 1) * 100
    except Exception:
        data["markets"]["DOTUSD"]["spot_perp_basis_pct"] = None

    # CoinGecko regime data
    global_market = None
    breadth = None
    try:
        cg = CoinGeckoClient()
        global_market = cg.global_market()
        data["global_market"] = global_market
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "coingecko_global", "error": str(exc)})
        data["global_market"] = None

    try:
        cg = CoinGeckoClient()
        breadth = cg.breadth()
        data["breadth"] = breadth
    except Exception as exc:  # noqa: BLE001
        errors.append({"component": "coingecko_breadth", "error": str(exc)})
        data["breadth"] = None

    validate_core_data(data)
    data["collection_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    data["status"] = "partial" if errors else "ok"
    data["dominance_history"] = update_history(now, global_market, breadth)

    summary = markdown_summary(data)
    serialized = json.dumps(json_safe(data), indent=2, sort_keys=True, allow_nan=False)
    LATEST.write_text(serialized, encoding="utf-8")
    SUMMARY.write_text(summary, encoding="utf-8")
    print(f"Wrote {LATEST}")
    print(f"Wrote {SUMMARY}")
    if errors:
        print(f"Completed with {len(errors)} non-fatal errors")
        for err in errors:
            print(f"- {err['component']}: {err['error']}")


if __name__ == "__main__":
    main()
