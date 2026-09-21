"""
Mathematical Quantitative Trading Engine for TypeSafe Jev
Author: Google Antigravity (Advanced Agentic Systems)

Implements elite quantitative mechanics:
1. Hurst Exponent (H) via Rescaled Range (R/S) Analysis (Trend vs. Mean-Reversion vs. Random Walk)
2. Ornstein-Uhlenbeck (O-U) Mean Reversion Half-Life
3. Fractional Kelly Criterion for Optimal Position Sizing
4. Robust Z-Scores via Median Absolute Deviation (MAD)
"""

import time
import math
import logging
import urllib.request
import json
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger(__name__)


def compute_hurst_exponent(prices: List[float]) -> Tuple[float, str]:
    """
    Calculate the Hurst Exponent (H) using Rescaled Range (R/S) analysis.
    H >= 0.56 -> Persistent / Trending (Momentum)
    H <= 0.44 -> Anti-Persistent (Mean-Reverting)
    0.47 <= H <= 0.53 -> Geometric Brownian Motion (Pure Random Walk)
    """
    if len(prices) < 32:
        return 0.50, "INSUFFICIENT_DATA"

    returns = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0 and prices[i] > 0:
            returns.append(math.log(prices[i] / prices[i-1]))
    
    N = len(returns)
    if N < 20:
        return 0.50, "INSUFFICIENT_DATA"

    lags = [8, 16, 32, 64]
    lags = [lag for lag in lags if lag <= N // 2]
    if len(lags) < 2:
        lags = [N // 4, N // 2]

    log_lags = []
    log_rs = []

    for lag in lags:
        rs_values = []
        num_chunks = N // lag
        for chunk_idx in range(num_chunks):
            chunk = returns[chunk_idx * lag : (chunk_idx + 1) * lag]
            if len(chunk) < lag:
                continue
            mean_chunk = sum(chunk) / len(chunk)
            
            cum_dev = []
            cur_sum = 0.0
            for val in chunk:
                cur_sum += (val - mean_chunk)
                cum_dev.append(cur_sum)
            
            r = max(cum_dev) - min(cum_dev)
            variance = sum((x - mean_chunk) ** 2 for x in chunk) / len(chunk)
            s = math.sqrt(variance) if variance > 0 else 1e-9
            
            if s > 1e-9:
                rs_values.append(r / s)
        
        if rs_values:
            avg_rs = sum(rs_values) / len(rs_values)
            if avg_rs > 0:
                log_lags.append(math.log(lag))
                log_rs.append(math.log(avg_rs))

    if len(log_lags) < 2:
        return 0.50, "RANDOM_WALK"

    n_pts = len(log_lags)
    mean_x = sum(log_lags) / n_pts
    mean_y = sum(log_rs) / n_pts
    
    denom = sum((x - mean_x) ** 2 for x in log_lags)
    if denom == 0:
        return 0.50, "RANDOM_WALK"
    
    numer = sum((log_lags[i] - mean_x) * (log_rs[i] - mean_y) for i in range(n_pts))
    h = numer / denom
    h = max(0.01, min(0.99, round(h, 3)))

    if h >= 0.56:
        regime = "TRENDING_MOMENTUM"
    elif h <= 0.44:
        regime = "MEAN_REVERTING"
    elif 0.47 <= h <= 0.53:
        regime = "RANDOM_WALK_CHOP"
    else:
        regime = "WEAK_TRANSITIONAL"

    return h, regime


def compute_ornstein_uhlenbeck(prices: List[float], dt_minutes: float = 15.0) -> Dict[str, Any]:
    """
    Estimate Ornstein-Uhlenbeck continuous mean reversion parameters:
    dX_t = theta * (mu - X_t) dt + sigma * dW_t
    Half-life tau = ln(2) / theta (in minutes)
    """
    if len(prices) < 20:
        return {"theta": 0.0, "half_life_minutes": 0.0, "equilibrium_mu": prices[-1] if prices else 0.0, "is_mean_reverting": False}

    x = prices[:-1]
    y = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    n = len(x)

    mean_x = sum(x) / n
    mean_y = sum(y) / n
    denom = sum((val - mean_x) ** 2 for val in x)
    
    if denom == 0:
        return {"theta": 0.0, "half_life_minutes": 999.0, "equilibrium_mu": round(mean_x, 2), "is_mean_reverting": False}
    
    numer = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    b = numer / denom
    a = mean_y - b * mean_x

    if b < 0:
        theta = -b / dt_minutes
        half_life_min = round(math.log(2) / theta, 1) if theta > 0 else 999.0
        long_term_mu = round(-a / b, 2)
    else:
        theta = 0.0
        half_life_min = 999.0
        long_term_mu = round(prices[-1], 2)

    return {
        "theta": round(theta, 5),
        "half_life_minutes": half_life_min,
        "equilibrium_mu": long_term_mu,
        "is_mean_reverting": (b < -0.01 and half_life_min < 180.0)
    }


def compute_fractional_kelly(win_prob: float, reward_to_risk: float, kelly_fraction: float = 0.25) -> Dict[str, Any]:
    """
    Calculates Fractional Kelly Criterion:
    f* = (b * p - (1 - p)) / b
    Returns mathematically optimal risk fraction and recommended leverage.
    """
    p = max(0.01, min(0.99, float(win_prob)))
    b = max(0.1, float(reward_to_risk))
    q = 1.0 - p

    raw_kelly = (b * p - q) / b
    if raw_kelly <= 0:
        return {
            "raw_kelly": round(raw_kelly, 4),
            "fractional_kelly": 0.0,
            "optimal_risk_pct": 0.0,
            "recommended_leverage": 1,
            "ev_status": "NEGATIVE_EXPECTANCY"
        }

    frac_kelly = raw_kelly * kelly_fraction
    risk_pct = round(max(0.5, min(2.5, frac_kelly * 100.0)), 2)

    if frac_kelly >= 0.08:
        rec_lev = 8
    elif frac_kelly >= 0.05:
        rec_lev = 6
    elif frac_kelly >= 0.03:
        rec_lev = 5
    elif frac_kelly >= 0.015:
        rec_lev = 4
    else:
        rec_lev = 3

    return {
        "raw_kelly": round(raw_kelly, 4),
        "fractional_kelly": round(frac_kelly, 4),
        "optimal_risk_pct": risk_pct,
        "recommended_leverage": rec_lev,
        "ev_status": "POSITIVE_EXPECTANCY"
    }


def compute_robust_mad_zscore(prices: List[float]) -> Dict[str, Any]:
    """
    Calculates Robust Z-Score via Median Absolute Deviation (MAD)
    Z_robust = (P_current - Median) / (1.4826 * MAD)
    Detects 3-sigma fat-tail anomalies.
    """
    if len(prices) < 15:
        return {"robust_z": 0.0, "is_fat_tail_spike": False, "tail_direction": "NORMAL"}

    sorted_p = sorted(prices)
    mid = len(sorted_p) // 2
    if len(sorted_p) % 2 == 0:
        median = (sorted_p[mid - 1] + sorted_p[mid]) / 2.0
    else:
        median = sorted_p[mid]

    deviations = sorted([abs(x - median) for x in prices])
    dev_mid = len(deviations) // 2
    if len(deviations) % 2 == 0:
        mad = (deviations[dev_mid - 1] + deviations[dev_mid]) / 2.0
    else:
        mad = deviations[dev_mid]

    if mad <= 0:
        return {"robust_z": 0.0, "is_fat_tail_spike": False, "tail_direction": "NORMAL"}

    scale = 1.4826 * mad
    current = prices[-1]
    z = round((current - median) / scale, 2)

    return {
        "robust_z": z,
        "median_price": round(median, 2),
        "mad": round(mad, 2),
        "is_fat_tail_spike": abs(z) >= 2.5,
        "tail_direction": "UPPER_SPIKE" if z >= 2.5 else ("LOWER_SPIKE" if z <= -2.5 else "NORMAL")
    }


class MathQuantEngine:
    """
    Continuous Mathematical Quant Engine polling Binance klines
    and computing Hurst, O-U, and MAD Z-scores.
    """

    def __init__(self, cache_ttl_sec: float = 15.0):
        self.cache_ttl = cache_ttl_sec
        self.last_update = 0.0
        self.cached_metrics = {}

    def fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 100) -> List[float]:
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=3.5)
            data = json.loads(resp.read().decode())
            closes = [float(k[4]) for k in data]
            return closes
        except Exception as e:
            logger.warning(f"[MATH] Kline fetch error: {e}")
            return []

    def get_quant_metrics(self) -> Dict[str, Any]:
        now = time.time()
        if self.cached_metrics and (now - self.last_update) < self.cache_ttl:
            return self.cached_metrics

        closes_15m = self.fetch_klines(interval="15m", limit=100)
        closes_5m = self.fetch_klines(interval="5m", limit=80)

        if not closes_15m:
            return self.cached_metrics or {
                "hurst": 0.50,
                "regime": "RANDOM_WALK_CHOP",
                "is_random_walk": True,
                "ou_half_life_min": 0.0,
                "robust_z": 0.0
            }

        h, regime = compute_hurst_exponent(closes_15m)
        ou = compute_ornstein_uhlenbeck(closes_15m, dt_minutes=15.0)
        mad_z = compute_robust_mad_zscore(closes_5m if closes_5m else closes_15m)

        is_random_walk = (0.47 <= h <= 0.53)

        self.cached_metrics = {
            "hurst": h,
            "regime": regime,
            "is_random_walk": is_random_walk,
            "ou_half_life_min": ou["half_life_minutes"],
            "ou_is_mean_reverting": ou["is_mean_reverting"],
            "ou_equilibrium": ou["equilibrium_mu"],
            "robust_z": mad_z["robust_z"],
            "is_fat_tail_spike": mad_z["is_fat_tail_spike"],
            "tail_direction": mad_z["tail_direction"],
            "last_updated": now
        }
        self.last_update = now
        return self.cached_metrics


if __name__ == "__main__":
    engine = MathQuantEngine()
    metrics = engine.get_quant_metrics()
    print("[TEST] Math Quant Engine Metrics:")
    print(json.dumps(metrics, indent=2))
    
    k = compute_fractional_kelly(win_prob=0.68, reward_to_risk=1.85)
    print("[TEST] Fractional Kelly Sizing:", json.dumps(k, indent=2))
