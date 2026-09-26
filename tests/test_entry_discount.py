"""Pullback-discount entry (Sep-2026 research, E2 variant).

The engine rests the post-only entry limit 0.50 x ATR(14) off the signal
price (below for LONG/BUY, above for SHORT/SELL) and lets it work up to
ENTRY_MAKER_WAIT_SEC (1h). Unfilled orders expire -> no trade, no chasing.
"""
from unittest.mock import MagicMock

import pytest

import scripts.autonomous_multi_asset_trader as trader_module
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from scripts.paper_fill_simulator import (
    ENTRY_DISCOUNT_ATR_MULT,
    ENTRY_MAKER_WAIT_SEC,
)
from test_risk_and_correlation import StubFillSimulator


class ExpiringStubFillSimulator(StubFillSimulator):
    """Never fills: every poll reports the order EXPIRED."""

    def poll_maker_order(self, order_id):
        return {
            "status": "EXPIRED", "order_id": order_id,
            "symbol": self._orders[order_id]["symbol"],
            "side": self._orders[order_id]["side"],
            "limit_price": self._orders[order_id]["limit_price"],
            "requested_qty": self._orders[order_id]["quantity"],
            "filled_qty": 0.0, "remaining_qty": self._orders[order_id]["quantity"],
            "new_filled_qty": 0.0, "new_fee_usd": 0.0,
            "avg_price": None, "is_maker": True, "reason": "expired",
            "timestamp_utc": "2026-09-26 00:00:00 UTC",
        }


@pytest.fixture
def discount_trader(tmp_path):
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    trader.positions_file = tmp_path / "autonomous_positions.json"
    trader.history_file = tmp_path / "autonomous_trade_history.json"
    trader.state_file = tmp_path / "autonomous_state.json"
    trader.pnl_ledger_file = tmp_path / "pnl_ledger.jsonl"
    trader.decision_log_file = tmp_path / "decision_log.jsonl"
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    trader.open_positions = {}
    trader.pending_entries = {}
    trader.PRICE_CACHE_TTL_SEC = 0
    return trader


def make_signal(symbol="BTCUSDT", side="BUY", price=100.0, atr_14=2.0, **kw):
    sig = {
        "symbol": symbol, "side": side, "price": price, "atr_14": atr_14,
        "sl_pct": 0.02, "tp1_pct": 0.015, "tp2_pct": 0.035, "setup": "TEST",
    }
    sig.update(kw)
    return sig


def test_research_constants():
    assert ENTRY_DISCOUNT_ATR_MULT == 0.5
    assert ENTRY_MAKER_WAIT_SEC == 3600


def test_buy_limit_rests_half_atr_below_signal(discount_trader):
    trader = discount_trader
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    trader._open_position(make_signal(side="BUY", price=100.0, atr_14=2.0))
    pe = trader.pending_entries["BTCUSDT"]
    assert pe["limit_price"] == pytest.approx(99.0)  # 100 - 0.5*2.0
    assert pe["signal_price"] == 100.0
    assert pe["entry_discount_atr"] == 0.5


def test_sell_limit_rests_half_atr_above_signal(discount_trader):
    trader = discount_trader
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    trader._open_position(make_signal(side="SELL", price=100.0, atr_14=2.0))
    pe = trader.pending_entries["BTCUSDT"]
    assert pe["limit_price"] == pytest.approx(101.0)  # 100 + 0.5*2.0


def test_entry_order_works_up_to_one_hour(discount_trader):
    trader = discount_trader
    sim = StubFillSimulator()
    seen = {}

    orig_place = sim.place_maker_order

    def spy(symbol, side, quantity, limit_price, max_wait_sec=120):
        seen["max_wait_sec"] = max_wait_sec
        return orig_place(symbol, side, quantity, limit_price,
                          max_wait_sec=max_wait_sec)

    sim.place_maker_order = spy
    trader.execution_adapter.fill_simulator = sim
    trader._open_position(make_signal())
    assert seen["max_wait_sec"] == 3600


def test_fill_at_discount_sets_position_from_limit(discount_trader):
    trader = discount_trader
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    trader._open_position(make_signal(side="BUY", price=100.0, atr_14=2.0))
    trader._poll_pending_entries()
    pos = trader.open_positions["BTCUSDT"]
    assert pos["entry_price"] == pytest.approx(99.0)
    # SL/TP are ATR-calibrated from the actual (discounted) fill.
    assert pos["stop_loss"] == pytest.approx(99.0 * (1 - 0.02))


def test_unfilled_entry_expires_into_no_position(discount_trader):
    trader = discount_trader
    trader.execution_adapter.fill_simulator = ExpiringStubFillSimulator()
    trader._open_position(make_signal())
    assert "BTCUSDT" in trader.pending_entries
    trader._poll_pending_entries()
    assert "BTCUSDT" not in trader.pending_entries
    assert "BTCUSDT" not in trader.open_positions


def test_missing_atr_falls_back_to_signal_price(discount_trader):
    trader = discount_trader
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    sig = make_signal(price=100.0)
    del sig["atr_14"]
    trader._open_position(sig)
    pe = trader.pending_entries["BTCUSDT"]
    assert pe["limit_price"] == pytest.approx(100.0)
