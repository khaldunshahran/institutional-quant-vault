"""
Institutional Session Clock & Liquidity Magnet Engine for TypeSafe Jev
Author: Google Antigravity (Advanced Agentic Systems)

Computes:
1. Active Institutional Trading Session (New York Cash Open, London Open, Asia Range, Off-Hours)
2. Perpetual Futures Liquidity Magnet Clusters (Short Squeeze Pools & Long Liquidation Pools)
3. Sizing & Leverage Caps based on Session Liquidity
4. Dynamic per-symbol 24H High/Low Liquidation Pool tracking
"""

import time
import logging
import urllib.request
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _format_price(val: float) -> str:
    if val >= 1000:
        return f"${val:,.0f}"
    elif val >= 10:
        return f"${val:,.2f}"
    elif val >= 1:
        return f"${val:,.3f}"
    else:
        return f"${val:,.4f}"


class SessionClockEngine:
    """
    Session time-of-day clock and Liquidity Magnet pool detector.
    Supports dynamic per-symbol 24H liquidity pools and localized caching.
    """

    def __init__(self, cache_ttl_sec: float = 15.0):
        self.cache_ttl = cache_ttl_sec
        self.symbol_cache: Dict[str, Dict[str, Any]] = {}
        self.last_update = 0.0
        self.cached_state: Dict[str, Any] = {}

    def fetch_24h_levels(self, symbol: str = "BTCUSDT") -> Optional[Dict[str, float]]:
        clean_sym = symbol.upper().replace("/", "").replace("-", "")
        # Try futures first
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={clean_sym}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=3.5)
            data = json.loads(resp.read().decode())
            return {
                "spot": float(data.get("lastPrice", 0.0)),
                "high_24h": float(data.get("highPrice", 0.0)),
                "low_24h": float(data.get("lowPrice", 0.0)),
                "volume": float(data.get("volume", 0.0)),
            }
        except Exception:
            # Fallback to spot
            try:
                if clean_sym == "XAUUSDT":
                    clean_sym = "PAXGUSDT"
                url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={clean_sym}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=3.5)
                data = json.loads(resp.read().decode())
                return {
                    "spot": float(data.get("lastPrice", 0.0)),
                    "high_24h": float(data.get("highPrice", 0.0)),
                    "low_24h": float(data.get("lowPrice", 0.0)),
                    "volume": float(data.get("volume", 0.0)),
                }
            except Exception as e:
                logger.debug(f"[SESSION] Error fetching 24h levels for {clean_sym}: {e}")
                return None

    def get_session_and_liquidity_state(
        self,
        current_spot: Optional[float] = None,
        symbol: str = "BTCUSDT"
    ) -> Dict[str, Any]:
        now_time = time.time()
        clean_sym = symbol.upper().replace("/", "").replace("-", "")

        cached = self.symbol_cache.get(clean_sym)
        if cached and (now_time - cached.get("last_updated", 0.0)) < self.cache_ttl:
            self.cached_state = cached
            return cached

        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        minute = now_utc.minute
        time_dec = hour + (minute / 60.0)

        # 1. Session Classification (Global UTC based)
        if 13.0 <= time_dec <= 16.5:
            session_name = "NEW_YORK_CASH_OPEN"
            session_label = "🇺🇸 NEW YORK CASH OPEN (Peak Volume & ETF Inflow)"
            session_bias = "HIGH_CONVICTION_EXPANSION"
            session_lev_cap = 8
            breakout_allowed = True
        elif 7.0 <= time_dec <= 10.5:
            session_name = "LONDON_OPEN"
            session_label = "🇬🇧 LONDON OPEN (Institutional Liquidity Sweeps)"
            session_bias = "SWEEP_EXPANSION"
            session_lev_cap = 7
            breakout_allowed = True
        elif 0.0 <= time_dec <= 6.0:
            session_name = "ASIA_RANGE"
            session_label = "🌏 ASIA RANGE (Low Volume Range Building)"
            session_bias = "MEAN_REVERSION_ONLY"
            session_lev_cap = 4
            breakout_allowed = False  # Prohibit chasing breakouts during dead Asia chop!
        else:
            session_name = "OFF_HOURS_DRIFT"
            session_label = "🌙 OFF-HOURS CONSOLIDATION (Selective Scalping)"
            session_bias = "SELECTIVE_DRIFT"
            session_lev_cap = 5
            breakout_allowed = True

        # Funding Settlement Check (00:00, 08:00, 16:00 UTC - within 30 min)
        is_funding_window = False
        for fund_h in [0, 8, 16]:
            if abs(time_dec - fund_h) <= 0.5 or abs(time_dec - (fund_h + 24)) <= 0.5:
                is_funding_window = True
                break

        # 2. Liquidity Magnet Clusters for this specific symbol
        levels = self.fetch_24h_levels(symbol=clean_sym)
        spot = current_spot or (levels["spot"] if levels else 81350.0)
        h24 = levels["high_24h"] if levels else spot * 1.01
        l24 = levels["low_24h"] if levels else spot * 0.99

        # Projected liquidation pools:
        # Upper: Above 24h High (1.010x) -> Cluster of Short Stops
        # Lower: Below 24h Low (0.990x) -> Cluster of Long Stops
        upper_pool = round(h24 * 1.010, 4 if spot < 1 else 2)
        lower_pool = round(l24 * 0.990, 4 if spot < 1 else 2)

        dist_upper_usd = round(upper_pool - spot, 4 if spot < 1 else 2)
        dist_upper_pct = round((dist_upper_usd / spot) * 100.0, 2) if spot > 0 else 0.0

        dist_lower_usd = round(spot - lower_pool, 4 if spot < 1 else 2)
        dist_lower_pct = round((dist_lower_usd / spot) * 100.0, 2) if spot > 0 else 0.0

        is_upper_magnet = (0.0 < dist_upper_pct <= 0.55)
        is_lower_magnet = (0.0 < dist_lower_pct <= 0.55)

        upper_str = _format_price(upper_pool)
        lower_str = _format_price(lower_pool)

        if is_upper_magnet:
            magnet_status = f"SHORT SQUEEZE POOL at {upper_str} (+{dist_upper_pct}%) [MAGNET ACTIVE]"
        elif is_lower_magnet:
            magnet_status = f"LONG LIQUIDATION POOL at {lower_str} (-{dist_lower_pct}%) [MAGNET ACTIVE]"
        else:
            magnet_status = f"Upper Pool: {upper_str} (+{dist_upper_pct}%) | Lower Pool: {lower_str} (-{dist_lower_pct}%)"

        result = {
            "symbol": clean_sym,
            "session_name": session_name,
            "session_label": session_label,
            "session_bias": session_bias,
            "session_lev_cap": session_lev_cap,
            "breakout_allowed": breakout_allowed,
            "is_funding_window": is_funding_window,
            "utc_time": now_utc.strftime("%H:%M UTC"),
            "high_24h": h24,
            "low_24h": l24,
            "upper_liquidity_pool": upper_pool,
            "lower_liquidity_pool": lower_pool,
            "dist_upper_pct": dist_upper_pct,
            "dist_lower_pct": dist_lower_pct,
            "is_upper_magnet": is_upper_magnet,
            "is_lower_magnet": is_lower_magnet,
            "magnet_status": magnet_status,
            "last_updated": now_time
        }

        self.symbol_cache[clean_sym] = result
        self.cached_state = result
        self.last_update = now_time
        return result


if __name__ == "__main__":
    engine = SessionClockEngine()
    for sym in ["BTCUSDT", "SOLUSDT", "DOGEUSDT"]:
        state = engine.get_session_and_liquidity_state(symbol=sym)
        print(f"[TEST] Session & Liquidity Magnet State for {sym}:")
        print(json.dumps(state, indent=2))

