"""
Order Flow & Cumulative Volume Delta (CVD) Engine for TypeSafe Jev
Author: Google Antigravity (Advanced Agentic Systems)

Computes:
1. Real-time Spot and Perpetual Cumulative Volume Delta (CVD) for any Binance symbol
2. 5M and 15M Delta Momentum (Base asset & USD notional)
3. Institutional Absorption and Exhaustion Divergence Alerts across all liquid assets
"""

import time
import logging
import urllib.request
import json
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class OrderFlowEngine:
    """
    Real-time Order Flow Engine calculating Spot & Perp CVD and absorption divergences.
    Supports dynamic per-symbol querying with localized caching.
    """

    def __init__(self, cache_ttl_sec: float = 10.0):
        self.cache_ttl = cache_ttl_sec
        self.symbol_cache: Dict[str, Dict[str, Any]] = {}
        self.last_update = 0.0
        self.cached_metrics: Dict[str, Any] = {}

    def fetch_klines(
        self,
        symbol: str = "BTCUSDT",
        is_futures: bool = True,
        interval: str = "1m",
        limit: int = 20
    ) -> List[dict]:
        clean_sym = symbol.upper().replace("/", "").replace("-", "")
        # PAXG is spot-only, map XAUUSDT to PAXGUSDT if querying spot
        if not is_futures and clean_sym == "XAUUSDT":
            clean_sym = "PAXGUSDT"

        try:
            if is_futures:
                url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean_sym}&interval={interval}&limit={limit}"
            else:
                url = f"https://api.binance.com/api/v3/klines?symbol={clean_sym}&interval={interval}&limit={limit}"

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=3.5)
            raw = json.loads(resp.read().decode())
            candles = []
            for k in raw:
                # [open_time, open, high, low, close, volume, close_time, quote_vol, trades, taker_buy_vol, taker_buy_quote, ...]
                tot_vol = float(k[5])
                quote_vol = float(k[7])
                taker_buy = float(k[9])
                taker_buy_quote = float(k[10])
                taker_sell = max(0.0, tot_vol - taker_buy)
                taker_sell_quote = max(0.0, quote_vol - taker_buy_quote)
                delta = taker_buy - taker_sell
                delta_usd = taker_buy_quote - taker_sell_quote
                candles.append({
                    "open_time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": tot_vol,
                    "quote_vol": quote_vol,
                    "taker_buy": taker_buy,
                    "taker_buy_quote": taker_buy_quote,
                    "taker_sell": taker_sell,
                    "taker_sell_quote": taker_sell_quote,
                    "delta": delta,
                    "delta_usd": delta_usd,
                })
            return candles
        except Exception as e:
            logger.debug(f"[ORDER_FLOW] Kline fetch error for {clean_sym} (futures={is_futures}): {e}")
            return []

    def get_order_flow_metrics(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        now = time.time()
        clean_sym = symbol.upper().replace("/", "").replace("-", "")

        cached = self.symbol_cache.get(clean_sym)
        if cached and (now - cached.get("last_updated", 0.0)) < self.cache_ttl:
            # Also keep self.cached_metrics pointing to latest for backwards compatibility
            self.cached_metrics = cached
            return cached

        perp_candles = self.fetch_klines(symbol=clean_sym, is_futures=True, interval="1m", limit=20)
        spot_candles = self.fetch_klines(symbol=clean_sym, is_futures=False, interval="1m", limit=20)

        # If futures klines unavailable, fallback to spot candles.
        # LOUD labeling: consumers must know "perp" CVD is really spot data.
        perp_source = "futures"
        if not perp_candles and spot_candles:
            perp_candles = spot_candles
            perp_source = "spot_fallback"

        if not perp_candles:
            cached_fb = self.symbol_cache.get(clean_sym)
            if cached_fb:
                fallback = dict(cached_fb)
                fallback["degraded"] = True
                fallback["degradation_reason"] = "stale_cache: klines fetch failed"
            else:
                fallback = {
                    "symbol": clean_sym,
                    "perp_cvd_5m": 0.0,
                    "perp_cvd_15m": 0.0,
                    "perp_cvd_15m_usd": 0.0,
                    "spot_cvd_5m": 0.0,
                    "spot_cvd_15m": 0.0,
                    "spot_cvd_15m_usd": 0.0,
                    "delta_share_pct": 0.0,
                    "order_flow_bias": "BALANCED",
                    "divergence_alert": "NORMAL",
                    "taker_ratio_15m": 1.0,
                    "price_chg_15m_pct": 0.0,
                    "degraded": True,
                    "degradation_reason": "klines_unavailable",
                    "perp_cvd_source": "unavailable",
                    "last_updated": now
                }
            return fallback

        # Base asset CVD (e.g. BTC, SOL, ETH)
        perp_5m = sum(c["delta"] for c in perp_candles[-5:])
        perp_15m = sum(c["delta"] for c in perp_candles[-15:])
        spot_5m = sum(c["delta"] for c in spot_candles[-5:]) if spot_candles else 0.0
        spot_15m = sum(c["delta"] for c in spot_candles[-15:]) if spot_candles else 0.0

        # USD Notional CVD (Scale-invariant across all assets)
        perp_15m_usd = sum(c.get("delta_usd", 0.0) for c in perp_candles[-15:])
        spot_15m_usd = sum(c.get("delta_usd", 0.0) for c in spot_candles[-15:]) if spot_candles else 0.0
        total_15m_quote = sum(c.get("quote_vol", 0.0) for c in perp_candles[-15:])
        delta_share_pct = (perp_15m_usd / max(1.0, total_15m_quote)) * 100.0

        # Taker buy / sell ratio over last 15m
        taker_buy_total = sum(c.get("taker_buy_quote", 0.0) for c in perp_candles[-15:])
        taker_sell_total = sum(c.get("taker_sell_quote", 0.0) for c in perp_candles[-15:])
        taker_ratio_15m = round(taker_buy_total / max(1.0, taker_sell_total), 2)

        # Price movement over 15m
        price_now = perp_candles[-1]["close"]
        price_15m_ago = perp_candles[0]["open"] if perp_candles else price_now
        price_chg_pct = ((price_now - price_15m_ago) / price_15m_ago) * 100.0 if price_15m_ago > 0 else 0.0

        # Scale-invariant Divergence & Absorption Detection
        # Bullish Absorption: Price falling or flat while buyers aggressively absorbing into bids
        # Bearish Exhaustion: Price pumped up while aggressive market selling occurs
        divergence = "NORMAL"
        bias = "BALANCED"

        if price_chg_pct <= 0.05 and (delta_share_pct > 6.0 or taker_ratio_15m >= 1.18):
            divergence = "BULLISH_ABSORPTION"
            bias = "STRONG_BULLISH_ACCUMULATION"
        elif price_chg_pct >= 0.15 and (delta_share_pct < -6.0 or taker_ratio_15m <= 0.82):
            divergence = "BEARISH_EXHAUSTION"
            bias = "BEARISH_TRAP_DIVERGENCE"
        elif delta_share_pct > 8.0 and taker_ratio_15m >= 1.20 and price_chg_pct > 0.05:
            divergence = "BULLISH_EXPANSION"
            bias = "AGGRESSIVE_BUYING"
        elif delta_share_pct < -8.0 and taker_ratio_15m <= 0.80 and price_chg_pct < -0.05:
            divergence = "BEARISH_EXPANSION"
            bias = "AGGRESSIVE_SELLING"
        elif delta_share_pct > 3.0 or taker_ratio_15m >= 1.08:
            bias = "MODERATE_BULLISH"
        elif delta_share_pct < -3.0 or taker_ratio_15m <= 0.92:
            bias = "MODERATE_BEARISH"

        metrics = {
            "symbol": clean_sym,
            "perp_cvd_5m": round(perp_5m, 2),
            "perp_cvd_15m": round(perp_15m, 2),
            "perp_cvd_15m_usd": round(perp_15m_usd, 2),
            "spot_cvd_5m": round(spot_5m, 2),
            "spot_cvd_15m": round(spot_15m, 2),
            "spot_cvd_15m_usd": round(spot_15m_usd, 2),
            "delta_share_pct": round(delta_share_pct, 2),
            "taker_ratio_15m": taker_ratio_15m,
            "price_chg_15m_pct": round(price_chg_pct, 3),
            "divergence_alert": divergence,
            "order_flow_bias": bias,
            "degraded": False,
            "perp_cvd_source": perp_source,
            "last_updated": now
        }

        self.symbol_cache[clean_sym] = metrics
        self.cached_metrics = metrics
        self.last_update = now
        return metrics


if __name__ == "__main__":
    engine = OrderFlowEngine()
    for test_sym in ["BTCUSDT", "SOLUSDT", "ETHUSDT"]:
        metrics = engine.get_order_flow_metrics(symbol=test_sym)
        print(f"[TEST] Order Flow Metrics for {test_sym}:")
        print(json.dumps(metrics, indent=2))

