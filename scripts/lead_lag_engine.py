"""
Coinbase Spot vs. Binance Perp Lead-Lag Arbitrage Engine
Author: Google Antigravity (Advanced Agentic Systems)

Detects US institutional spot flows on Coinbase leading Binance Perpetual Futures.
When Coinbase Spot accelerates ahead of Binance perps, Jev front-runs the
futures catch-up move with high statistical probability.
"""

import time
import json
import logging
import urllib.request
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class LeadLagEngine:
    def __init__(self, cache_ttl_sec: float = 3.0):
        self.cache_ttl = cache_ttl_sec
        self.last_poll_ts = 0.0
        self.cached_state = None
        self.cb_history = []   # list of (timestamp, price)
        self.bin_history = []  # list of (timestamp, price)

    def get_lead_lag_state(self, binance_perp_price: float) -> Dict[str, Any]:
        """
        Polls Coinbase Spot and computes real-time lead-lag velocity delta
        against Binance Perpetual Futures.
        """
        now = time.time()
        if self.cached_state and (now - self.last_poll_ts) < self.cache_ttl:
            return self.cached_state

        cb_spot = self._fetch_coinbase_spot()
        if not cb_spot:
            cb_spot = binance_perp_price

        # Record history (keep last 60 seconds)
        self.cb_history.append((now, cb_spot))
        self.bin_history.append((now, binance_perp_price))
        self.cb_history = [(t, p) for t, p in self.cb_history if (now - t) <= 60.0]
        self.bin_history = [(t, p) for t, p in self.bin_history if (now - t) <= 60.0]

        # Compute 15-second velocities ($/s)
        cb_vel = self._compute_velocity(self.cb_history, window_sec=15.0)
        bin_vel = self._compute_velocity(self.bin_history, window_sec=15.0)
        lead_lag_delta = round(cb_vel - bin_vel, 2)

        # Coinbase Premium Spread ($)
        cb_premium = round(cb_spot - binance_perp_price, 2)

        # Regime & Bias determination
        if lead_lag_delta >= 1.2 and cb_premium > 2.0:
            bias = "COINBASE_SPOT_LEADING_BULLISH"
            label = "🇺🇸 COINBASE SPOT ACCUMULATION (BULLISH LEAD)"
            signal_boost = "LONG"
        elif lead_lag_delta <= -1.2 and cb_premium < -2.0:
            bias = "COINBASE_SPOT_LEADING_BEARISH"
            label = "🇺🇸 COINBASE SPOT DUMP (BEARISH LEAD)"
            signal_boost = "SHORT"
        else:
            bias = "BALANCED_SYNCHRONIZED"
            label = "⚖️ Spot-Perp Synchronized"
            signal_boost = "NEUTRAL"

        state = {
            "coinbase_spot": cb_spot,
            "binance_perp": binance_perp_price,
            "coinbase_premium_usd": cb_premium,
            "coinbase_velocity_15s": cb_vel,
            "binance_velocity_15s": bin_vel,
            "lead_lag_velocity_delta": lead_lag_delta,
            "lead_lag_bias": bias,
            "lead_lag_label": label,
            "signal_boost": signal_boost,
            "last_updated": now,
        }

        self.last_poll_ts = now
        self.cached_state = state
        return state

    def _compute_velocity(self, history, window_sec: float = 15.0) -> float:
        if len(history) < 2:
            return 0.0
        now = history[-1][0]
        recent = [item for item in history if (now - item[0]) <= window_sec]
        if len(recent) < 2:
            return 0.0
        dt = recent[-1][0] - recent[0][0]
        if dt < 1.0:
            return 0.0
        dp = recent[-1][1] - recent[0][1]
        return round(dp / dt, 2)

    def _fetch_coinbase_spot(self) -> Optional[float]:
        try:
            url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return float(data.get("data", {}).get("amount", 0.0))
        except Exception as e:
            logger.warning(f"Coinbase fetch failed: {e}")
            return None


if __name__ == "__main__":
    engine = LeadLagEngine()
    spot = 81300.0
    state = engine.get_lead_lag_state(spot)
    print("[TEST] Coinbase vs Binance Lead-Lag State:")
    print(json.dumps(state, indent=2))
