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


def _tick(chg, vol):
    return {"priceChangePercent": str(chg), "quoteVolume": str(vol),
            "lastPrice": "1.0", "highPrice": "1.2", "lowPrice": "0.8"}


def test_select_gainers_only_gainers():
    """24h-gainers sleeve (2026-09-27): gainers only, sorted desc, guarded."""
    scanner = BinanceUniverseScanner(include_movers=True)
    tickers = {
        "PUMPUSDT": _tick(25.0, 100_000_000),
        "MIDUSDT": _tick(10.0, 60_000_000),
        "DUMPUSDT": _tick(-30.0, 200_000_000),   # big loser -> excluded
        "FLATUSDT": _tick(4.9, 100_000_000),     # below +5% -> excluded
        "SMALLUSDT": _tick(50.0, 1_000_000),     # illiquid -> excluded
        "BTCUPUSDT": _tick(40.0, 100_000_000),   # leveraged -> excluded
        "BTCUSDT": _tick(60.0, 999_999_999),     # TOP_20 -> excluded
        "XAUUSDT": _tick(70.0, 999_999_999),     # gold -> excluded
    }
    res = scanner._select_gainers(tickers, limit=10)
    syms = [r["symbol"] for r in res]
    assert syms == ["PUMPUSDT", "MIDUSDT"]  # gainers only, desc by change
    assert all(r["tier"] == "24H_GAINER" for r in res)


def test_select_gainers_respects_limit():
    scanner = BinanceUniverseScanner(include_movers=True)
    many = {f"A{i:02d}USDT": _tick(6 + i, 60_000_000) for i in range(15)}
    res = scanner._select_gainers(many, limit=10)
    assert len(res) == 10
    changes = [r["change_24h_pct"] for r in res]
    assert changes == sorted(changes, reverse=True)


def test_hunting_watchlist_appends_gainers(tmp_path):
    """Frozen core stays first and intact; gainers appended once, deduped."""
    from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    core = list(trader._frozen_universe)
    trader.universe_scanner.cached_movers = [
        {"symbol": "PUMPUSDT"},
        {"symbol": "BTCUSDT"},  # already in frozen core -> must not duplicate
    ]
    wl = trader._get_hunting_watchlist()
    assert wl[:len(core)] == core
    assert wl.count("PUMPUSDT") == 1
    assert wl.count("BTCUSDT") == 1
    assert wl.index("PUMPUSDT") > wl.index(core[-1])
    # no movers -> exactly the frozen core (graceful degradation)
    trader.universe_scanner.cached_movers = []
    assert trader._get_hunting_watchlist() == core


def test_select_broad_volume_ranked_guarded():
    """Broad sleeve (2026-09-27): top by volume, liquidity-screened, guarded."""
    from scripts.binance_universe_scanner import BROAD_MIN_VOLUME_USD
    scanner = BinanceUniverseScanner()
    tickers = {
        "BIGUSDT": _tick(1.0, 500_000_000),
        "MIDUSDT": _tick(-2.0, 100_000_000),   # loser OK: ranked by volume, not change
        "SMALLUSDT": _tick(3.0, 5_000_000),    # below $10M floor -> excluded
        "TINYUSDT": _tick(3.0, BROAD_MIN_VOLUME_USD - 1),  # just under floor
        "EDGEUSDT": _tick(3.0, BROAD_MIN_VOLUME_USD),      # exactly at floor -> kept
        "BTCUPUSDT": _tick(9.0, 900_000_000),  # leveraged -> excluded
        "ETHUSDT": _tick(2.0, 800_000_000),    # TOP_50 core -> excluded
        "XAUUSDT": _tick(0.5, 700_000_000),    # gold -> excluded
    }
    res = scanner._select_broad(tickers, limit=10)
    syms = [r["symbol"] for r in res]
    assert syms == ["BIGUSDT", "MIDUSDT", "EDGEUSDT"]  # volume desc
    assert all(r["tier"] == "BROAD_MKT" for r in res)
    vols = [r["volume_24h_usd"] for r in res]
    assert vols == sorted(vols, reverse=True)


def test_select_broad_respects_limit():
    scanner = BinanceUniverseScanner()
    many = {f"B{i:02d}USDT": _tick(1.0, 20_000_000 + i * 1_000_000) for i in range(60)}
    res = scanner._select_broad(many, limit=40)
    assert len(res) == 40


def test_hunting_watchlist_appends_broad_after_gainers(tmp_path):
    """Order: frozen core -> gainers sleeve -> broad sleeve; deduped."""
    from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    core = list(trader._frozen_universe)
    trader.universe_scanner.cached_movers = [{"symbol": "PUMPUSDT"}]
    trader.universe_scanner.cached_broad = [
        {"symbol": "VOL1USDT"},
        {"symbol": "BTCUSDT"},   # already in frozen core -> must not duplicate
        {"symbol": "PUMPUSDT"},  # already in gainers -> must not duplicate
    ]
    wl = trader._get_hunting_watchlist()
    assert wl[:len(core)] == core
    assert wl[len(core)] == "PUMPUSDT"
    assert wl[len(core) + 1] == "VOL1USDT"
    assert wl.count("BTCUSDT") == 1
    assert wl.count("PUMPUSDT") == 1
    assert wl.count("VOL1USDT") == 1
    # no sleeves -> exactly the frozen core (graceful degradation)
    trader.universe_scanner.cached_movers = []
    trader.universe_scanner.cached_broad = []
    assert trader._get_hunting_watchlist() == core


def test_fetch_watchlist_data_parallel(tmp_path, monkeypatch):
    """Parallel kline fetch maps every symbol; failures become None."""
    from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        if symbol == "BADUSDT":
            return None
        return {"price": 1.0, "closes": [1.0] * 100}

    monkeypatch.setattr(trader, "_fetch_market_data", fake_fetch)
    out = trader._fetch_watchlist_data(["AAUSDT", "BADUSDT", "CCUSDT"])
    assert set(out) == {"AAUSDT", "BADUSDT", "CCUSDT"}
    assert out["BADUSDT"] is None
    assert out["AAUSDT"]["price"] == 1.0
    assert sorted(calls) == ["AAUSDT", "BADUSDT", "CCUSDT"]
    assert trader._fetch_watchlist_data([]) == {}
