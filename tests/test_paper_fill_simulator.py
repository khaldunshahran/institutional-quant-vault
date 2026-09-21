"""Tests for the paper fill simulator's working-order API.

The honesty anchor: a post-only maker order is a WORKING order that can
only fill on market activity observed AFTER its placement timestamp.
Pre-placement trades must NEVER fill it. Partial fills are banked
incrementally via poll_maker_order(); tp1_hit-style completion is the
caller's job.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from paper_fill_simulator import PaperFillSimulator, MAKER_FEE_RATE, TAKER_FEE_RATE

BOOK = {
    "best_bid": 100.0, "best_ask": 100.10,
    "bids": [(100.0, 50.0), (99.9, 100.0)],
    "asks": [(100.10, 50.0), (100.2, 100.0)],
}
FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_notional": 5.0}


def make_sim(book=None, trades=None, filters=None):
    """trades_fetcher returns the scripted post-placement trades; the
    simulator itself enforces the ts > placed_ts anchor."""
    trades = trades if trades is not None else []
    return PaperFillSimulator(
        cache_dir="/tmp/fill_sim_test",
        book_fetcher=lambda s: book if book is not None else BOOK,
        trades_fetcher=lambda s, start_ms: [dict(t) for t in trades],
        filters_fetcher=lambda s: filters if filters is not None else FILTERS,
    )


def poll_now(sim, order_id):
    """Bypass the 1/sec network throttle for deterministic tests."""
    sim._working_orders[order_id]["last_poll_ts_ms"] = 0
    return sim.poll_maker_order(order_id)


def placed_ts(sim, order_id):
    return sim._working_orders[order_id]["placed_ts_ms"]


def trade(a, price, qty, ts_ms):
    return {"a": a, "price": price, "qty": qty, "ts_ms": ts_ms}


def test_post_only_cross_rejected():
    sim = make_sim()
    # BUY limit at/above the ask would cross -> GTX reject
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 100.10)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r
    # SELL limit at/below the bid would cross -> reject
    r = sim.place_maker_order("TSTUSDT", "SELL", 1.0, 100.0)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r


def test_pre_placement_trades_never_fill():
    """The core honesty test: violent price action BEFORE placement must
    not fill the order. Only post-placement trade-through fills."""
    sim = make_sim()
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 99.50)
    assert r["status"] == "WORKING", r
    oid = r["order_id"]
    pts = placed_ts(sim, oid)

    # Simulate a huge pre-placement dump by injecting it into the fetcher,
    # then poll: it must be ignored because ts <= placed_ts.
    sim._trades_fetcher = lambda s, start_ms: [trade(1, 99.0, 10_000.0, pts - 60_000)]
    p = poll_now(sim, oid)
    assert p["status"] == "WORKING" and p["filled_qty"] == 0.0, p

    # Same dump AFTER placement fills the order.
    sim._trades_fetcher = lambda s, start_ms: [trade(2, 99.0, 10_000.0, pts + 1_000)]
    p = poll_now(sim, oid)
    assert p["status"] == "FILLED", p
    assert p["filled_qty"] == 1.0
    assert p["new_fee_usd"] == round(99.50 * MAKER_FEE_RATE, 4)


def test_no_trade_through_expires():
    # Price never trades down through our 99.50 bid -> no fill -> EXPIRED
    sim = make_sim()
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 99.50, max_wait_sec=0)
    assert r["status"] == "WORKING", r
    sim._trades_fetcher = lambda s, start_ms: [
        trade(1, 100.5, 100.0, placed_ts(sim, r["order_id"]) + 1_000),  # above limit: no hit
    ]
    p = poll_now(sim, r["order_id"])
    assert p["status"] == "EXPIRED" and p["filled_qty"] == 0.0, p


def test_trade_through_fills_with_queue():
    # Queue ahead at 100.0: 50 units * 100 = $5000. First post-placement
    # trade's volume share ($1990) depletes part of the queue; the second
    # trade's share fills us.
    sim = make_sim()
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 100.0)
    assert r["status"] == "WORKING", r
    oid, pts = r["order_id"], placed_ts(sim, r["order_id"])
    sim._trades_fetcher = lambda s, start_ms: [
        trade(1, 99.5, 200.0, pts + 1_000),
        trade(2, 99.0, 2_000.0, pts + 2_000),
    ]
    p = poll_now(sim, oid)
    assert p["status"] == "FILLED", p
    assert p["filled_qty"] == 1.0
    assert p["new_fee_usd"] == round(100.0 * MAKER_FEE_RATE, 4)


def test_partial_fills_banked_incrementally():
    sim = make_sim()
    r = sim.place_maker_order("TSTUSDT", "BUY", 1000.0, 100.0)
    assert r["status"] == "WORKING", r
    oid, pts = r["order_id"], placed_ts(sim, r["order_id"])

    sim._trades_fetcher = lambda s, start_ms: [trade(1, 99.0, 2_000.0, pts + 1_000)]
    p1 = poll_now(sim, oid)
    assert p1["status"] == "PARTIAL", p1
    assert 0.0 < p1["filled_qty"] < 1000.0, p1
    # Queue ($5000) absorbed first; $14800 of the $19800 share filled 148 units
    assert abs(p1["filled_qty"] - 148.0) < 0.01, p1

    # A second wave of volume banks MORE, and the increment is reported.
    sim._trades_fetcher = lambda s, start_ms: [
        trade(1, 99.0, 2_000.0, pts + 1_000),   # already processed -> ignored
        trade(2, 99.0, 5_000.0, pts + 2_000),   # fresh -> fills 495 more
    ]
    p2 = poll_now(sim, oid)
    assert p2["status"] == "PARTIAL", p2
    assert abs(p2["new_filled_qty"] - 495.0) < 0.01, p2
    assert abs(p2["filled_qty"] - 643.0) < 0.01, p2
    assert p2["remaining_qty"] > 0


def test_cancel_maker_order():
    sim = make_sim()
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 99.50)
    oid = r["order_id"]
    c = sim.cancel_maker_order(oid)
    assert c["status"] == "CANCELLED", c
    # Even violent post-placement action cannot fill a cancelled order.
    sim._trades_fetcher = lambda s, start_ms: [trade(1, 99.0, 10_000.0, placed_ts(sim, oid) + 1_000)]
    p = poll_now(sim, oid)
    assert p["status"] == "CANCELLED" and p["filled_qty"] == 0.0, p


def test_poll_unknown_order():
    sim = make_sim()
    p = sim.poll_maker_order("nope_missing")
    assert p["status"] == "UNKNOWN", p


def test_taker_fill_has_slippage_and_taker_fee():
    sim = make_sim()
    r = sim.simulate_taker_fill("TSTUSDT", "SELL", 10.0)
    assert r["status"] == "FILLED"
    # SELL taker hits the bid and slips below it
    assert r["avg_price"] < 100.0, r
    assert r["fee_usd"] == round(r["notional_usd"] * TAKER_FEE_RATE, 4)
    assert r["slippage_usd_per_unit"] > 0


def test_taker_degraded_uses_reference_with_penalty():
    sim = PaperFillSimulator(
        cache_dir="/tmp/fill_sim_test",
        book_fetcher=lambda s: None,  # book down
        trades_fetcher=lambda s, start_ms: [],
        filters_fetcher=lambda s: dict(FILTERS),
    )
    r = sim.simulate_taker_fill("TSTUSDT", "SELL", 10.0, reference_price=100.0)
    assert r["status"] == "FILLED" and r["degraded"] is True
    # penalty slippage: 3x of (half spread + impact) below 100
    assert r["avg_price"] < 100.0 - 0.05, r


def test_maker_rejects_when_filters_unavailable():
    sim = PaperFillSimulator(
        cache_dir="/tmp/fill_sim_test",
        book_fetcher=lambda s: {"best_bid": 1, "best_ask": 2, "bids": [], "asks": []},
        trades_fetcher=lambda s, start_ms: [],
        filters_fetcher=lambda s: None,  # exchangeInfo down
    )
    r = sim.place_maker_order("TSTUSDT", "BUY", 1.0, 1.0)
    assert r["status"] == "REJECTED" and "filters_unavailable" in r["reason"], r


def test_fee_protected_break_even_long():
    # entry 100, qty 1, entry fee $0.015, funding $0.01 -> BE must exceed 100
    be = PaperFillSimulator.fee_protected_break_even(100.0, 1.0, True, 0.015, 0.01)
    assert be > 100.0, be
    # exiting at BE nets >= 0 after taker fee
    net = be * 1.0 * (1 - TAKER_FEE_RATE) - 100.0 * 1.0 - 0.015 - 0.01
    assert net >= -1e-9, net


def test_fee_protected_break_even_short():
    be = PaperFillSimulator.fee_protected_break_even(100.0, 1.0, False, 0.015, 0.01)
    assert be < 100.0, be
    net = 100.0 * 1.0 - be * 1.0 * (1 + TAKER_FEE_RATE) - 0.015 - 0.01
    assert net >= -1e-9, net


def test_funding_flat_rate():
    assert PaperFillSimulator.compute_funding(10_000.0) == 1.0
