"""
Global Macro Multi-Asset Telemetry Engine for TypeSafe Jev
Author: Google Antigravity (Advanced Agentic Systems)

Ingests real-time cross-asset macro feeds from Binance:
1. Gold / USD (PAXGUSDT): Physical gold spot price & safe-haven liquidity
2. ETH / BTC (ETHBTC): Crypto risk-on vs. risk-off market breadth barometer
3. SOL / BTC (SOLBTC): High-beta speculative liquidity appetite
"""

import time
import logging
import urllib.request
import json
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MacroTelemetryEngine:
    """
    Real-time cross-asset macro engine polling Gold, ETH/BTC, and SOL/BTC.
    """

    def __init__(self, cache_ttl_sec: float = 15.0):
        self.cache_ttl = cache_ttl_sec
        self.last_update = 0.0
        self.cached_metrics = {}

    def fetch_24hr_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=3.5)
            data = json.loads(resp.read().decode())
            return {
                "last_price": float(data.get("lastPrice", 0.0)),
                "price_change_pct": float(data.get("priceChangePercent", 0.0)),
                "high_price": float(data.get("highPrice", 0.0)),
                "low_price": float(data.get("lowPrice", 0.0)),
                "volume": float(data.get("volume", 0.0)),
            }
        except Exception as e:
            logger.warning(f"[MACRO] Failed to fetch ticker for {symbol}: {e}")
            return None

    def get_macro_state(self) -> Dict[str, Any]:
        now = time.time()
        if self.cached_metrics and (now - self.last_update) < self.cache_ttl:
            return self.cached_metrics

        paxg = self.fetch_24hr_ticker("PAXGUSDT")
        ethbtc = self.fetch_24hr_ticker("ETHBTC")
        solbtc = self.fetch_24hr_ticker("SOLBTC")

        gold_price = paxg["last_price"] if paxg else 4365.0
        gold_chg = paxg["price_change_pct"] if paxg else 0.0

        eth_ratio = ethbtc["last_price"] if ethbtc else 0.0325
        eth_chg = ethbtc["price_change_pct"] if ethbtc else 0.0

        sol_ratio = solbtc["last_price"] if solbtc else 0.0022
        sol_chg = solbtc["price_change_pct"] if solbtc else 0.0

        # Synthesize Macro Regime
        # If ETH/BTC is expanding (+1.0%) -> Capital expanding into risk assets
        # If Gold is surging (+1.5%) while ETH/BTC is bleeding (-2.0%) -> Safe haven flight
        if eth_chg > 0.8 and sol_chg > 0.5:
            regime = "MACRO_RISK_ON"
            bias_score = 1.0
            desc = "Broad crypto risk-on expansion (ETH/BTC & SOL/BTC outperforming). Strong tailwind for BTC."
        elif gold_chg > 1.0 and eth_chg < -1.0:
            regime = "SAFE_HAVEN_ROTATION"
            bias_score = -0.5
            desc = "Gold outperforming while crypto beta bleeds. Institutional safe-haven flight."
        elif eth_chg < -1.5 and sol_chg < -1.5:
            regime = "MACRO_DE_RISKING"
            bias_score = -1.0
            desc = "Capital retreating from high-beta crypto into fiat/stablecoins."
        elif gold_chg > 0.2 and eth_chg > 0.2:
            regime = "HARD_ASSET_INFLATION"
            bias_score = 0.5
            desc = "Both Gold and Crypto bid against fiat dollar. Broad monetary debasement hedge."
        else:
            regime = "NEUTRAL_BALANCED"
            bias_score = 0.0
            desc = "Macro cross-assets balanced with no severe systemic divergence."

        self.cached_metrics = {
            "gold_usd": round(gold_price, 2),
            "gold_change_24h_pct": round(gold_chg, 2),
            "eth_btc": round(eth_ratio, 5),
            "eth_btc_change_24h_pct": round(eth_chg, 2),
            "sol_btc": round(sol_ratio, 5),
            "sol_btc_change_24h_pct": round(sol_chg, 2),
            "macro_risk_regime": regime,
            "macro_bias_score": bias_score,
            "macro_description": desc,
            "last_updated": now
        }
        self.last_update = now
        return self.cached_metrics


if __name__ == "__main__":
    engine = MacroTelemetryEngine()
    state = engine.get_macro_state()
    print("[TEST] Macro Telemetry State:")
    print(json.dumps(state, indent=2))
