import time
from unittest.mock import MagicMock
import pytest
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from scripts.paper_fill_simulator import MAKER_FEE_RATE, TAKER_FEE_RATE


class StubFillSimulator:
    """Deterministic test double: every order fills in full at the requested
    price with the correct fee tier. Used ONLY in tests — production paper
    trading goes through the realistic PaperFillSimulator."""

    def simulate_maker_fill(self, symbol, side, quantity, limit_price, max_wait_sec=120):
        notional = round(quantity * limit_price, 2)
        return {
            "status": "FILLED", "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": quantity, "avg_price": limit_price,
            "notional_usd": notional,
            "fee_usd": round(notional * MAKER_FEE_RATE, 4), "is_maker": True,
            "reason": None,
            "timestamp_utc": "2026-09-21 00:00:00 UTC",
        }

    def simulate_taker_fill(self, symbol, side, quantity, reference_price=None):
        price = reference_price if reference_price else 100.0
        notional = round(quantity * price, 2)
        return {
            "status": "FILLED", "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": quantity, "avg_price": price,
            "notional_usd": notional,
            "fee_usd": round(notional * TAKER_FEE_RATE, 4), "is_maker": False,
            "reason": None, "degraded": False,
            "timestamp_utc": "2026-09-21 00:00:00 UTC",
        }


@pytest.fixture
def isolated_trader(tmp_path):
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    trader.positions_file = tmp_path / "autonomous_positions.json"
    trader.history_file = tmp_path / "autonomous_trade_history.json"
    trader.state_file = tmp_path / "autonomous_state.json"
    trader.pnl_ledger_file = tmp_path / "pnl_ledger.jsonl"
    trader.decision_log_file = tmp_path / "decision_log.jsonl"
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    trader.open_positions = {}
    # Deterministic fills: no network in tests.
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    return trader


def test_crypto_correlation_cap(isolated_trader):
    """Verify that no more than 3 directional crypto positions can be opened simultaneously."""
    trader = isolated_trader

    # Open 3 crypto positions
    cryptos = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for sym in cryptos:
        sig = {
            "symbol": sym,
            "side": "BUY",
            "price": 100.0,
            "atr_14": 2.0,
            "sl_pct": 0.02,
            "tp1_pct": 0.015,
            "tp2_pct": 0.035,
            "setup": "TEST"
        }
        trader._open_position(sig)

    assert len(trader.open_positions) == 3

    # Attempt to open a 4th crypto (AVAXUSDT)
    fourth_crypto = {
        "symbol": "AVAXUSDT",
        "side": "BUY",
        "price": 25.0,
        "atr_14": 0.5,
        "sl_pct": 0.02,
        "tp1_pct": 0.015,
        "tp2_pct": 0.035,
        "setup": "TEST"
    }
    trader._open_position(fourth_crypto)
    assert "AVAXUSDT" not in trader.open_positions, "4th crypto should be blocked by correlation cap!"
    assert len(trader.open_positions) == 3

    # Gold (XAUUSDT) is an institutional uncorrelated metal, so it MUST be allowed as 4th position
    gold_sig = {
        "symbol": "XAUUSDT",
        "side": "BUY",
        "price": 4350.0,
        "atr_14": 15.0,
        "sl_pct": 0.01,
        "tp1_pct": 0.01,
        "tp2_pct": 0.025,
        "setup": "TEST"
    }
    trader._open_position(gold_sig)
    assert "XAUUSDT" in trader.open_positions, "Gold should be permitted as uncorrelated hedge!"
    assert len(trader.open_positions) == 4


def test_early_break_even_ratchet(isolated_trader):
    """Verify stop loss is ratcheted to fee-protected break-even when profit reaches +1.0 ATR."""
    trader = isolated_trader

    entry_price = 100.0
    atr = 2.0
    sig = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": entry_price,
        "atr_14": atr,
        "sl_pct": 0.02,   # SL = 98.0
        "tp1_pct": 0.03,  # TP1 = 103.0
        "tp2_pct": 0.06,  # TP2 = 106.0
        "setup": "TEST"
    }
    trader._open_position(sig)
    pos = trader.open_positions["BTCUSDT"]

    assert pos["stop_loss"] == 98.0
    assert not pos["early_be_hit"]
    assert not pos["break_even_active"]

    # Mock market data at +1.1 ATR profit ($102.20)
    # This is >= 1.0 ATR ($2.00) so Early BE MUST trigger before reaching TP1 ($103.0)
    trader._fetch_market_data = lambda s: {"price": 102.20, "closes": [102.20]*20}
    trader._manage_open_positions()

    pos = trader.open_positions["BTCUSDT"]
    assert pos["early_be_hit"] is True, "Early BE should be marked as hit"
    assert pos["break_even_active"] is True, "Break-even should be active"
    # Stop loss should now be at entry + fee buffer (100.0 + 0.03 = 100.03)
    assert pos["stop_loss"] >= entry_price, "Stop loss must be ratcheted to or above entry price!"
    assert not pos["tp1_hit"], "TP1 should NOT have been hit yet"


def test_no_alpha_decay_premature_kill(isolated_trader):
    """Verify positions older than 45 minutes (2700s) are NOT prematurely closed if within SL/TP bounds."""
    trader = isolated_trader

    now = time.time()
    old_time = now - 3600.0  # Opened 60 minutes ago!
    trader.open_positions["BTCUSDT"] = {
        "id": "test_old_trade",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "quantity": 10.0,
        "remaining_quantity": 10.0,
        "notional_usd": 1000.0,
        "margin_collateral_usd": 100.0,
        "leverage": 10,
        "atr_14": 2.0,
        "stop_loss": 97.0,
        "tp1": 104.0,
        "tp2": 108.0,
        "tp1_hit": False,
        "early_be_hit": False,
        "break_even_active": False,
        "open_time": old_time,
        "mode": "PAPER"
    }

    # Market price is slightly flat at 100.10 (within bounds)
    trader._fetch_market_data = lambda s: {"price": 100.10, "closes": [100.10]*20}
    trader._manage_open_positions()

    # The position MUST remain open and NOT be killed by an arbitrary alpha decay clock!
    assert "BTCUSDT" in trader.open_positions, "Position should NOT be killed by legacy alpha decay timeout!"
