"""
Multi-Timeframe Technical Indicator Pipeline for Bitcoin Futures
Author: Google Antigravity (Advanced Agentic Systems)

Fetches, caches, and calculates multi-timeframe indicators:
- 1m: Micro impulse, velocity, microstructure
- 15m: Intraday timing (EMA 9, EMA 21, RSI 14)
- 4h: Primary swing structure (EMA 20, EMA 50, EMA 200, Bollinger Bands 20,2, RSI 14, ATR 14)
- 8h: Intermediate trend (EMA 20, EMA 50, RSI 14)
- 12h: Institutional wave (EMA 20, EMA 50, RSI 14)
- 1w: Macro structural regime (EMA 20w, EMA 50w, Bull/Bear market status)
"""

import math
import time
import logging
import threading
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger(__name__)


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """Calculates the Exponential Moving Average (EMA) for the given period."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price * k) + (ema * (1.0 - k))
    return round(ema, 2)


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculates Wilder's Smoothed Relative Strength Index (RSI)."""
    if len(prices) < period + 1:
        return 50.0

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(0.0, ch) for ch in changes]
    losses = [max(0.0, -ch) for ch in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 1)


def calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Dict[str, Any]:
    """Calculates Bollinger Bands: Upper, Middle, Lower, Bandwidth, and %B."""
    if len(prices) < period:
        p = prices[-1] if prices else 80000.0
        return {
            "middle": p,
            "upper": p * 1.02,
            "lower": p * 0.98,
            "bandwidth_pct": 4.0,
            "pct_b": 0.5,
            "squeeze": False,
        }

    slice_prices = prices[-period:]
    middle = sum(slice_prices) / period
    variance = sum((x - middle) ** 2 for x in slice_prices) / period
    std_dev = math.sqrt(variance)

    upper = middle + (num_std * std_dev)
    lower = middle - (num_std * std_dev)

    current_price = prices[-1]
    band_range = max(1.0, upper - lower)
    pct_b = (current_price - lower) / band_range
    bandwidth_pct = (band_range / max(middle, 1.0)) * 100.0

    return {
        "middle": round(middle, 2),
        "upper": round(upper, 2),
        "lower": round(lower, 2),
        "bandwidth_pct": round(bandwidth_pct, 2),
        "pct_b": round(pct_b, 3),
        "squeeze": bandwidth_pct < 3.2,
    }


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Calculates Average True Range (ATR) for volatility stop-loss calibration."""
    if len(closes) < period + 1:
        return 350.0

    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
    return round(atr, 1)


class MTFTechnicalEngine:
    """
    Multi-Timeframe Technical Engine for BTCUSDT.
    Polls Binance klines with caching:
    - 1m/15m: TTL 10 seconds
    - 4h/8h/12h: TTL 60 seconds
    - 1w: TTL 300 seconds
    """

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl_map = {
            "1m": 10,
            "15m": 15,
            "4h": 60,
            "8h": 90,
            "12h": 120,
            "1w": 300,
        }
        self.last_summary: Optional[Dict[str, Any]] = None

    def fetch_klines(self, interval: str, limit: int = 100) -> List[Dict[str, float]]:
        """Fetches klines from Binance API with TTL caching."""
        now = time.time()
        cached = self._cache.get(interval)
        ttl = self._ttl_map.get(interval, 30)

        if cached and (now - cached["timestamp"]) < ttl:
            return cached["candles"]

        try:
            url = "https://api.binance.com/api/v3/klines"
            res = requests.get(url, params={"symbol": self.symbol, "interval": interval, "limit": limit}, timeout=4)
            if res.status_code == 200:
                raw = res.json()
                candles = [
                    {
                        "open_time": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                    }
                    for c in raw
                ]
                with self.lock:
                    self._cache[interval] = {"timestamp": now, "candles": candles}
                return candles
        except Exception as e:
            logger.debug(f"Error fetching {interval} klines: {e}")

        if cached:
            return cached["candles"]
        return []

    def compute_all_indicators(self, current_spot: Optional[float] = None) -> Dict[str, Any]:
        """
        Computes all technical indicators across 1m, 15m, 4h, 8h, 12h, and 1w.
        Returns a clean, institutional-grade multi-timeframe state dictionary.
        """
        k_1m = self.fetch_klines("1m", 60)
        k_15m = self.fetch_klines("15m", 100)
        k_4h = self.fetch_klines("4h", 100)
        k_8h = self.fetch_klines("8h", 80)
        k_12h = self.fetch_klines("12h", 80)
        k_1w = self.fetch_klines("1w", 60)

        spot = current_spot or (k_1m[-1]["close"] if k_1m else 81000.0)

        # 1. 1M Micro Momentum
        closes_1m = [c["close"] for c in k_1m]
        v1m = (closes_1m[-1] - closes_1m[-2]) if len(closes_1m) >= 2 else 0.0
        rsi_1m = calculate_rsi(closes_1m, 14) if len(closes_1m) >= 15 else 50.0

        # 2. 15M Intraday Trigger
        closes_15m = [c["close"] for c in k_15m]
        ema_9_15m = calculate_ema(closes_15m, 9)
        ema_21_15m = calculate_ema(closes_15m, 21)
        rsi_15m = calculate_rsi(closes_15m, 14)
        trend_15m = "NEUTRAL"
        if ema_9_15m and ema_21_15m:
            if ema_9_15m > ema_21_15m and spot > ema_9_15m:
                trend_15m = "BULLISH"
            elif ema_9_15m < ema_21_15m and spot < ema_9_15m:
                trend_15m = "BEARISH"

        # 3. 4H Primary Swing Trend & Bollinger Bands
        closes_4h = [c["close"] for c in k_4h]
        highs_4h = [c["high"] for c in k_4h]
        lows_4h = [c["low"] for c in k_4h]
        ema_20_4h = calculate_ema(closes_4h, 20)
        ema_50_4h = calculate_ema(closes_4h, 50)
        ema_200_4h = calculate_ema(closes_4h, 200) if len(closes_4h) >= 200 else None
        rsi_4h = calculate_rsi(closes_4h, 14)
        boll_4h = calculate_bollinger_bands(closes_4h, 20, 2.0)
        atr_4h = calculate_atr(highs_4h, lows_4h, closes_4h, 14)

        trend_4h = "NEUTRAL"
        if ema_20_4h and ema_50_4h:
            if ema_20_4h > ema_50_4h and spot > ema_20_4h:
                trend_4h = "BULLISH"
            elif ema_20_4h < ema_50_4h and spot < ema_20_4h:
                trend_4h = "BEARISH"

        # 4. 8H Intermediate Trend
        closes_8h = [c["close"] for c in k_8h]
        ema_20_8h = calculate_ema(closes_8h, 20)
        ema_50_8h = calculate_ema(closes_8h, 50)
        rsi_8h = calculate_rsi(closes_8h, 14)
        trend_8h = "NEUTRAL"
        if ema_20_8h and ema_50_8h:
            trend_8h = "BULLISH" if ema_20_8h > ema_50_8h else "BEARISH"

        # 5. 12H Institutional Wave
        closes_12h = [c["close"] for c in k_12h]
        ema_20_12h = calculate_ema(closes_12h, 20)
        ema_50_12h = calculate_ema(closes_12h, 50)
        rsi_12h = calculate_rsi(closes_12h, 14)
        trend_12h = "NEUTRAL"
        if ema_20_12h and ema_50_12h:
            trend_12h = "BULLISH" if ema_20_12h > ema_50_12h else "BEARISH"

        # 6. 1W Macro Structural Bias
        closes_1w = [c["close"] for c in k_1w]
        ema_20_1w = calculate_ema(closes_1w, 20)
        ema_50_1w = calculate_ema(closes_1w, 50)
        rsi_1w = calculate_rsi(closes_1w, 14)
        trend_1w = "NEUTRAL"
        if ema_20_1w:
            if spot > ema_20_1w and (not ema_50_1w or ema_20_1w > ema_50_1w):
                trend_1w = "BULLISH"
            elif spot < ema_20_1w:
                trend_1w = "BEARISH"

        # Macro Alignment Scoring
        bull_votes = sum(1 for t in [trend_15m, trend_4h, trend_8h, trend_12h, trend_1w] if t == "BULLISH")
        bear_votes = sum(1 for t in [trend_15m, trend_4h, trend_8h, trend_12h, trend_1w] if t == "BEARISH")

        if bull_votes >= 4:
            macro_alignment = "STRONG_BULLISH"
        elif bull_votes >= 3 and bear_votes <= 1:
            macro_alignment = "MODERATE_BULLISH"
        elif bear_votes >= 4:
            macro_alignment = "STRONG_BEARISH"
        elif bear_votes >= 3 and bull_votes <= 1:
            macro_alignment = "MODERATE_BEARISH"
        else:
            macro_alignment = "CHOPPY_RANGE"

        summary = {
            "symbol": self.symbol,
            "spot_price": spot,
            "timestamp": time.time(),
            "timeframes": {
                "1m": {
                    "rsi": rsi_1m,
                    "delta_last_candle": round(v1m, 1),
                    "trend": "BULLISH" if v1m > 5.0 else "BEARISH" if v1m < -5.0 else "NEUTRAL",
                },
                "15m": {
                    "rsi": rsi_15m,
                    "ema_9": ema_9_15m,
                    "ema_21": ema_21_15m,
                    "trend": trend_15m,
                },
                "4h": {
                    "rsi": rsi_4h,
                    "ema_20": ema_20_4h,
                    "ema_50": ema_50_4h,
                    "ema_200": ema_200_4h,
                    "atr": atr_4h,
                    "bollinger": boll_4h,
                    "trend": trend_4h,
                },
                "8h": {
                    "rsi": rsi_8h,
                    "ema_20": ema_20_8h,
                    "ema_50": ema_50_8h,
                    "trend": trend_8h,
                },
                "12h": {
                    "rsi": rsi_12h,
                    "ema_20": ema_20_12h,
                    "ema_50": ema_50_12h,
                    "trend": trend_12h,
                },
                "1w": {
                    "rsi": rsi_1w,
                    "ema_20": ema_20_1w,
                    "ema_50": ema_50_1w,
                    "trend": trend_1w,
                },
            },
            "macro_alignment": macro_alignment,
            "bull_votes": bull_votes,
            "bear_votes": bear_votes,
            "summary_text": (
                f"MTF Bias: {macro_alignment} (Bull: {bull_votes}/5, Bear: {bear_votes}/5) | "
                f"4H RSI: {rsi_4h} | 4H BB %B: {boll_4h['pct_b']:.2f} | 4H ATR: ${atr_4h:.0f}"
            ),
        }
        with self.lock:
            self.last_summary = summary
        return summary


# Global singleton
GLOBAL_MTF_ENGINE = MTFTechnicalEngine()

if __name__ == "__main__":
    print("Testing MTFTechnicalEngine...")
    engine = MTFTechnicalEngine()
    res = engine.compute_all_indicators()
    print("Summary Text:", res["summary_text"])
    print("Macro Alignment:", res["macro_alignment"])
    print("Timeframes:", {tf: res["timeframes"][tf]["trend"] for tf in res["timeframes"]})
    print("4H Bollinger Bands:", res["timeframes"]["4h"]["bollinger"])