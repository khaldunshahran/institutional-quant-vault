import pytest
import math
from scripts.binance_universe_scanner import BinanceUniverseScanner
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader


def test_atr_calculation_accuracy():
    """Verify 14-period ATR calculation matches True Range formula."""
    scanner = BinanceUniverseScanner()
    
    # Generate 20 candles with known constant true range of 10.0
    candles = []
    base_price = 100.0
    for i in range(25):
        candles.append({
            "open": base_price,
            "high": base_price + 6.0,
            "low": base_price - 4.0,  # High - Low = 10.0
            "close": base_price + 1.0,
            "volume": 1000.0
        })
        base_price += 1.0

    atr = scanner._calc_atr(candles, period=14)
    assert round(atr, 2) == 10.0, f"Expected ATR 10.0, got {atr}"


def test_atr_short_history_fallback():
    """Verify ATR handles fewer than period + 1 candles safely."""
    scanner = BinanceUniverseScanner()
    candles = [{"high": 100.0, "low": 90.0, "close": 95.0, "volume": 100.0}] * 5
    atr = scanner._calc_atr(candles, period=14)
    assert atr == 0.0


def test_hurst_exponent_trending_vs_mean_reverting():
    """Verify Hurst exponent correctly identifies persistent trend vs mean reversion."""
    scanner = BinanceUniverseScanner()
    
    # Strongly trending series
    trending_prices = [100.0 + (i * 2.0) for i in range(50)]
    h_trend = scanner._calc_hurst(trending_prices)
    assert h_trend >= 0.50, f"Expected H >= 0.50 for strong trend, got {h_trend}"

    # Highly oscillating mean-reverting series
    reverting_prices = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(50)]
    h_rev = scanner._calc_hurst(reverting_prices)
    assert h_rev < 0.50, f"Expected H < 0.50 for mean reversion, got {h_rev}"


def test_trader_multi_timeframe_data_structure():
    """Verify trader accepts 15m candle structure and computes ATR."""
    trader = AutonomousMultiAssetTrader()
    highs = [105.0 + i for i in range(30)]
    lows = [95.0 + i for i in range(30)]
    closes = [100.0 + i for i in range(30)]
    
    atr = trader._calc_atr(highs, lows, closes, period=14)
    assert atr > 0, "ATR must be positive"
    assert atr >= 10.0, "ATR should reflect at least High - Low of 10"
