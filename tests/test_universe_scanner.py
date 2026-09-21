import pytest
from scripts.binance_universe_scanner import BinanceUniverseScanner, TOP_20_SYMBOLS, GOLD_SYMBOLS


def test_liquid_universe_whitelist():
    """Verify illiquid high-loss altcoins are pruned and Tier-1 prime assets are whitelisted."""
    # Ensure high-loss altcoins are excluded
    blacklisted = ["FILUSDT", "INJUSDT", "NEARUSDT"]
    for sym in blacklisted:
        assert sym not in TOP_20_SYMBOLS, f"{sym} should NOT be in TOP_20_SYMBOLS!"

    # Ensure prime liquid assets and metals are present
    assert "BTCUSDT" in TOP_20_SYMBOLS
    assert "ETHUSDT" in TOP_20_SYMBOLS
    assert "SOLUSDT" in TOP_20_SYMBOLS
    assert "XAUUSDT" in GOLD_SYMBOLS
    assert "PAXGUSDT" in GOLD_SYMBOLS


def test_scanner_pre_calculated_metrics():
    """Verify scanner caches 15m ATR, Hurst, and opportunity score."""
    scanner = BinanceUniverseScanner()
    
    # Mock ticker map & candles
    mock_item = {
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "price": 85000.0,
        "change_24h_pct": 2.5,
        "volume_24h_usd": 1_000_000_000.0,
        "high_24h": 86000.0,
        "low_24h": 84000.0,
        "tier": "TOP_20",
        "asset_category": "CRYPTO_CORE",
        "hurst_exponent": 0.58,
        "volatility_15m_pct": 0.45,
        "momentum_15m_pct": 0.35,
        "atr_15m": 350.0,
        "timeframe": "15m",
        "regime": "PERSISTENT_TREND",
        "direction": "LONG",
        "badge": "TREND LONG",
        "opportunity_score": 88.5
    }
    scanner.cached_universe = [mock_item]
    scanner.cached_gold = []
    scanner.cached_movers = []

    res = scanner.scan_universe()
    assert res["status"] == "ok"
    assert res["universe_count"] == 1
    top = res["top_candidate"]
    assert top["symbol"] == "BTCUSDT"
    assert top["timeframe"] == "15m"
    assert top["atr_15m"] == 350.0
    assert top["hurst_exponent"] == 0.58
