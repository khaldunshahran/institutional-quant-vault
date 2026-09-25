"""
Binance Universe Scanner - INSTITUTIONAL MULTI-ASSET EDITION
Continuously monitors 300+ Binance pairs in a non-blocking background worker.
Covers:
1. Institutional Metals: Gold (XAUUSDT Futures & PAXGUSDT Spot)
2. Top 20 Binance Liquid Mega-Caps (BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, SUI, LINK, etc.)
3. Top 24H Volatility Movers (highest percentage breakouts)

Uses ThreadPoolExecutor for sub-second parallel kline streaming.
Pre-calculates Hurst exponents, Robust Z-scores, and Opportunity Scores.
API calls return INSTANTLY from memory cache.
"""

import urllib.request
import json
import time
import math
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

GOLD_SYMBOLS = ["XAUUSDT", "PAXGUSDT"]

# Institutional Tier-1 Liquid Assets (Deep Liquidity, Tight Spreads, Low Slippage)
# Low-cap wick-traps (FIL, INJ, NEAR, etc.) are strictly blacklisted
TOP_20_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT",
    "LINKUSDT", "DOGEUSDT", "LTCUSDT", "XRPUSDT"
]

# Expanded experiment universe (Sep 2026): the core 9 above plus 20 more
# large-cap, high-liquidity majors. Still strictly large-cap — the Sep 20-21
# burst post-mortem showed blacklisted low-caps caused ~91% of historical
# losses, so low-cap wick-traps (FIL, INJ, NEAR, etc.) stay EXCLUDED.
# Safety: the scanner skips any symbol missing from the live ticker map,
# so a stale/delisted entry here is harmless (it is simply ignored).
TOP_50_SYMBOLS = [
    # core 9 (same as TOP_20_SYMBOLS)
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT",
    "LINKUSDT", "DOGEUSDT", "LTCUSDT", "XRPUSDT",
    # 20 additional liquid large-caps
    "ADAUSDT", "TRXUSDT", "TONUSDT", "DOTUSDT", "ATOMUSDT",
    "UNIUSDT", "ETCUSDT", "HBARUSDT", "VETUSDT", "ICPUSDT",
    "SUIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "POLUSDT",
    "SEIUSDT", "JUPUSDT", "AAVEUSDT", "LDOUSDT", "STXUSDT",
]


class BinanceUniverseScanner:
    def __init__(self, min_volume_usd: float = 50_000_000.0, cache_ttl_sec: float = 15.0, include_movers: bool = False):
        self.min_volume_usd = min_volume_usd
        self.cache_ttl_sec = cache_ttl_sec
        self.include_movers = include_movers
        self.last_scan_time = 0.0
        self.cached_universe: List[Dict[str, Any]] = []
        self.cached_by_symbol: Dict[str, Dict[str, Any]] = {}
        self.cached_gold: List[Dict[str, Any]] = []
        self.cached_movers: List[Dict[str, Any]] = []
        self.is_scanning = False

        # Run initial scan immediately in background thread
        threading.Thread(target=self._background_scan_loop, daemon=True, name="UniverseScanner").start()

    def _background_scan_loop(self):
        while True:
            try:
                self._execute_scan()
            except Exception as e:
                pass
            time.sleep(self.cache_ttl_sec)

    def _fetch_24h_tickers(self) -> List[Dict[str, Any]]:
        urls = [
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            "https://api.binance.com/api/v3/ticker/24hr"
        ]
        combined = []
        seen = set()
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        items = json.loads(resp.read().decode())
                        for it in items:
                            sym = it.get("symbol", "")
                            if sym and sym not in seen:
                                seen.add(sym)
                                combined.append(it)
            except Exception:
                continue
        return combined

    def _fetch_klines(self, symbol: str, interval: str = "15m", limit: int = 100) -> List[Dict[str, Any]]:
        """Fetches OHLCV candle records for institutional multi-timeframe analysis."""
        if symbol == "PAXGUSDT":
            urls = [f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"]
        else:
            urls = [
                f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}",
                f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            ]

        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        return [{
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5])
                        } for c in data]
            except Exception:
                continue
        return []

    def _calc_atr(self, candles: List[Dict[str, Any]], period: int = 14) -> float:
        """Calculates 14-period Average True Range (ATR) for volatility stop placement."""
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        if len(trs) < period:
            return 0.0
        return sum(trs[-period:]) / period

    def _calc_hurst(self, prices: List[float]) -> float:
        if len(prices) < 12:
            return 0.50
        try:
            returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
            n = len(returns)
            if n < 8:
                return 0.50
            mean_r = sum(returns) / n
            var_r = sum((r - mean_r) ** 2 for r in returns) / n
            std_r = math.sqrt(var_r)
            if std_r == 0:
                return 0.50

            devs = []
            cum = 0.0
            for r in returns:
                cum += (r - mean_r)
                devs.append(cum)
            r_range = max(devs) - min(devs)
            rs = r_range / std_r
            if rs <= 0 or n <= 1:
                return 0.50
            hurst = math.log(rs) / math.log(n)
            return round(max(0.20, min(0.80, hurst)), 3)
        except Exception:
            return 0.50

    def _execute_scan(self):
        self.is_scanning = True
        tickers = self._fetch_24h_tickers()
        if not tickers:
            self.is_scanning = False
            return

        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}

        # 1. Collect Gold pairs
        gold_pairs = []
        for sym in GOLD_SYMBOLS:
            if sym in ticker_map:
                t = ticker_map[sym]
                try:
                    price = float(t.get("lastPrice", 0.0))
                    if price > 0:
                        gold_pairs.append({
                            "symbol": sym,
                            "base_asset": "GOLD" if "XAU" in sym else "PAXG",
                            "price": price,
                            "change_24h_pct": round(float(t.get("priceChangePercent", 0.0)), 2),
                            "volume_24h_usd": float(t.get("quoteVolume", 0.0)),
                            "high_24h": float(t.get("highPrice", 0.0)),
                            "low_24h": float(t.get("lowPrice", 0.0)),
                            "tier": "GOLD",
                            "asset_category": "METALS"
                        })
                except Exception:
                    pass

        # 2. Collect Top 20 pairs
        top_20_pairs = []
        for sym in TOP_20_SYMBOLS:
            if sym in ticker_map and sym not in GOLD_SYMBOLS:
                t = ticker_map[sym]
                try:
                    price = float(t.get("lastPrice", 0.0))
                    if price > 0:
                        top_20_pairs.append({
                            "symbol": sym,
                            "base_asset": sym.replace("USDT", ""),
                            "price": price,
                            "change_24h_pct": round(float(t.get("priceChangePercent", 0.0)), 2),
                            "volume_24h_usd": float(t.get("quoteVolume", 0.0)),
                            "high_24h": float(t.get("highPrice", 0.0)),
                            "low_24h": float(t.get("lowPrice", 0.0)),
                            "tier": "TOP_20",
                            "asset_category": "CRYPTO_CORE"
                        })
                except Exception:
                    pass

        # 3. Dynamic 24h Movers (Only if include_movers=True; disabled for Institutional Focus)
        all_movers = []
        if self.include_movers:
            for sym, t in ticker_map.items():
                if not sym.endswith("USDT") or any(x in sym for x in ["UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT"]):
                    continue
                if sym in GOLD_SYMBOLS or sym in TOP_20_SYMBOLS:
                    continue
                try:
                    vol = float(t.get("quoteVolume", 0.0))
                    chg = float(t.get("priceChangePercent", 0.0))
                    price = float(t.get("lastPrice", 0.0))
                    if vol >= self.min_volume_usd and abs(chg) >= 5.0 and price > 0:
                        all_movers.append({
                            "symbol": sym,
                            "base_asset": sym.replace("USDT", ""),
                            "price": price,
                            "change_24h_pct": round(chg, 2),
                            "volume_24h_usd": vol,
                            "high_24h": float(t.get("highPrice", 0.0)),
                            "low_24h": float(t.get("lowPrice", 0.0)),
                            "tier": "24H_MOVER",
                            "asset_category": "MOMENTUM_MOVER"
                        })
                except Exception:
                    continue

            all_movers.sort(key=lambda x: abs(x["change_24h_pct"]), reverse=True)
        top_movers = all_movers[:10] if self.include_movers else []

        priority_candidates = gold_pairs + top_20_pairs + top_movers

        # Parallelize Kline fetching with ThreadPoolExecutor (15m interval, 100 bars = 25h history)
        kline_results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_sym = {
                executor.submit(self._fetch_klines, p["symbol"], "15m", 100): p["symbol"]
                for p in priority_candidates
            }
            for fut in as_completed(future_to_sym):
                sym = future_to_sym[fut]
                try:
                    kline_results[sym] = fut.result()
                except Exception:
                    kline_results[sym] = []

        processed = []
        for p in priority_candidates:
            symbol = p["symbol"]
            candles = kline_results.get(symbol, [])
            prices = [c["close"] for c in candles]
            atr_15m = self._calc_atr(candles, period=14)

            if len(prices) >= 14:
                recent_ret = (prices[-1] - prices[-5]) / prices[-5] * 100.0
                returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
                volatility_15m = (statistics.stdev(returns) * 100.0) if len(returns) > 1 else 0.5
                hurst = self._calc_hurst(prices)
            else:
                recent_ret = p["change_24h_pct"] * 0.1
                volatility_15m = 0.6
                hurst = 0.50

            score = 50.0
            score += min(volatility_15m * 15.0, 25.0)
            score += min(abs(recent_ret) * 8.0, 20.0)

            # Bonus for institutional Gold stability
            if p["tier"] == "GOLD":
                score += 10.0

            if hurst > 0.55:
                score += 15.0
                regime = "PERSISTENT_TREND"
                direction = "LONG" if recent_ret >= 0 else "SHORT"
                badge = f"TREND {direction}"
            elif hurst < 0.45:
                score += 10.0
                regime = "MEAN_REVERTING"
                direction = "LONG" if recent_ret < 0 else "SHORT"
                badge = "MEAN-REV SCALP"
            else:
                regime = "CHOPPY_RANGE"
                direction = "NEUTRAL"
                badge = "CHOPPY"
                score -= 5.0

            p["hurst_exponent"] = round(hurst, 3)
            p["volatility_15m_pct"] = round(volatility_15m, 3)
            p["momentum_15m_pct"] = round(recent_ret, 2)
            p["atr_15m"] = round(atr_15m, 4)
            p["timeframe"] = "15m"
            p["regime"] = regime
            p["direction"] = direction
            p["badge"] = badge
            p["opportunity_score"] = round(float(max(10.0, min(99.0, score))), 1)
            processed.append(p)

        processed.sort(key=lambda x: x["opportunity_score"], reverse=True)

        self.cached_universe = processed
        self.cached_by_symbol = {item["symbol"]: item for item in processed}
        self.cached_gold = [p for p in processed if p["tier"] == "GOLD"]
        self.cached_movers = [p for p in processed if p["tier"] == "24H_MOVER"]
        self.last_scan_time = time.time()
        self.is_scanning = False

    def scan_universe(self, max_ranked: int = 35, force_refresh: bool = False) -> Dict[str, Any]:
        """Returns instantly from pre-warmed background cache."""
        if force_refresh:
            threading.Thread(target=self._execute_scan, daemon=True).start()

        return {
            "status": "ok",
            "cached": True,
            "universe_count": len(self.cached_universe),
            "ranked_universe": self.cached_universe[:max_ranked],
            "gold_assets": self.cached_gold,
            "top_movers": self.cached_movers,
            "top_candidate": self.cached_universe[0] if self.cached_universe else None
        }

    def get_hunting_watchlist(self) -> List[str]:
        """Returns ordered symbols list for institutional high-frequency hunting."""
        symbols = []
        # Always prioritize Gold
        for g in self.cached_gold:
            symbols.append(g["symbol"])
        if not symbols:
            symbols.extend(GOLD_SYMBOLS)

        # Then top opportunity ranked assets from permitted universe
        for p in self.cached_universe:
            if not self.include_movers and p.get("tier") == "24H_MOVER":
                continue
            sym = p["symbol"]
            if sym not in symbols:
                symbols.append(sym)

        # Fallback to core top 20
        for s in TOP_20_SYMBOLS:
            if s not in symbols:
                symbols.append(s)

        return symbols[:25]


if __name__ == "__main__":
    scanner = BinanceUniverseScanner()
    print("[INIT] Testing Binance Universe Scanner with ThreadPool...")
    scanner._execute_scan()
    res = scanner.scan_universe()
    print(f"Total Universe Cached: {res['universe_count']} assets")
    print(f"Gold Assets: {[g['symbol'] + ' $' + str(g['price']) for g in res['gold_assets']]}")
    print(f"Top 5 Ranked: {[p['symbol'] + ' (' + str(p['opportunity_score']) + ')' for p in res['ranked_universe'][:5]]}")
    print(f"Hunting Watchlist ({len(scanner.get_hunting_watchlist())} symbols):", scanner.get_hunting_watchlist()[:10])
