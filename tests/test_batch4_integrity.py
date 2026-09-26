"""Batch 4 integrity tests: concurrency, funding, rollover, price feed,
instance lock, pending-aware controls, memory quarantine, and the fenced
directional system.

Every test here guards a specific reviewer finding. They must stay green:
each one pins a bug that once existed.
"""
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

import scripts.autonomous_multi_asset_trader as trader_module
from scripts.autonomous_multi_asset_trader import (
    AutonomousMultiAssetTrader,
    LedgerWriteError,
)
from scripts.paper_fill_simulator import PaperFillSimulator
from test_risk_and_correlation import StubFillSimulator


@pytest.fixture
def b4_trader(tmp_path, monkeypatch):
    # Pin the entry discount to zero: batch-4 integrity tests use exact
    # price expectations. Discount policy is covered in
    # tests/test_entry_discount.py.
    monkeypatch.setattr(trader_module, "ENTRY_DISCOUNT_ATR_MULT", 0.0)
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    trader.open_positions = {}
    trader.pending_entries = {}
    trader.execution_adapter.fill_simulator = StubFillSimulator()
    trader.PRICE_CACHE_TTL_SEC = 0  # tests drive ticks manually; never serve a cached price
    return trader


def make_signal(symbol="BTCUSDT", price=100.0, **kw):
    sig = {"symbol": symbol, "side": "BUY", "price": price, "atr_14": 2.0,
           "sl_pct": 0.02, "tp1_pct": 0.015, "tp2_pct": 0.035, "setup": "TEST"}
    sig.update(kw)
    return sig


def open_and_fill(trader, sig):
    trader._open_position(sig)
    trader._poll_pending_entries()


def ledger_events(trader):
    with open(trader.pnl_ledger_file, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# ----------------------------------------------------------------------
# C1: the double-booking race
# ----------------------------------------------------------------------
def test_close_claim_protocol_single_booking(b4_trader):
    """Whoever claims the close first owns it; the loser stands down and
    books nothing. Deterministic interleaving: run-loop claims, then the
    Telegram close arrives."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    claimed = trader._try_begin_close("BTCUSDT")  # run loop wins
    assert claimed is not None

    res = trader.close_position("BTCUSDT", reason="REMOTE_TELEGRAM_CLOSE")
    assert res["success"] is False
    assert "already in progress" in res["error"]

    # The run loop finishes its close: exactly one MANUAL-free booking path
    # ran, so the ledger must show only the ENTRY event (no exit booked twice).
    events = ledger_events(trader)
    assert len([e for e in events if e["leg"] == "MANUAL"]) == 0


def test_concurrent_close_position_single_exit(b4_trader):
    """Two racing close_position calls: exactly one succeeds, exactly one
    MANUAL ledger event, history reconciles to the ledger."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    results = []
    barrier = threading.Barrier(2)

    def do_close():
        barrier.wait(timeout=5)
        results.append(trader.close_position("BTCUSDT", reason="MANUAL_CLOSE"))

    t1 = threading.Thread(target=do_close)
    t2 = threading.Thread(target=do_close)
    t1.start(); t2.start(); t1.join(timeout=10); t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    successes = [r for r in results if r.get("success")]
    assert len(successes) == 1, results
    trade = successes[0]["trade"]
    events = ledger_events(trader)
    manuals = [e for e in events if e["leg"] == "MANUAL"]
    assert len(manuals) == 1
    assert round(sum(e["realized_pnl_usd"] for e in events
                     if e["trade_id"] == trade["trade_id"]), 2) == trade["pnl_usd"]
    assert "BTCUSDT" not in trader.open_positions


def test_closing_flag_cleared_on_load(tmp_path):
    """A crash-persisted _closing flag must not make a position uncloseable
    after restart."""
    t1 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t1.telegram_bot = MagicMock(); t1.episodic_memory = MagicMock()
    t1.open_positions = {"BTCUSDT": {"symbol": "BTCUSDT", "_closing": True,
                                    "entry_price": 100.0, "side": "BUY",
                                    "quantity": 1.0, "remaining_quantity": 1.0}}
    t1._save_positions()
    t2 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    pos = t2.open_positions["BTCUSDT"]
    assert "_closing" not in pos
    assert t2._try_begin_close("BTCUSDT") is not None


# ----------------------------------------------------------------------
# M2: funding stamp order (the batch-3 miss)
# ----------------------------------------------------------------------
def test_funding_young_position_retries_and_charges_once(b4_trader):
    trader = b4_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]

    assert trader._accrue_funding(pos, 10_000.0) == 0.0
    assert pos.get("last_funding_window") is None  # NOT stamped

    pos["open_time"] = time.time() - 600  # same window, now old enough
    charged = trader._accrue_funding(pos, 10_000.0)
    assert charged == round(10_000.0 * 0.0001, 4)
    assert trader._accrue_funding(pos, 10_000.0) == 0.0  # once per window
    funding_events = [e for e in ledger_events(trader) if e["leg"] == "FUNDING"]
    assert len(funding_events) == 1
    assert funding_events[0]["realized_pnl_usd"] == -charged


# ----------------------------------------------------------------------
# M1: honest total-trade break-even
# ----------------------------------------------------------------------
def test_be_after_tp1_sits_below_entry(b4_trader):
    """After TP1 banks profit, the 'break-even' stop must reflect the whole
    trade: banked profit already paid the costs, so the runner's risk-free
    level can sit below entry instead of amputating the runner."""
    trader = b4_trader
    pos = {"entry_price": 100.0, "entry_avg_price": 100.0, "side": "LONG",
           "quantity": 100.0, "remaining_quantity": 50.0,
           "realized_pnl_usd": 4.975}  # banked TP1 5.00 minus entry fee 0.025
    be = trader._fee_protected_be(pos)
    assert be < 100.0, be


def test_be_before_tp1_classic(b4_trader):
    """Before any scale-out, realized = -entry_fee - funding: classic BE
    just above entry. Behavior unchanged from batch 3."""
    trader = b4_trader
    pos = {"entry_price": 100.0, "entry_avg_price": 100.0, "side": "LONG",
           "quantity": 100.0, "remaining_quantity": 100.0,
           "entry_fee_usd": 0.5, "funding_paid_usd": 1.0,
           "realized_pnl_usd": -1.5}
    be = trader._fee_protected_be(pos)
    assert be > 100.0, be


# ----------------------------------------------------------------------
# M3: day rollover in the run loop
# ----------------------------------------------------------------------
def test_maybe_rollover_day_reconciles(b4_trader):
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}
    trader.close_position("BTCUSDT")
    # Corrupt the in-memory counter, fake yesterday, roll over.
    trader.daily_pnl_usd = 999999.0
    trader.daily_reset_date = "2000-01-01"
    trader.circuit_breaker_triggered = True
    trader._maybe_rollover_day()
    assert trader.daily_reset_date != "2000-01-01"
    assert trader.circuit_breaker_triggered is False
    assert trader.daily_pnl_usd != 999999.0  # rebuilt from the ledger


# ----------------------------------------------------------------------
# M4: price cache
# ----------------------------------------------------------------------
def test_get_price_tick_cache_and_fallback(b4_trader):
    trader = b4_trader
    trader.PRICE_CACHE_TTL_SEC = 2.0  # this test exercises the cache itself
    trader._light_price_feed_enabled = True
    calls = []
    trader._fetch_light_price = lambda s: calls.append(s) or 105.0

    t1 = trader._get_price_tick("BTCUSDT")
    t2 = trader._get_price_tick("BTCUSDT")
    assert t1["price"] == 105.0 and t2["cached"] is True
    assert len(calls) == 1  # second tick served from cache

    # Light feed down -> falls back to the klines fetch.
    trader._price_cache.clear()

    def boom(s):
        raise Exception("down")
    trader._fetch_light_price = boom
    trader._fetch_market_data = lambda s: {"price": 107.0}
    t3 = trader._get_price_tick("BTCUSDT")
    assert t3["price"] == 107.0 and t3["cached"] is False

    # Everything down -> None (caller must skip, never manage stale).
    trader._price_cache.clear()
    trader._fetch_market_data = boom
    assert trader._get_price_tick("BTCUSDT") is None


def test_manage_positions_uses_cached_tick(b4_trader):
    """With the light feed disabled (tests), management still works through
    the stubbed _fetch_market_data fallback."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 97.90, "closes": [97.90] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions  # SL filled
    assert trader._price_fetch_failures == 0


def test_total_price_failure_counts_degraded(b4_trader):
    trader = b4_trader
    open_and_fill(trader, make_signal())

    def boom(s):
        raise Exception("down")
    trader._fetch_light_price = boom
    trader._fetch_market_data = boom
    trader._manage_open_positions()
    assert trader._price_fetch_failures == 1
    assert "BTCUSDT" in trader.open_positions  # NOT managed on no data


# ----------------------------------------------------------------------
# M5: single-instance guard
# ----------------------------------------------------------------------
def test_instance_lock_refuses_second_holder(tmp_path):
    t1 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t1.telegram_bot = MagicMock(); t1.episodic_memory = MagicMock()
    assert t1._acquire_instance_lock() is True

    t2 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    assert t2._acquire_instance_lock() is False  # same process registry

    t1._release_instance_lock()
    assert t2._acquire_instance_lock() is True
    t2._release_instance_lock()


def test_instance_lock_stale_pid_taken_over(tmp_path):
    t = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    (tmp_path / "trader.pid").write_text("999999999")  # dead PID
    assert t._acquire_instance_lock() is True
    t._release_instance_lock()
    assert not (tmp_path / "trader.pid").exists()


def test_start_refuses_when_locked(tmp_path):
    t1 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t1.telegram_bot = MagicMock(); t1.episodic_memory = MagicMock()
    assert t1._acquire_instance_lock() is True
    t2 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t2.telegram_bot = MagicMock(); t2.episodic_memory = MagicMock()
    res = t2.start()
    assert res["success"] is False
    assert "runtime lock" in res["error"]
    t1._release_instance_lock()


# ----------------------------------------------------------------------
# Breaker cancels pendings; closeall covers pendings; cap counts pendings
# ----------------------------------------------------------------------
def test_breaker_trip_cancels_pending_entries(b4_trader):
    trader = b4_trader
    trader._open_position(make_signal("ETHUSDT", 50.0))  # resting entry
    assert "ETHUSDT" in trader.pending_entries
    trader._trip_circuit_breaker()
    assert trader.circuit_breaker_triggered is True
    assert trader.pending_entries == {}
    trader.telegram_bot.notify_circuit_breaker.assert_called_once()


def test_closeall_cancels_pending_entries(b4_trader):
    trader = b4_trader
    trader._open_position(make_signal("ETHUSDT", 50.0))  # pending only, no position
    res = trader.handle_remote_action("closeall", {})
    assert res["success"] is True
    assert trader.pending_entries == {}
    assert res["closed_count"] == 1


def test_close_routes_through_claim_when_nothing_open(b4_trader):
    trader = b4_trader
    res = trader.handle_remote_action("close", {"symbol": "BTCUSDT"})
    assert res["success"] is False
    assert "error" in res


def test_correlation_cap_counts_pending_entries(b4_trader):
    trader = b4_trader
    for sym in ("ETHUSDT", "SOLUSDT", "DOGEUSDT"):
        trader._open_position(make_signal(sym, 50.0))
    assert len(trader.pending_entries) == 3
    trader._open_position(make_signal("AVAXUSDT", 50.0))  # 4th crypto blocked
    assert "AVAXUSDT" not in trader.pending_entries
    assert "AVAXUSDT" not in trader.open_positions


# ----------------------------------------------------------------------
# M8: honest pause message. m10: TP1 cancelled on SL close.
# m14: history never truncated. m18: TP1 re-place keeps original target.
# ----------------------------------------------------------------------
def test_stop_message_honest(b4_trader):
    trader = b4_trader
    trader.stop()
    last = trader.thought_stream[-1]
    assert "NOT actively managed" in last


def test_sl_close_cancels_working_tp1(b4_trader):
    trader = b4_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    sim = trader.execution_adapter.fill_simulator
    order = sim.place_maker_order("BTCUSDT", "SELL", 50.0, 101.5)
    pos["tp1_order_id"] = order["order_id"]
    pos["tp1_target_qty"] = 50.0
    pos["tp1_filled_qty"] = 0.0
    # The TP1 order is still WORKING (no fills yet) when the stop hits.
    sim.poll_maker_order = lambda oid: {
        "status": "WORKING", "order_id": oid, "symbol": "BTCUSDT", "side": "SELL",
        "limit_price": 101.5, "requested_qty": 50.0, "filled_qty": 0.0,
        "remaining_qty": 50.0, "new_filled_qty": 0.0, "new_fee_usd": 0.0,
        "avg_price": 101.5, "is_maker": True, "reason": None,
        "timestamp_utc": "2026-09-21 00:00:00 UTC",
    }
    trader._fetch_market_data = lambda s: {"price": 97.90, "closes": [97.90] * 20}
    trader._manage_open_positions()
    assert "BTCUSDT" not in trader.open_positions
    assert sim._orders[order["order_id"]]["status"] == "CANCELLED"


def test_history_never_truncated(b4_trader):
    trader = b4_trader
    for i in range(505):
        trader._record_closed_trade({"trade_id": f"t{i}", "pnl_usd": 1.0})
    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    assert len(history) == 505


def test_tp1_replaces_original_target_after_unknown(b4_trader):
    """Order state lost after a partial TP1 fill: re-place target - banked,
    not 50% of the remainder."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    pos["tp1_order_id"] = "lost_order"
    pos["tp1_target_qty"] = 50.0
    pos["tp1_filled_qty"] = 20.0  # partial banked before the loss
    pos["remaining_quantity"] = 80.0
    sim = trader.execution_adapter.fill_simulator
    sim.poll_maker_order = lambda oid: {"status": "UNKNOWN", "reason": "simulated restart"}

    trader._fetch_market_data = lambda s: {"price": 102.00, "closes": [102.00] * 20}
    trader._manage_open_positions()  # UNKNOWN -> marks _tp1_replacing
    assert pos.get("_tp1_replacing") is True

    placed = {}
    orig_place = sim.place_maker_order

    def spy_place(symbol, side, quantity, limit_price, max_wait_sec=120):
        placed["qty"] = quantity
        return orig_place(symbol, side, quantity, limit_price, max_wait_sec)

    sim.place_maker_order = spy_place
    trader._manage_open_positions()  # re-place tick
    assert placed["qty"] == pytest.approx(30.0)  # 50 target - 20 banked


# ----------------------------------------------------------------------
# Memory quarantine: fail-closed filter
# ----------------------------------------------------------------------
def test_memory_retrieval_excludes_provenance_less_records(tmp_path):
    from scripts.episodic_memory_engine import EpisodicMemoryEngine
    mem_file = tmp_path / "trade_memory.json"
    mem_file.write_text(json.dumps([
        {"trade_id": "a", "metrics_provenance": "live", "side": "BUY",
         "lesson": "live lesson", "is_win": True,
         "entry_metrics": {"hurst": 0.6, "session": "LONDON"}},
        {"trade_id": "b", "side": "BUY",  # NO provenance -> excluded
         "lesson": "contaminated lesson", "is_win": False,
         "entry_metrics": {"hurst": 0.6, "session": "LONDON"}},
        {"trade_id": "c", "metrics_provenance": "backfilled", "side": "BUY",
         "lesson": "backfilled lesson", "is_win": False,
         "entry_metrics": {"hurst": 0.6, "session": "LONDON"}},
    ]))
    eng = EpisodicMemoryEngine(memory_file_path=str(mem_file))
    res = eng.retrieve_relevant_lessons(
        current_side="BUY", current_session="LONDON", current_hurst=0.6, limit=3)
    assert res["has_memory"] is True
    assert res["total_memories_stored"] == 1
    assert res["top_lessons"] == ["live lesson"]


def test_memory_retrieval_all_provenance_less_is_empty(tmp_path):
    """Fail-closed: with zero explicit-live records, retrieval reports no
    memory instead of learning from provenance-less records."""
    from scripts.episodic_memory_engine import EpisodicMemoryEngine
    mem_file = tmp_path / "trade_memory.json"
    mem_file.write_text(json.dumps([
        {"trade_id": "b", "side": "BUY", "lesson": "contaminated",
         "entry_metrics": {"hurst": 0.6, "session": "LONDON"}},
    ]))
    eng = EpisodicMemoryEngine(memory_file_path=str(mem_file))
    res = eng.retrieve_relevant_lessons(
        current_side="BUY", current_session="LONDON", current_hurst=0.6, limit=3)
    assert res["has_memory"] is False
    assert res["top_lessons"] == []


# ----------------------------------------------------------------------
# Observability: heartbeat fields
# ----------------------------------------------------------------------
def test_status_reports_liveness(b4_trader):
    st = b4_trader.get_status()
    assert st["thread_alive"] is False
    assert st["seconds_since_tick"] is None
    assert st["price_fetch_failures"] == 0


def test_ledger_write_failure_leaves_daily_pnl_untouched(b4_trader, tmp_path):
    """m11: if the ledger append fails, daily PnL must not be mutated."""
    trader = b4_trader
    trader.pnl_ledger_file = tmp_path / "no_such_dir" / "ledger.jsonl"
    before = trader.daily_pnl_usd
    trader._record_fill_event("t1", "BTCUSDT", "TP1", "SELL", 1.0, 100.0, 0.01, 5.0)
    assert trader.daily_pnl_usd == before


# ----------------------------------------------------------------------
# Ledger-first transaction ordering (failure injection)
# ----------------------------------------------------------------------
def _break_ledger(trader, tmp_path):
    trader.pnl_ledger_file = tmp_path / "no_such_dir" / "ledger.jsonl"


def test_exit_leg_ledger_failure_leaves_position_untouched(b4_trader, tmp_path):
    """If the ledger append fails, _apply_exit_leg must raise loudly and
    leave realized PnL, remaining quantity, legs, and daily PnL untouched."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    before_realized = pos["realized_pnl_usd"]
    before_remaining = pos["remaining_quantity"]
    before_legs = list(pos["legs"])
    before_daily = trader.daily_pnl_usd

    _break_ledger(trader, tmp_path)
    fill = {"status": "FILLED", "filled_qty": before_remaining, "avg_price": 101.0,
            "fee_usd": 0.05, "timestamp_utc": "2026-09-21 00:00:00 UTC"}
    with pytest.raises(LedgerWriteError):
        trader._apply_exit_leg(pos, "TP2", fill, "SELL")

    assert pos["realized_pnl_usd"] == before_realized
    assert pos["remaining_quantity"] == before_remaining
    assert pos["legs"] == before_legs
    assert trader.daily_pnl_usd == before_daily


def test_funding_ledger_failure_does_not_stamp_or_mutate(b4_trader, tmp_path):
    """A failed funding write must not stamp the window and must not mutate
    the position — so the charge retries on the next tick, exactly once."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    pos = trader.open_positions["BTCUSDT"]
    pos["open_time"] = time.time() - 600  # old enough to be charged
    before_realized = pos["realized_pnl_usd"]

    _break_ledger(trader, tmp_path)
    with pytest.raises(LedgerWriteError):
        trader._accrue_funding(pos, 10_000.0)

    assert pos.get("last_funding_window") is None
    assert pos.get("funding_paid_usd", 0.0) == 0.0
    assert pos["realized_pnl_usd"] == before_realized

    # Ledger heals: the retry charges exactly once for the window.
    trader.pnl_ledger_file = tmp_path / "ledger.jsonl"
    charged = trader._accrue_funding(pos, 10_000.0)
    assert charged > 0
    assert trader._accrue_funding(pos, 10_000.0) == 0.0  # no double charge
    fundings = [e for e in ledger_events(trader) if e["leg"] == "FUNDING"]
    assert len(fundings) == 1


def test_entry_ledger_failure_keeps_pending_and_opens_on_retry(b4_trader, tmp_path):
    """If the ENTRY ledger event fails, no position may open and the pending
    entry must survive so the real fill is not lost; the next poll retries."""
    trader = b4_trader
    trader._open_position(make_signal())  # stages the pending entry only
    assert "BTCUSDT" in trader.pending_entries

    _break_ledger(trader, tmp_path)
    trader._poll_pending_entries()  # finalize raises LedgerWriteError inside, caught
    assert "BTCUSDT" not in trader.open_positions
    assert "BTCUSDT" in trader.pending_entries  # kept for retry

    trader.pnl_ledger_file = tmp_path / "ledger.jsonl"
    trader._poll_pending_entries()
    assert "BTCUSDT" in trader.open_positions
    assert "BTCUSDT" not in trader.pending_entries
    entries = [e for e in ledger_events(trader) if e["leg"] == "ENTRY"]
    assert len(entries) == 1  # booked exactly once despite the retry


def test_close_position_ledger_failure_releases_claim(b4_trader, tmp_path):
    """A failed exit-leg write must release the _closing claim and report
    failure — the position stays open and closeable, never stuck."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    _break_ledger(trader, tmp_path)
    res = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    assert res["success"] is False
    assert "still open" in res["error"]
    assert "BTCUSDT" in trader.open_positions
    assert not trader.open_positions["BTCUSDT"].get("_closing")

    # Retry after the ledger heals: the close completes exactly once.
    trader.pnl_ledger_file = tmp_path / "ledger.jsonl"
    res2 = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    assert res2["success"] is True
    assert "BTCUSDT" not in trader.open_positions
    manuals = [e for e in ledger_events(trader) if e["leg"] == "MANUAL"]
    assert len(manuals) == 1


def test_history_write_failure_is_loud_but_close_completes(b4_trader, tmp_path):
    """History is a secondary index: if it cannot be written, the economic
    close (already in the ledger) still completes — loudly, with a counter."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}
    trader.history_file = tmp_path / "no_such_dir" / "history.json"

    res = trader.close_position("BTCUSDT", reason="MANUAL_CLOSE")
    assert res["success"] is True
    assert res["history_persisted"] is False
    assert trader.history_write_failures == 1
    assert "BTCUSDT" not in trader.open_positions
    # The ledger still holds the full economic truth of the close.
    events = ledger_events(trader)
    assert any(e["leg"] == "MANUAL" for e in events)


def test_concurrent_close_records_history_exactly_once(b4_trader):
    """The close-claim protocol: one winner, one MANUAL ledger event, and
    exactly one history record — even under a real thread race."""
    trader = b4_trader
    open_and_fill(trader, make_signal())
    trader._fetch_market_data = lambda s: {"price": 101.00, "closes": [101.00] * 20}

    results = []
    barrier = threading.Barrier(2)

    def do_close():
        barrier.wait(timeout=5)
        results.append(trader.close_position("BTCUSDT", reason="MANUAL_CLOSE"))

    t1 = threading.Thread(target=do_close)
    t2 = threading.Thread(target=do_close)
    t1.start(); t2.start(); t1.join(timeout=10); t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    successes = [r for r in results if r.get("success")]
    assert len(successes) == 1, results
    trade_id = successes[0]["trade"]["trade_id"]
    history = json.loads(trader.history_file.read_text(encoding="utf-8"))
    mine = [h for h in history if h.get("trade_id") == trade_id]
    assert len(mine) == 1


# ----------------------------------------------------------------------
# Ticker-outage fallback: no kline storm; degradation timing documented
# ----------------------------------------------------------------------
def test_heavy_fallback_rate_limited_during_ticker_outage(b4_trader):
    """With the light feed enabled but the ticker failing, the heavy
    100-kline fallback may fire at most once per HEAVY_FALLBACK_INTERVAL_SEC
    per symbol — never once per tick per position."""
    trader = b4_trader
    trader._light_price_feed_enabled = True
    trader._fetch_light_price = lambda s: None  # ticker outage
    calls = []
    def heavy(s):
        calls.append(s)
        return {"price": 100.0}
    trader._fetch_market_data = heavy

    t1 = trader._get_price_tick("BTCUSDT")
    assert t1["price"] == 100.0
    assert len(calls) == 1

    # Immediate retries within the interval: no new heavy request; the
    # cached price from the successful fallback IS served, but once the
    # cache is cleared the symbol is skipped (None) rather than re-fetched.
    trader._price_cache.clear()
    assert trader._get_price_tick("BTCUSDT") is None
    assert trader._get_price_tick("BTCUSDT") is None
    assert len(calls) == 1

    # After the interval elapses, one more attempt is allowed.
    trader._heavy_fallback_attempt_ts["BTCUSDT"] -= 61.0
    t2 = trader._get_price_tick("BTCUSDT")
    assert t2["price"] == 100.0
    assert len(calls) == 2


def test_degradation_counter_reaches_alert_threshold(b4_trader):
    """300 consecutive total-failure management ticks trip the run loop's
    degradation alert. At 1 tick/second that is ~5 minutes of blind risk
    management — the documented timing of the DATA DEGRADED telegram."""
    trader = b4_trader
    open_and_fill(trader, make_signal())

    def boom(s):
        raise Exception("down")
    trader._fetch_light_price = boom
    trader._fetch_market_data = boom

    for _ in range(300):
        trader._manage_open_positions()
    assert trader._price_fetch_failures == 300
    # The position was never managed on absent data.
    assert "BTCUSDT" in trader.open_positions
    # One good tick resets the streak.
    trader._fetch_market_data = lambda s: {"price": 100.0}
    trader._manage_open_positions()
    assert trader._price_fetch_failures == 0


def test_start_releases_lock_when_thread_fails(b4_trader, tmp_path, monkeypatch):
    """If worker-thread startup raises, start() must unwind: is_running
    False, instance lock released, restart possible."""
    trader = b4_trader
    import threading as _th
    real_thread = _th.Thread
    def boom(*a, **k):
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(_th, "Thread", boom)
    try:
        with pytest.raises(RuntimeError):
            trader.start()
    finally:
        monkeypatch.setattr(_th, "Thread", real_thread)
    assert trader.is_running is False
    assert trader.worker_thread is None
    # Lock released: acquiring it again for this runtime dir must succeed
    # (a stale hold would make every future start refuse).
    assert trader._acquire_instance_lock() is True
    trader._release_instance_lock()


# ----------------------------------------------------------------------
# Restart: pending entries persist, then reconcile as terminal telemetry
# ----------------------------------------------------------------------
def test_pending_entries_persist_and_reconcile_on_restart(tmp_path):
    """A staged pending entry is persisted. A new trader on the same
    runtime dir must NOT restore it as a live order; it terminally logs
    ENTRY_ORDER_LOST_ON_RESTART so the decision trail has no silent hole."""
    t1 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t1.telegram_bot = MagicMock(); t1.episodic_memory = MagicMock()
    t1.execution_adapter.fill_simulator = StubFillSimulator()
    t1._open_position(make_signal())
    assert "BTCUSDT" in t1.pending_entries
    persisted = json.loads((tmp_path / "pending_entries.json").read_text(encoding="utf-8"))
    assert "BTCUSDT" in persisted

    t2 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    t2.telegram_bot = MagicMock(); t2.episodic_memory = MagicMock()
    assert t2.pending_entries == {}  # never restored as a live order
    log = (tmp_path / "decision_log.jsonl").read_text(encoding="utf-8")
    assert "ENTRY_ORDER_LOST_ON_RESTART" in log
    # A second restart must not re-log the same drop.
    t3 = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    log2 = (tmp_path / "decision_log.jsonl").read_text(encoding="utf-8")
    assert log2.count("ENTRY_ORDER_LOST_ON_RESTART") == log.count("ENTRY_ORDER_LOST_ON_RESTART")


def test_simulator_prunes_old_terminal_orders(tmp_path):
    """Terminal working orders are pruned after the retention window so a
    24/7 run cannot leak memory; recent terminal orders stay pollable."""
    from scripts.paper_fill_simulator import PaperFillSimulator
    sim = PaperFillSimulator(
        cache_dir=str(tmp_path / "simcache"),
        book_fetcher=lambda s: {"best_bid": 99.0, "best_ask": 101.0,
                                "bids": [[99.0, 10.0]], "asks": [[101.0, 10.0]]},
        filters_fetcher=lambda s: {"tick_size": 0.01, "step_size": 0.001},
    )
    o1 = sim.place_maker_order("BTCUSDT", "BUY", 1.0, 98.0)
    assert o1["status"] == "WORKING"
    sim.cancel_maker_order(o1["order_id"])
    # Backdate past retention, then place a fresh order to trigger pruning.
    sim._working_orders[o1["order_id"]]["terminal_ts_ms"] -= 3700 * 1000
    o2 = sim.place_maker_order("BTCUSDT", "BUY", 1.0, 98.0)
    assert o2["status"] == "WORKING"
    assert sim.poll_maker_order(o1["order_id"])["status"] == "UNKNOWN"  # pruned
    # A recently-cancelled order is still pollable.
    o3 = sim.place_maker_order("ETHUSDT", "BUY", 1.0, 98.0)
    sim.cancel_maker_order(o3["order_id"])
    o4 = sim.place_maker_order("ETHUSDT", "BUY", 1.0, 98.0)
    assert sim.poll_maker_order(o3["order_id"])["status"] == "CANCELLED"


# ----------------------------------------------------------------------
# Frozen experiment universe
# ----------------------------------------------------------------------
def test_universe_frozen_by_default(b4_trader):
    """The forward experiment runs on a fixed symbol list so results are
    attributable to the signal, not to universe rotation."""
    trader = b4_trader
    wl = trader._get_hunting_watchlist()
    assert wl[0] == "XAUUSDT" and "PAXGUSDT" in wl
    assert len(wl) == len(set(wl))  # de-duplicated
    # The live scanner is not consulted while frozen.
    trader.universe_scanner.get_hunting_watchlist = lambda: ["FAKEUSDT"]
    assert "FAKEUSDT" not in trader._get_hunting_watchlist()
    st = trader.get_status()
    assert st["universe_frozen"] is True
    assert st["universe_symbols"] == wl


def test_universe_thaw_delegates_to_scanner(b4_trader):
    trader = b4_trader
    trader.freeze_universe = False
    trader.universe_scanner.get_hunting_watchlist = lambda: ["FAKEUSDT"]
    assert trader._get_hunting_watchlist() == ["FAKEUSDT"]
