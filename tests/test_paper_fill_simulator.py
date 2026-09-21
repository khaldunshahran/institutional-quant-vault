"""Tests for the paper fill simulator: post-only rejections, trade-through
fills, partial fills, taker slippage, fee math, and break-even math."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from paper_fill_simulator import PaperFillSimulator, MAKER_FEE_RATE, TAKER_FEE_RATE


def make_sim(book=None, klines=None, filters=None):
    book = book or {
        "best_bid": 100.0, "best_ask": 100.10,
        "bids": [(100.0, 50.0), (99.9, 100.0)],
        "asks": [(100.10, 50.0), (100.2, 100.0)],
    }
    klines = klines if klines is not None else []
    filters = filters or {"tick_size": 0.01, "step_size": 0.001, "min_notional": 5.0}
    return PaperFillSimulator(
        cache_dir="/tmp/fill_sim_test",
        book_fetcher=lambda s: book,
        klines_fetcher=lambda s, n: klines,
        filters_fetcher=lambda s: filters,
    )


def test_post_only_cross_rejected():
    sim = make_sim()
    # BUY limit at/above the ask would cross -> GTX reject
    r = sim.simulate_maker_fill("TSTUSDT", "BUY", 1.0, 100.10)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r
    # SELL limit at/below the bid would cross -> reject
    r = sim.simulate_maker_fill("TSTUSDT", "SELL", 1.0, 100.0)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r


def test_no_trade_through_expires():
    # Price never trades down through our 99.50 bid -> no fill
    klines = [
        {"open": 100, "high": 101, "low": 99.8, "close": 100.5, "quote_volume": 1_000_000},
        {"open": 100.5, "high": 102, "low": 99.9, "close": 101.5, "quote_volume": 1_000_000},
    ]
    sim = make_sim(klines=klines)
    r = sim.simulate_maker_fill("TSTUSDT", "BUY", 1.0, 99.50)
    assert r["status"] == "EXPIRED" and r["filled_qty"] == 0.0, r


def test_trade_through_fills_with_queue():
    # Queue ahead at 100.0: 50 units * 100 = $5000. First candle volume share
    # (10% of $20k = $2000) depletes part of queue; second candle fills us.
    klines = [
        {"open": 100, "high": 100.5, "low": 99.0, "close": 99.5, "quote_volume": 20_000},
        {"open": 99.5, "high": 100.2, "low": 99.0, "close": 100.0, "quote_volume": 200_000},
    ]
    sim = make_sim(klines=klines)
    r = sim.simulate_maker_fill("TSTUSDT", "BUY", 1.0, 100.0)
    assert r["status"] == "FILLED", r
    assert r["filled_qty"] == 1.0
    assert r["fee_usd"] == round(100.0 * MAKER_FEE_RATE, 4)


def test_partial_fill_when_volume_thin():
    klines = [
        {"open": 100, "high": 100.5, "low": 99.0, "close": 99.5, "quote_volume": 20_000},
    ]
    sim = make_sim(klines=klines)
    # Huge order vs thin volume -> partial at best
    r = sim.simulate_maker_fill("TSTUSDT", "BUY", 1000.0, 100.0)
    assert r["status"] in ("PARTIAL", "EXPIRED"), r
    assert r["filled_qty"] < 1000.0


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
        klines_fetcher=lambda s, n: [],
        filters_fetcher=lambda s: {"tick_size": 0.01, "step_size": 0.001, "min_notional": 5.0},
    )
    r = sim.simulate_taker_fill("TSTUSDT", "SELL", 10.0, reference_price=100.0)
    assert r["status"] == "FILLED" and r["degraded"] is True
    # penalty slippage: 3x of (half spread + impact) below 100
    assert r["avg_price"] < 100.0 - 0.05, r


def test_maker_rejects_when_filters_unavailable():
    sim = PaperFillSimulator(
        cache_dir="/tmp/fill_sim_test",
        book_fetcher=lambda s: {"best_bid": 1, "best_ask": 2, "bids": [], "asks": []},
        klines_fetcher=lambda s, n: [],
        filters_fetcher=lambda s: None,  # exchangeInfo down
    )
    r = sim.simulate_maker_fill("TSTUSDT", "BUY", 1.0, 1.0)
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
