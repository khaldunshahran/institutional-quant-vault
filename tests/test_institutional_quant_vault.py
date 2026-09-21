"""
Unit Tests for $100K Institutional Multi-Asset Quant Vault
Author: Google Antigravity (Advanced Agentic Systems)

Tests:
1. Dynamic Multi-Symbol Order Book Depth & Whale Walls (Ensures no SOL/BTC cross-contamination)
2. Dynamic Multi-Symbol Order Flow CVD (USD notional scale-invariance & taker ratio)
3. Dynamic Session Clock Liquidity Magnets (Adaptive price formatting across BTC, SOL, DOGE)
4. $100K Monte Carlo Simulation Engine (10,000 runs, $2k target, -$2k circuit breaker)
5. Autonomous Trader multi-symbol telemetry integration
"""

import pytest
from unittest.mock import MagicMock, patch
from scripts.order_book_engine import OrderBookEngine
from scripts.order_flow_engine import OrderFlowEngine
from scripts.session_clock_engine import SessionClockEngine, _format_price
from scripts.monte_carlo_engine import MonteCarloEngine
from scripts.math_quant_engine import compute_hurst_exponent, compute_robust_mad_zscore
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader


def test_order_book_multi_symbol_isolation():
    """Verify OrderBookEngine handles multiple symbols without state cross-contamination."""
    engine = OrderBookEngine(symbol="BTCUSDT")

    # Mock L2 depth for BTC and SOL
    btc_depth = {
        "bids": [[81000.0, 50.0], [80900.0, 40.0]],
        "asks": [[81100.0, 45.0], [81200.0, 55.0]],
    }
    sol_depth = {
        "bids": [[110.0, 5000.0], [109.5, 4000.0]],
        "asks": [[110.5, 4500.0], [111.0, 6000.0]],
    }

    with patch.object(engine, "fetch_l2_depth", side_effect=lambda s: sol_depth if "SOL" in s else btc_depth):
        btc_res = engine.get_order_book_state(current_spot=81050.0, symbol="BTCUSDT")
        sol_res = engine.get_order_book_state(current_spot=110.25, symbol="SOLUSDT")

        assert btc_res["symbol"] == "BTCUSDT"
        assert sol_res["symbol"] == "SOLUSDT"

        # Crucial bug check: SOL must NOT have 0 ask depth or fake +100% OBI
        assert sol_res["ask_depth_usd"] > 0
        assert sol_res["bid_depth_usd"] > 0
        assert -100.0 <= sol_res["obi_pct"] <= 100.0
        assert sol_res["mid_price"] == 110.25


def test_order_flow_multi_symbol_usd_cvd():
    """Verify OrderFlowEngine computes scale-invariant USD CVD and taker ratio."""
    engine = OrderFlowEngine()

    # Mock candles with quote volume & taker quote volume
    mock_candles = [
        {
            "open_time": 1000 + i * 60,
            "open": 100.0 + i,
            "high": 102.0 + i,
            "low": 99.0 + i,
            "close": 101.0 + i,
            "volume": 1000.0,
            "quote_vol": 100000.0,
            "taker_buy": 600.0,
            "taker_buy_quote": 60000.0,
            "taker_sell": 400.0,
            "taker_sell_quote": 40000.0,
            "delta": 200.0,
            "delta_usd": 20000.0,
        }
        for i in range(20)
    ]

    with patch.object(engine, "fetch_klines", return_value=mock_candles):
        res = engine.get_order_flow_metrics(symbol="SOLUSDT")

        assert res["symbol"] == "SOLUSDT"
        assert res["perp_cvd_15m_usd"] == 300000.0  # 15 * 20,000
        assert res["delta_share_pct"] == 20.0       # 20,000 / 100,000 = 20%
        assert res["taker_ratio_15m"] == 1.5        # 60,000 / 40,000 = 1.5
        assert res["order_flow_bias"] in ["AGGRESSIVE_BUYING", "STRONG_BULLISH_ACCUMULATION", "MODERATE_BULLISH"]


def test_session_clock_adaptive_price_formatting():
    """Verify price formatting handles large and small asset prices gracefully."""
    assert _format_price(81500.0) == "$81,500"
    assert _format_price(110.25) == "$110.25"
    assert _format_price(2.456) == "$2.456"
    assert _format_price(0.0885) == "$0.0885"

    engine = SessionClockEngine()
    levels_doge = {
        "spot": 0.088,
        "high_24h": 0.090,
        "low_24h": 0.085,
        "volume": 50000000.0,
    }
    with patch.object(engine, "fetch_24h_levels", return_value=levels_doge):
        res = engine.get_session_and_liquidity_state(current_spot=0.088, symbol="DOGEUSDT")
        assert res["symbol"] == "DOGEUSDT"
        assert res["upper_liquidity_pool"] > 0
        assert "$0.0" in res["magnet_status"]


def test_monte_carlo_100k_simulation_scale():
    """Verify Monte Carlo simulation correctly reflects $100K Quant Vault targets and circuit breakers."""
    engine = MonteCarloEngine()
    res = engine.run_simulation(
        initial_balance=100_000.0,
        daily_target_usd=2_000.0,
        circuit_breaker_usd=2_000.0,
        days=30,
        trades_per_day=8.0,
        win_rate=0.74,
        rr_ratio=1.6,
        risk_pct=1.0,
        leverage=10,
        num_sims=500
    )

    assert res["initial_balance"] == 100_000.0
    assert res["daily_target_usd"] == 2_000.0
    assert res["circuit_breaker_usd"] == 2_000.0
    assert res["median_30d_balance"] > 100_000.0
    assert res["worst_case_drawdown_p95_pct"] < 25.0
    assert "PROVEN" in res["circuit_breaker_safety"]
    assert len(res["sample_trajectories"]) == 3


def test_autonomous_trader_evaluates_multi_symbol_correctly(tmp_path):
    """Verify AutonomousMultiAssetTrader evaluates altcoin signals using localized symbol context."""
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path))

    # Mock clean engines
    trader.economic_calendar_engine.get_news_shield_state = MagicMock(return_value={"is_blackout_active": False})
    trader.session_clock_engine.get_session_and_liquidity_state = MagicMock(return_value={"breakout_allowed": True})
    trader.order_flow_engine.get_order_flow_metrics = MagicMock(return_value={
        "order_flow_bias": "AGGRESSIVE_BUYING",
        "divergence_alert": "BULLISH_EXPANSION"
    })
    trader.order_book_engine.get_order_book_state = MagicMock(return_value={"obi_pct": 25.0})
    trader._fetch_macro_trend = MagicMock(return_value="BULLISH")
    trader.jev_engine.evaluate_futures_setup = MagicMock(return_value={"approved": True, "conviction": 4.5, "trap_risk": 0.05})

    # Linear upward trend with a volume expansion spike
    closes = [100.0 + i * 0.5 for i in range(35)]
    volumes = [50000.0] * 34 + [120000.0]  # RVOL = 2.4x breakout volume
    sol_data = {
        "price": closes[-1],
        "closes": closes,
        "highs": [c + 0.8 for c in closes],
        "lows": [c - 0.5 for c in closes],
        "volumes": volumes
    }

    sig = trader._evaluate_signal("SOLUSDT", sol_data)
    assert sig is not None
    assert sig["symbol"] == "SOLUSDT"
    assert sig["side"] == "BUY"
    assert sig["price"] == closes[-1]
    assert sig["conviction"] >= 82.0
