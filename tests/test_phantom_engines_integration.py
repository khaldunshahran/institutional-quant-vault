import pytest
from unittest.mock import MagicMock
from scripts.economic_calendar_engine import EconomicCalendarEngine
from scripts.session_clock_engine import SessionClockEngine
from scripts.order_flow_engine import OrderFlowEngine
from scripts.order_book_engine import OrderBookEngine
from scripts.jev_decision_engine import JevDecisionEngine
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader


def test_economic_calendar_news_shield_blocks_entries():
    """Verify that when news blackout is active, trade signals are blocked."""
    trader = AutonomousMultiAssetTrader()
    
    # Mock news shield active
    trader.economic_calendar_engine.get_news_shield_state = MagicMock(return_value={
        "is_blackout_active": True,
        "blackout_reason": "US CPI Release",
        "shield_status": "BLACKOUT_ACTIVE"
    })

    dummy_data = {
        "price": 100.0,
        "closes": [100.0 + i for i in range(25)],
        "highs": [102.0 + i for i in range(25)],
        "lows": [98.0 + i for i in range(25)],
        "volumes": [1000.0] * 25
    }

    signal = trader._evaluate_signal("BTCUSDT", dummy_data)
    assert signal is None, "Signal must be None when News Blackout is active!"
    assert "BLACKOUT" in trader.latest_decision.get("setup", "")


def test_session_clock_blocks_asia_breakout():
    """Verify breakout signals are blocked during low-liquidity Asia Range session."""
    trader = AutonomousMultiAssetTrader()
    
    # Mock session clock returning Asia Range with breakout_allowed = False
    trader.session_clock_engine.get_session_and_liquidity_state = MagicMock(return_value={
        "session_name": "ASIA_RANGE",
        "breakout_allowed": False
    })
    trader.economic_calendar_engine.get_news_shield_state = MagicMock(return_value={
        "is_blackout_active": False
    })

    # Strong breakout data (high momentum and high Hurst)
    dummy_data = {
        "price": 100.0,
        "closes": [90.0 + (i * 0.5) for i in range(30)],
        "highs": [91.0 + (i * 0.5) for i in range(30)],
        "lows": [89.0 + (i * 0.5) for i in range(30)],
        "volumes": [1000.0] * 30
    }

    signal = trader._evaluate_signal("BTCUSDT", dummy_data)
    assert signal is None, "Breakout signals must be blocked during dead Asia Range!"


def test_order_flow_cvd_divergence_blocks_long():
    """Verify long signal is rejected if CVD order flow is showing aggressive selling / bearish divergence."""
    trader = AutonomousMultiAssetTrader()
    
    trader.economic_calendar_engine.get_news_shield_state = MagicMock(return_value={"is_blackout_active": False})
    trader.session_clock_engine.get_session_and_liquidity_state = MagicMock(return_value={"breakout_allowed": True})
    
    # Mock aggressive selling in order flow
    trader.order_flow_engine.get_order_flow_metrics = MagicMock(return_value={
        "order_flow_bias": "AGGRESSIVE_SELLING",
        "divergence_alert": "BEARISH_EXHAUSTION"
    })

    dummy_data = {
        "price": 100.0,
        "closes": [90.0 + (i * 0.5) for i in range(30)],
        "highs": [91.0 + (i * 0.5) for i in range(30)],
        "lows": [89.0 + (i * 0.5) for i in range(30)],
        "volumes": [1000.0] * 30
    }

    signal = trader._evaluate_signal("BTCUSDT", dummy_data)
    assert signal is None, "Long entry must be blocked when CVD order flow shows aggressive selling!"


def test_order_book_obi_calculation():
    """Verify Order Book Imbalance (OBI) formula correctly measures bid vs ask depth."""
    engine = OrderBookEngine(symbol="BTCUSDT")
    
    # Depth with 80% bids, 20% asks
    engine.fetch_l2_depth = MagicMock(return_value={
        "bids": [[100.0, 8.0]],
        "asks": [[100.5, 2.0]]
    })

    state = engine.get_order_book_state(current_spot=100.25)
    assert state["obi_pct"] > 0, "OBI should be positive with heavier bid volume"
    assert 59.0 <= state["obi_pct"] <= 61.0, f"OBI ratio should be approx 60%, got {state['obi_pct']}"


def test_jev_decision_engine_futures_evaluation():
    """Verify JevDecisionEngine evaluates futures payloads and gates trades."""
    engine = JevDecisionEngine()
    engine.is_live_ready = MagicMock(return_value=False)
    
    # Positive setup
    long_payload = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": 85000.0,
        "hurst": 0.62,
        "z_score": 1.45,
        "mom_pct": 0.85,
        "rvol": 1.6,
        "order_flow_bias": "STRONG_BULLISH_ACCUMULATION",
        "obi_pct": 35.0,
        "regime": "MOMENTUM_EXPANSION"
    }
    res = engine.evaluate_futures_setup(long_payload)
    assert res["approved"] is True
    assert res["verdict"] == "CONFIRM_LONG"
    assert res["conviction"] >= 3.0
    assert res["trap_risk"] <= 0.40

    # Conflicting setup (trying to buy into aggressive selling and sell wall)
    bad_payload = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": 85000.0,
        "hurst": 0.52,
        "z_score": 0.30,
        "mom_pct": -0.10,
        "rvol": 0.7,
        "order_flow_bias": "AGGRESSIVE_SELLING",
        "obi_pct": -45.0,
        "regime": "CHOPPY"
    }
    bad_res = engine.evaluate_futures_setup(bad_payload)
    assert bad_res["approved"] is False
    assert bad_res["verdict"] == "PASS"
