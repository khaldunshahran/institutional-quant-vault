"""
Deribit Bitcoin Implied Volatility (DVOL / "Crypto VIX") Engine for TypeSafe Jev
Author: Google Antigravity (Advanced Agentic Systems)

Fetches real-time 30-day forward implied volatility from Deribit Options:
1. DVOL Level (e.g. 34.91)
2. Volatility Compression / Expansion Regime (Extreme Squeeze vs. Normal vs. High Vol)
"""

import time
import logging
import urllib.request
import json
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DvolEngine:
    """
    Deribit BTC Volatility Index (DVOL) polling and regime classification.
    """

    def __init__(self, cache_ttl_sec: float = 60.0):
        self.cache_ttl = cache_ttl_sec
        self.last_update = 0.0
        self.cached_metrics = {}

    def fetch_dvol(self) -> Optional[float]:
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - 86400000  # last 24h
            url = f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&start_timestamp={start_ms}&end_timestamp={now_ms}&resolution=3600"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=4.0)
            data = json.loads(resp.read().decode())
            candles = data.get("result", {}).get("data", [])
            if candles:
                latest = candles[-1]
                # [timestamp, open, high, low, close]
                dvol_val = float(latest[4])
                return dvol_val
            return None
        except Exception as e:
            logger.warning(f"[DVOL] Failed to fetch Deribit DVOL: {e}")
            return None

    def get_dvol_state(self) -> Dict[str, Any]:
        now = time.time()
        if self.cached_metrics and (now - self.last_update) < self.cache_ttl:
            return self.cached_metrics

        val = self.fetch_dvol()
        if val is None:
            val = self.cached_metrics.get("dvol", 34.9)

        val = round(val, 2)

        # Regimes:
        # < 42: Extreme Volatility Squeeze (Option buyers expect almost nothing; massive expansion coming)
        # 42 - 65: Normal Balanced Volatility
        # > 65: High Implied Volatility (Options bloated; market expects violence)
        if val < 42.0:
            regime = "EXTREME_VOL_SQUEEZE"
            regime_label = "⚡ SQUEEZE EXPANSION IMMINENT"
            bias_note = "Historic options compression. High likelihood of explosive 2,000+ point trend expansion. Prepare runners."
            runner_multiplier = 1.35
        elif val > 65.0:
            regime = "HIGH_VOL_EXHAUSTION"
            regime_label = "🔥 HIGH IMPLIED VOLATILITY"
            bias_note = "Options premiums blown out. Extreme swings; mean reversion favored."
            runner_multiplier = 0.8
        else:
            regime = "NORMAL_VOLATILITY"
            regime_label = "BALANCED IMPLIED VOL"
            bias_note = "Standard options volatility pricing."
            runner_multiplier = 1.0

        self.cached_metrics = {
            "dvol": val,
            "dvol_regime": regime,
            "dvol_label": regime_label,
            "bias_note": bias_note,
            "runner_multiplier": runner_multiplier,
            "last_updated": now
        }
        self.last_update = now
        return self.cached_metrics


if __name__ == "__main__":
    engine = DvolEngine()
    state = engine.get_dvol_state()
    print("[TEST] Deribit DVOL State:")
    print(json.dumps(state, indent=2))
