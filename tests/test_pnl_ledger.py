"""Tests for the unified realized-PnL ledger under the SINGLE-BOOKING
convention:
- every fee/funding cost appears EXACTLY ONCE in ledger PnL
- closed-trade pnl_usd == that trade's ledger sum, exactly
- daily PnL is derived from the ledger, never a separate counter
- funding accrues once per 8h window
- break-even exits net >= 0 after all costs
- manual/Telegram closes route through taker fills with fees
- TP1 partial fills bank incrementally and NEVER lock the free trade early
"""
import json
import time
from unittest.mock import MagicMock

import pytest

from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from scripts.paper_fill_simulator import PaperFillSimulator, MAKER_FEE_RATE, TAKER_FEE_RATE
from test_risk_and_correlation import StubFillSimulator


class PartialStubFillSimulator(StubFillSimulator):
    """First poll banks half the TP1 quantity, second poll the rest —
    exercises the partial-fill continuation logic deterministically."""

    def poll_maker_order(self, order_id):
        o = self._orders[order_id]
        o["polls"] = o.get("polls", 0) + 1
        qty, limit = o["quantity"], o["limit_price"]
        chunk = qty / 2
        if o["polls"] == 1:
            status, filled, remaining = "PARTIAL", chunk, qty - chunk
        else:
            status, filled, remaining = "FILLED", qty, 0.0
        notional = round(chunk * limit, 2)
        fee = round(notional * MAKER_FEE_RATE, 4)
        o["status"] = status
        return {
            "status": status, "order_id": order_id, "symbol": o["symbol"], "side": o["side"],
            "limit_price": limit, "requested_qty": qty,
            "filled_qty": filled, "remaining_qty": remaining,
            "new_filled_qty": chunk, "new_fee_usd": fee,
            "avg_price": limit, "is_maker": True, "reason": None,
            "timestamp_utc": "2026-09-21 00:00:00 UTC",
        }


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
    trader.PRICE_CACHE_TTL_SEC = 0  # tests drive ticks manually; never serve a cached price
    return trader


def open_and_fill(trader, sig):
    trader._open_position(sig)
    trader._poll_pending_entries()


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


def trade_ledger_sum(events, trade_id):
    return round(sum(e["realized_pnl_usd"] for e in events if e["trade_id"] == trade_id), 2)


def test_tp1_plus_runner_exact_single_booking(ledger_trader):
    """Closed-trade pnl == its ledger sum EXACTLY. Entry fee, TP1 leg, TP2
    leg each appear once; exit legs carry only gross-minus-their-own-fee."""
    trader = ledger_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    trade_id = pos["id"]

    # Tick 1 @ TP1 touch: places the TP1 working order. Tick 2: poll fills it.
    trader._fetch_market_data = lambda s: {"price": 101.50, "closes": [101.50] * 20}
    trader._manage_open_positions()
    assert trader.open_positions["BTCUSDT"].get("tp1_order_id") is not None
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

    # --- hand-computed expectations from the single-booking convention ---
    qty, entry = 100.0, 100.0
    exp_entry_fee = round(round(qty * entry, 2) * MAKER_FEE_RATE, 4)
    exp_tp1_fee = round(round(50.0 * 101.50, 2) * MAKER_FEE_RATE, 4)
    exp_tp1 = round((101.50 - entry) * 50.0 - exp_tp1_fee, 2)
    exp_tp2_fee = round(round(50.0 * 103.50, 2) * TAKER_FEE_RATE, 4)
    exp_tp2 = round((103.50 - entry) * 50.0 - exp_tp2_fee, 2)
    exp_total = round(-exp_entry_fee + exp_tp1 + exp_tp2, 2)

    tp1_leg = [l for l in rec["legs"] if l["leg"] == "TP1"][0]
    tp2_leg = [l for l in rec["legs"] if l["leg"] == "TP2"][0]
    # Exit legs = gross minus ONLY their own exit fee (no entry-fee share,
    # no funding share — that was the double-counting bug).
    assert tp1_leg["realized_pnl_usd"] == exp_tp1, tp1_leg
    assert tp2_leg["realized_pnl_usd"] == exp_tp2, tp2_leg
    assert rec["pnl_usd"] == exp_total, (rec["pnl_usd"], exp_total)

    # Ledger: exactly one ENTRY, one TP1, one TP2 for this trade.
    events = ledger_events(trader)
    by_leg = {}
    for e in events:
        assert e["trade_id"] == trade_id
        by_leg.setdefault(e["leg"], []).append(e)
    assert [e["realized_pnl_usd"] for e in by_leg["ENTRY"]] == [-exp_entry_fee]
    assert [e["realized_pnl_usd"] for e in by_leg["TP1"]] == [exp_tp1]
    assert [e["realized_pnl_usd"] for e in by_leg["TP2"]] == [exp_tp2]

    # THE invariant: closed-trade PnL == its ledger sum, exactly.
    assert trade_ledger_sum(events, trade_id) == rec["pnl_usd"]
    # Daily PnL is derived from the ledger, nothing else.
    assert trader.daily_pnl_usd == round(sum(e["realized_pnl_usd"] for e in events), 2)
    # History total == ledger total (single counting, no fudge factors).
    assert round(sum(t["pnl_usd"] for t in history), 2) == trader.daily_pnl_usd


def test_no_fee_or_funding_double_count(ledger_trader):
    """Force a funding charge, then close: the exit leg must not subtract
    the entry fee or funding again."""
    trader = ledger_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    trade_id = pos["id"]

    # Force-age past 5 minutes and accrue one funding window.
    pos["open_time"] = time.time() - 600
    pos["last_funding_window"] = "2000-01-01-0"
    charged = trader._accrue_funding(pos, 10_000.0)
    assert charged == round(10_000.0 * 0.0001, 4)

    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}
    result = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    rec = result["trade"]

    events = ledger_events(trader)
    funding_events = [e for e in events if e["leg"] == "FUNDING"]
    assert len(funding_events) == 1
    assert funding_events[0]["realized_pnl_usd"] == -charged

    exit_leg = [l for l in rec["legs"] if l["leg"] == "MANUAL"][0]
    exp_fee = round(round(100.0 * 101.00, 2) * TAKER_FEE_RATE, 4)
    exp_leg = round((101.00 - 100.0) * 100.0 - exp_fee, 2)
    # Single booking: exit leg = gross - exit fee ONLY.
    assert exit_leg["realized_pnl_usd"] == exp_leg, exit_leg

    exp_total = round(-round(10_000.0 * MAKER_FEE_RATE, 4) - charged + exp_leg, 2)
    assert rec["pnl_usd"] == exp_total
    assert trade_ledger_sum(events, trade_id) == rec["pnl_usd"]


def test_tp1_partial_fill_does_not_lock_free_trade(ledger_trader):
    """A partial TP1 fill banks profit but must NOT set tp1_hit or move the
    stop: the free trade locks only when the FULL TP1 quantity completes."""
    trader = ledger_trader
    # tp1_pct 0.005 -> TP1 at 100.50 (+0.5%), BELOW the early-BE trigger
    # (+0.6%), so the stop can only move via the TP1 completion path.
    open_and_fill(trader, make_signal(tp1_pct=0.005))
    trader.execution_adapter.fill_simulator = PartialStubFillSimulator()  # TP1 partials
    pos = trader.open_positions["BTCUSDT"]
    orig_stop = pos["stop_loss"]
    assert pos["tp1"] == 100.50

    trader._fetch_market_data = lambda s: {"price": 100.50, "closes": [100.50] * 20}
    trader._manage_open_positions()  # places TP1 working order
    trader._manage_open_positions()  # poll 1 -> PARTIAL (25 of 50)
    pos = trader.open_positions["BTCUSDT"]
    assert pos["tp1_hit"] is False, "partial fill must not lock the free trade"
    assert pos["stop_loss"] == orig_stop, "stop must not move on a partial"
    assert pos["tp1_filled_qty"] == 25.0
    assert pos["tp1_order_id"] is not None, "remainder must keep working"

    trader._manage_open_positions()  # poll 2 -> FILLED (rest)
    pos = trader.open_positions["BTCUSDT"]
    assert pos["tp1_hit"] is True
    # Honest total-trade break-even: the banked TP1 profit already covered
    # the entry fee, so the risk-free level sits BELOW entry. The old
    # profit-lock put it above entry, amputating the runner on any
    # normal pullback.
    assert pos["stop_loss"] == pytest.approx(trader._fee_protected_be(pos))
    assert pos["stop_loss"] < 100.0

    # Both partial chunks banked as TP1 legs. Each chunk rounds to cents
    # independently (real chunked-fill behavior), so expect per-chunk math.
    tp1_legs = [l for l in pos["legs"] if l["leg"] == "TP1"]
    assert len(tp1_legs) == 2
    chunk_fee = round(round(25.0 * 100.50, 2) * MAKER_FEE_RATE, 4)
    exp_chunk = round((100.50 - 100.0) * 25.0 - chunk_fee, 2)
    assert [l["realized_pnl_usd"] for l in tp1_legs] == [exp_chunk, exp_chunk]


def test_stop_loss_pays_taker_fee_and_slippage_free_but_costed(ledger_trader):
    trader = ledger_trader
    open_and_fill(trader, make_signal())

    # Straight to stop: 98.00 -> taker exit
    trader._fetch_market_data = lambda s: {"price": 97.90, "closes": [97.90] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions

    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    rec = history[0]
    assert rec["exit_reason"] == "STOP_LOSS"
    # Loss must EXCEED the raw price move: fees are now charged on both sides.
    qty = 100.0  # $10k / $100
    raw_loss = (97.90 - 100.0) * qty
    assert rec["pnl_usd"] < raw_loss, (rec["pnl_usd"], raw_loss)
    assert rec["fees_usd"] > 0
    # Cooldown armed on stop loss
    assert "BTCUSDT" in trader.symbol_cooldowns
    # Exact reconciliation still holds on a full loss.
    events = ledger_events(trader)
    assert trade_ledger_sum(events, rec["trade_id"]) == rec["pnl_usd"]


def test_break_even_exit_nets_non_negative(ledger_trader):
    """After the early-BE ratchet, a stop-out at the BE level must not lose
    money once entry fee + taker exit fee + funding are accounted for.

    The TP1 working order stays unfilled on the pullback (price never
    returns to the TP1 limit, so a real maker order would still be
    resting) — this exercises the pre-TP1 early-BE path, not the post-TP1
    honest-BE path.
    """
    trader = ledger_trader
    open_and_fill(trader, make_signal())

    # Push to +1.1 ATR to arm the honest break-even (also places the TP1
    # working order).
    trader._fetch_market_data = lambda s: {"price": 102.20, "closes": [102.20] * 20}
    trader._manage_open_positions()
    pos = trader.open_positions["BTCUSDT"]
    assert pos["break_even_active"] is True
    be_stop = pos["stop_loss"]
    assert be_stop > 100.0

    # Keep the TP1 order WORKING through the pullback: no fills, so the
    # early-BE stop is what gets tested.
    sim = trader.execution_adapter.fill_simulator

    def working_poll(order_id):
        o = sim._orders[order_id]
        return {"status": "WORKING", "order_id": order_id, "symbol": o["symbol"],
                "side": o["side"], "limit_price": o["limit_price"],
                "requested_qty": o["quantity"], "filled_qty": 0.0,
                "remaining_qty": o["quantity"], "new_filled_qty": 0.0,
                "new_fee_usd": 0.0, "avg_price": o["limit_price"],
                "is_maker": True, "reason": None,
                "timestamp_utc": "2026-09-21 00:00:00 UTC"}

    sim.poll_maker_order = working_poll

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
    open_and_fill(trader, make_signal())
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


def test_funding_young_position_not_stamped_charges_later(ledger_trader):
    """A position younger than 5 minutes is NOT charged and NOT stamped:
    it retries next tick and still pays for the current window once old
    enough. Stamping-without-charging was the bug that silently skipped
    the first funding window."""
    trader = ledger_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    assert time.time() - pos["open_time"] < 300

    assert trader._accrue_funding(pos, 10_000.0) == 0.0
    assert pos.get("last_funding_window") is None, "young position must NOT be stamped"
    events = ledger_events(trader)
    assert not [e for e in events if e["leg"] == "FUNDING"], "no funding booked for young position"

    # Same window, now older than 5 minutes -> charged exactly once.
    pos["open_time"] = time.time() - 600
    charged = trader._accrue_funding(pos, 10_000.0)
    assert charged == round(10_000.0 * 0.0001, 4)
    assert pos.get("last_funding_window") is not None
    # And not again for the same window.
    assert trader._accrue_funding(pos, 10_000.0) == 0.0
    funding_events = [e for e in ledger_events(trader) if e["leg"] == "FUNDING"]
    assert len(funding_events) == 1


def test_manual_close_routes_through_taker_fill(ledger_trader):
    trader = ledger_trader
    open_and_fill(trader, make_signal())
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


def test_telegram_close_and_closeall_use_ledger_path(ledger_trader):
    """The old Telegram handlers deleted positions and booked raw unrealized
    PnL — skipping taker fills, fees, and the ledger. They must now route
    through close_position like every other exit."""
    trader = ledger_trader
    open_and_fill(trader, make_signal("BTCUSDT"))
    open_and_fill(trader, make_signal("ETHUSDT"))
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    res = trader.handle_remote_action("close", {"symbol": "BTCUSDT"})
    assert res["success"] is True and res["closed"] == "BTCUSDT"
    assert "BTCUSDT" not in trader.open_positions

    events = ledger_events(trader)
    manual_legs = [e for e in events if e["leg"] == "MANUAL" and e["symbol"] == "BTCUSDT"]
    assert len(manual_legs) == 1, "Telegram close must book a taker MANUAL leg"
    assert manual_legs[0]["fee_usd"] > 0, "Telegram close must pay the taker fee"

    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    btc_rec = [t for t in history if t["symbol"] == "BTCUSDT"][0]
    assert btc_rec["exit_reason"] == "REMOTE_TELEGRAM_CLOSE"
    assert trade_ledger_sum(events, btc_rec["trade_id"]) == btc_rec["pnl_usd"]

    res = trader.handle_remote_action("closeall", {})
    assert res["success"] is True and res["closed_count"] == 1
    assert trader.open_positions == {}
    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    eth_rec = [t for t in history if t["symbol"] == "ETHUSDT"][0]
    assert eth_rec["exit_reason"] == "REMOTE_TELEGRAM_CLOSEALL"


def test_reconcile_rebuilds_daily_from_ledger(ledger_trader):
    trader = ledger_trader
    open_and_fill(trader, make_signal())
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
    open_and_fill(trader, make_signal())
    # Entry fee immediately reduces equity
    assert trader.equity_usd < 100_000.0
    assert abs(trader.equity_usd - (100_000.0 + trader._lifetime_realized_pnl())) < 1e-9


def test_telegram_close_cancels_pending_entry(ledger_trader):
    """Closing a symbol with an unfilled pending entry cancels the working
    order and must never open the position later."""
    trader = ledger_trader

    class NeverFills(StubFillSimulator):
        def poll_maker_order(self, order_id):
            o = self._orders[order_id]
            return {"status": "WORKING", "order_id": order_id, "symbol": o["symbol"],
                    "side": o["side"], "limit_price": o["limit_price"],
                    "requested_qty": o["quantity"], "filled_qty": 0.0,
                    "remaining_qty": o["quantity"], "new_filled_qty": 0.0,
                    "new_fee_usd": 0.0, "avg_price": o["limit_price"],
                    "is_maker": True, "reason": None,
                    "timestamp_utc": "2026-09-21 00:00:00 UTC"}

    trader.execution_adapter.fill_simulator = NeverFills()
    trader._open_position(make_signal())  # stages, never fills
    assert "BTCUSDT" in trader.pending_entries
    assert "BTCUSDT" not in trader.open_positions

    res = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    assert res["success"] is True
    assert "BTCUSDT" not in trader.pending_entries
    # A later poll cycle must not resurrect the position.
    trader._poll_pending_entries()
    assert "BTCUSDT" not in trader.open_positions
    assert "BTCUSDT" not in trader.pending_entries
