"""Tests for the unified realized-PnL ledger:
- TP1 + runner legs sum to the closed trade's pnl (no double counting)
- daily PnL is derived from the ledger, never a separate counter
- funding accrues once per 8h window
- break-even exits net >= 0 after all costs
- manual closes route through taker fills with fees
"""
import json
import time
from unittest.mock import MagicMock

import pytest

from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from scripts.paper_fill_simulator import PaperFillSimulator, MAKER_FEE_RATE, TAKER_FEE_RATE
from test_risk_and_correlation import StubFillSimulator


@pytest.fixture
def ledger_trader(tmp_path):
    trader = AutonomousMultiAssetTrader()
    trader.positions_file = tmp_path / "autonomous_positions.json"
    trader.history_file = tmp_path / "autonomous_trade_history.json"
    trader.state_file = tmp_path / "autonomous_state.json"
    trader.pnl_ledger_file = tmp_path / "pnl_ledger.jsonl"
    trader.decision_log_file = tmp_path / "decision_log.jsonl"
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    trader.open_positions = {}
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    return trader


def make_signal(symbol="BTCUSDT", price=100.0, **kw):
    sig = {
        "symbol": symbol, "side": "BUY", "price": price, "atr_14": 2.0,
        "sl_pct": 0.02, "tp1_pct": 0.015, "tp2_pct": 0.035, "setup": "TEST",
    }
    sig.update(kw)
    return sig


def ledger_events(trader):
    with open(trader.pnl_ledger_file, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_tp1_plus_runner_equals_closed_trade_pnl(ledger_trader):
    """The old bug: TP1 added to daily AND the close added runner-only PnL.
    Now the closed trade's pnl_usd == sum of its legs == ledger sum."""
    trader = ledger_trader
    trader._open_position(make_signal())

    # TP1 touch at 101.50 -> maker fill of half
    trader._fetch_market_data = lambda s: {"price": 101.50, "closes": [101.50] * 20}
    trader._manage_open_positions()
    pos = trader.open_positions["BTCUSDT"]
    assert pos["tp1_hit"] is True

    # Runner runs to TP2 at 103.50 -> taker fill of the rest
    trader._fetch_market_data = lambda s: {"price": 103.50, "closes": [103.50] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions

    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    assert len(history) == 1
    rec = history[0]
    assert rec["exit_reason"] == "TP2_RUNNER_TARGET"

    leg_pnl = sum(leg["realized_pnl_usd"] for leg in rec["legs"])
    assert rec["legs"][0]["leg"] == "TP1"
    assert rec["legs"][-1]["leg"] == "TP2"
    # Closed-trade PnL is exactly the sum of its legs — nothing more.
    assert abs(rec["pnl_usd"] - round(leg_pnl, 2)) < 1e-9, (rec["pnl_usd"], leg_pnl)

    # Daily PnL is derived from the ledger: ENTRY fee + TP1 leg + TP2 leg.
    events = ledger_events(trader)
    ledger_total = round(sum(e["realized_pnl_usd"] for e in events), 2)
    assert abs(trader.daily_pnl_usd - ledger_total) < 1e-9
    # And the history total matches the ledger total (single counting):
    # history pnl = ledger total + entry fee (the ENTRY leg is a cost of the
    # trade but is recorded separately from the exit legs).
    history_total = round(sum(t["pnl_usd"] for t in history), 2)
    entry_fee = abs(events[0]["realized_pnl_usd"])
    assert abs(history_total - (ledger_total + entry_fee)) < 1e-9


def test_stop_loss_pays_taker_fee_and_slippage_free_but_costed(ledger_trader):
    trader = ledger_trader
    trader._open_position(make_signal())

    # Straight to stop: 98.00 -> taker exit
    trader._fetch_market_data = lambda s: {"price": 97.90, "closes": [97.90] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions

    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    rec = history[0]
    assert rec["exit_reason"] == "STOP_LOSS"
    # Loss must EXCEED the raw price move: fees are now charged on both sides.
    qty = 100.0  # $10k / $100
    raw_loss = (98.0 - 100.0) * qty  # -200 before costs... (exit at stub ref price 97.90)
    raw_loss = (97.90 - 100.0) * qty
    assert rec["pnl_usd"] < raw_loss, (rec["pnl_usd"], raw_loss)
    assert rec["fees_usd"] > 0
    # Cooldown armed on stop loss
    assert "BTCUSDT" in trader.symbol_cooldowns


def test_break_even_exit_nets_non_negative(ledger_trader):
    """After the early-BE ratchet, a stop-out at the BE level must not lose
    money once entry fee + taker exit fee + funding are accounted for."""
    trader = ledger_trader
    trader._open_position(make_signal())

    # Push to +1.1 ATR to arm the honest break-even
    trader._fetch_market_data = lambda s: {"price": 102.20, "closes": [102.20] * 20}
    trader._manage_open_positions()
    pos = trader.open_positions["BTCUSDT"]
    assert pos["break_even_active"] is True
    be_stop = pos["stop_loss"]
    assert be_stop > 100.0

    # Price falls back exactly to the BE stop -> taker exit at ~BE
    trader._fetch_market_data = lambda s: {"price": be_stop - 0.01, "closes": [be_stop - 0.01] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions

    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    rec = history[0]
    assert rec["exit_reason"] == "BREAK_EVEN_STOP"
    # Net of ALL costs must be >= 0 (the guarantee the old 0.03% buffer faked)
    assert rec["pnl_usd"] >= -0.01, rec["pnl_usd"]


def test_funding_accrues_once_per_window(ledger_trader):
    trader = ledger_trader
    trader._open_position(make_signal())
    pos = trader.open_positions["BTCUSDT"]

    notional = 100.0 * 100.0
    first = trader._accrue_funding(pos, notional)
    assert first == round(notional * 0.0001, 4) or first == 0.0  # 0 if position < 5 min old
    # Force age past 5 minutes and accrue for a fresh window
    pos["open_time"] = time.time() - 600
    pos["last_funding_window"] = "2000-01-01-0"
    charged = trader._accrue_funding(pos, notional)
    assert charged == round(notional * 0.0001, 4)
    # Same window again -> no double charge
    assert trader._accrue_funding(pos, notional) == 0.0
    assert pos["funding_paid_usd"] == round(charged, 2)

    events = ledger_events(trader)
    funding_events = [e for e in events if e["leg"] == "FUNDING"]
    assert len(funding_events) == 1
    assert funding_events[0]["realized_pnl_usd"] == -charged


def test_manual_close_routes_through_taker_fill(ledger_trader):
    trader = ledger_trader
    trader._open_position(make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    result = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    assert result["success"] is True
    rec = result["trade"]
    assert rec["exit_reason"] == "MANUAL_CLOSE"
    # Taker fee charged on the exit leg
    exit_leg = [l for l in rec["legs"] if l["leg"] == "MANUAL"][0]
    expected_fee = round(exit_leg["qty"] * exit_leg["price"] * TAKER_FEE_RATE, 4)
    assert abs(exit_leg["fee_usd"] - expected_fee) < 1e-9
    # Ledger and daily agree
    events = ledger_events(trader)
    assert abs(trader.daily_pnl_usd - round(sum(e["realized_pnl_usd"] for e in events), 2)) < 1e-9


def test_reconcile_rebuilds_daily_from_ledger(ledger_trader):
    trader = ledger_trader
    trader._open_position(make_signal())
    trader._fetch_market_data = lambda s: {"price": 97.90, "closes": [97.90] * 20}
    trader._manage_open_positions()
    on_ledger = round(sum(e["realized_pnl_usd"] for e in ledger_events(trader)), 2)

    # Corrupt the in-memory counter, then reconcile: the ledger wins.
    trader.daily_pnl_usd = 12345.67
    trader._reconcile_daily_pnl()
    assert abs(trader.daily_pnl_usd - on_ledger) < 1e-9


def test_equity_tracks_lifetime_realized(ledger_trader):
    trader = ledger_trader
    assert trader.equity_usd == 100_000.0
    trader._open_position(make_signal())
    # Entry fee immediately reduces equity
    assert trader.equity_usd < 100_000.0
    assert abs(trader.equity_usd - (100_000.0 + trader._lifetime_realized_pnl())) < 1e-9
