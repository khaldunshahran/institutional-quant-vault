"""Regression tests for the Sep-2026 filter-integrity repair.

Root cause: a poisoned exchange_info_cache.json entry (tick_size 0.1 cached
for STXUSDT while STX trades ~$0.34) made place_maker_order() floor a 0.3476
SELL limit to 0.30, which then falsely tripped the post-only cross check
(21 ENTRY_FILL_FAILED on the inverted arm, 2026-09-26).

Invariants under test:
1. Implausible filters are NEVER used for rounding (loud filters_unavailable,
   never a silent false post_only_would_cross).
2. Fallback/defaults are NEVER written to the cache — only genuinely fetched
   data; failed fetches leave the old entry untouched.
3. Valid filters still round correctly; side-aware post-only checks intact.
4. Plausibility boundary: tick == 1% of price passes, above fails.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper_fill_simulator
from paper_fill_simulator import PaperFillSimulator

# STX-like market: ~$0.34
STX_BOOK = {
    "best_bid": 0.3412, "best_ask": 0.3414,
    "bids": [(0.3412, 5000.0), (0.3411, 8000.0)],
    "asks": [(0.3414, 5000.0), (0.3415, 8000.0)],
}
# The poisoned entry observed live: 0.1 tick on a $0.34 symbol (30% grid).
POISONED_FILTERS = {"tick_size": 0.1, "step_size": 0.001, "min_notional": 50.0}
# Genuine STXUSDT filters as Binance reports them.
REAL_STX_FILTERS = {"tick_size": 0.0001, "step_size": 1.0, "min_notional": 5.0}

BTC_BOOK = {
    "best_bid": 84327.3, "best_ask": 84327.5,
    "bids": [(84327.3, 2.0)], "asks": [(84327.5, 2.0)],
}
BTC_FILTERS = {"tick_size": 0.1, "step_size": 0.001, "min_notional": 50.0}


def make_sim_injected(tmp_path, filters, book=None):
    """Simulator with an injected (deterministic) filters_fetcher."""
    return PaperFillSimulator(
        cache_dir=str(tmp_path),
        book_fetcher=lambda s: book if book is not None else STX_BOOK,
        trades_fetcher=lambda s, start_ms: [],
        filters_fetcher=lambda s: dict(filters),
    )


def seed_cache(tmp_path, entries):
    p = tmp_path / "exchange_info_cache.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


def genuine_stx_payload():
    return {"symbols": [{"filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
        {"filterType": "LOT_SIZE", "stepSize": "1"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]}]}


# ------------------------------------------------------------------
# 1. The incident: poisoned tick must not be used for rounding
# ------------------------------------------------------------------

def test_stx_poisoned_tick_rejected_not_rounded(tmp_path):
    """The exact 2026-09-26 incident: SELL 0.3476 with cached tick 0.1 must
    NOT be floored to 0.30 (false post_only_would_cross). It must fail loud
    as filters_unavailable with the limit untouched."""
    sim = make_sim_injected(tmp_path, POISONED_FILTERS, book=STX_BOOK)
    r = sim.place_maker_order("STXUSDT", "SELL", 1000.0, 0.3476)
    assert r["status"] == "REJECTED", r
    assert "filters_unavailable" in r["reason"], r
    assert "post_only_would_cross" not in r["reason"], r
    assert r["limit_price"] == 0.3476, r  # never rounded to the garbage grid


def test_poisoned_tick_buy_side_also_rejected(tmp_path):
    sim = make_sim_injected(tmp_path, POISONED_FILTERS, book=STX_BOOK)
    r = sim.place_maker_order("STXUSDT", "BUY", 1000.0, 0.3300)
    assert r["status"] == "REJECTED" and "filters_unavailable" in r["reason"], r


# ------------------------------------------------------------------
# 2. Defaults/failures are never written to the cache
# ------------------------------------------------------------------

def test_fetch_failure_writes_nothing_to_cache(tmp_path, monkeypatch):
    good = {"BTCUSDT": {"cached_at": time.time(),
                        "filters": dict(BTC_FILTERS)}}
    cache_file = seed_cache(tmp_path, good)
    before = cache_file.read_bytes()
    monkeypatch.setattr(paper_fill_simulator, "_http_get_json",
                        lambda url, timeout=6.0: None)
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: BTC_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    assert sim._fetch_filters("NEWCOINUSDT") is None
    assert sim._refetch_filters_live("NEWCOINUSDT", 1.0) is None
    assert cache_file.read_bytes() == before  # untouched: no defaults entry


def test_degenerate_payload_not_cached(tmp_path, monkeypatch):
    """tickSize 0 from a (broken) payload must not be cached."""
    cache_file = seed_cache(tmp_path, {})
    monkeypatch.setattr(
        paper_fill_simulator, "_http_get_json",
        lambda url, timeout=6.0: {"symbols": [{"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]})
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: BTC_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    assert sim._refetch_filters_live("XUSDT", 100.0) is None
    assert json.loads(cache_file.read_text()) == {}


def test_implausible_live_payload_not_cached(tmp_path, monkeypatch):
    """Genuinely fetched but implausible-at-price data is not cached either."""
    cache_file = seed_cache(tmp_path, {})
    monkeypatch.setattr(paper_fill_simulator, "_http_get_json",
                        lambda url, timeout=6.0: genuine_stx_payload())
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: STX_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    # Genuine STX tick 0.0001 is implausible at a $0.000001 price -> not cached.
    assert sim._refetch_filters_live("STXUSDT", 0.000001) is None
    assert json.loads(cache_file.read_text()) == {}


def test_refetch_failure_keeps_old_cache_entry(tmp_path, monkeypatch):
    """Poisoned entry + dead network: the old entry stays as-is (no fresh
    timestamp stamped on it), and placement fails loud."""
    poisoned = {"STXUSDT": {"cached_at": 1790407359.0,
                            "filters": dict(POISONED_FILTERS)}}
    cache_file = seed_cache(tmp_path, poisoned)
    monkeypatch.setattr(paper_fill_simulator, "_http_get_json",
                        lambda url, timeout=6.0: None)
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: STX_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    r = sim.place_maker_order("STXUSDT", "SELL", 1000.0, 0.3476)
    assert r["status"] == "REJECTED" and "filters_unavailable" in r["reason"], r
    assert json.loads(cache_file.read_text()) == poisoned  # byte-identical


def test_poisoned_file_cache_self_heals_on_refetch(tmp_path, monkeypatch):
    """Poisoned entry + working network: one live refetch heals the cache and
    the order places at the correctly rounded limit."""
    seed_cache(tmp_path, {"STXUSDT": {"cached_at": 1790407359.0,
                                      "filters": dict(POISONED_FILTERS)}})
    monkeypatch.setattr(paper_fill_simulator, "_http_get_json",
                        lambda url, timeout=6.0: genuine_stx_payload())
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: STX_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    r = sim.place_maker_order("STXUSDT", "SELL", 1000.0, 0.3476)
    assert r["status"] == "WORKING", r
    assert r["limit_price"] == 0.3476, r  # 0.0001 grid: untouched
    healed = json.loads((tmp_path / "exchange_info_cache.json").read_text())
    assert healed["STXUSDT"]["filters"] == REAL_STX_FILTERS, healed


def test_valid_cached_filters_used_without_network(tmp_path, monkeypatch):
    """Happy path: fresh valid cache entry -> no HTTP at all."""
    def boom(url, timeout=6.0):
        raise AssertionError("network must not be touched")
    monkeypatch.setattr(paper_fill_simulator, "_http_get_json", boom)
    seed_cache(tmp_path, {"BTCUSDT": {"cached_at": time.time(),
                                      "filters": dict(BTC_FILTERS)}})
    sim = PaperFillSimulator(cache_dir=str(tmp_path),
                             book_fetcher=lambda s: BTC_BOOK,
                             trades_fetcher=lambda s, start_ms: [])
    r = sim.place_maker_order("BTCUSDT", "BUY", 0.1186, 84327.46)
    assert r["status"] == "WORKING", r


# ------------------------------------------------------------------
# 3. Valid filters still round correctly; post-only checks intact
# ------------------------------------------------------------------

def test_btc_tick_rounds_correctly(tmp_path):
    sim = make_sim_injected(tmp_path, BTC_FILTERS, book=BTC_BOOK)
    r = sim.place_maker_order("BTCUSDT", "BUY", 0.1186, 84327.46)
    assert r["status"] == "WORKING", r
    assert abs(r["limit_price"] - 84327.4) < 1e-9, r  # floored to the 0.1 grid
    assert abs(r["requested_qty"] - 0.118) < 1e-12, r  # floored to the 0.001 step


def test_post_only_checks_still_side_aware(tmp_path):
    sim = make_sim_injected(tmp_path, BTC_FILTERS, book=BTC_BOOK)
    # BUY at the ask crosses -> reject
    r = sim.place_maker_order("BTCUSDT", "BUY", 0.1, 84327.5)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r
    # SELL at the bid crosses -> reject
    r = sim.place_maker_order("BTCUSDT", "SELL", 0.1, 84327.3)
    assert r["status"] == "REJECTED" and "post_only_would_cross" in r["reason"], r
    # SELL above the bid rests -> working
    r = sim.place_maker_order("BTCUSDT", "SELL", 0.1, 84327.62)
    assert r["status"] == "WORKING" and r["limit_price"] == 84327.6, r


def test_taker_ignores_poisoned_tick(tmp_path):
    """Taker path: a poisoned tick must not falsify the fill price (old code
    floored the $0.34 fill to $0.30). It fills unrounded instead."""
    sim = make_sim_injected(tmp_path, POISONED_FILTERS, book=STX_BOOK)
    r = sim.simulate_taker_fill("STXUSDT", "SELL", 1000.0)
    assert r["status"] == "FILLED", r
    assert r["avg_price"] > 0.34, r  # ~0.3411, never 0.30


# ------------------------------------------------------------------
# 4. Plausibility boundary
# ------------------------------------------------------------------

def test_tick_plausibility_boundary():
    assert PaperFillSimulator._tick_plausible(1.0, 100.0) is True   # exactly 1%: passes
    assert PaperFillSimulator._tick_plausible(1.0001, 100.0) is False
    assert PaperFillSimulator._tick_plausible(0.0, 100.0) is False
    assert PaperFillSimulator._tick_plausible(-0.01, 100.0) is False
    assert PaperFillSimulator._tick_plausible(0.01, 0.0) is False
    assert PaperFillSimulator._tick_plausible(0.01, -5.0) is False
    assert PaperFillSimulator._tick_plausible("bad", 100.0) is False
    assert PaperFillSimulator._tick_plausible(None, 100.0) is False


def test_filters_plausibility_cases():
    P = PaperFillSimulator._filters_plausible
    good = {"tick_size": 0.01, "step_size": 0.001, "min_notional": 5.0}
    assert P(good, 100.0) is True
    assert P(None, 100.0) is False
    assert P("nope", 100.0) is False
    assert P({"tick_size": 0.01}, 100.0) is False                      # missing step
    assert P({"tick_size": 0.1, "step_size": 0.001,
              "min_notional": 50.0}, 0.34) is False                   # the STX incident
    assert P({"tick_size": 0.01, "step_size": 200.0,
              "min_notional": 5.0}, 100.0) is False                   # $20k per step
    assert P({"tick_size": 0.01, "step_size": 0.001,
              "min_notional": 0.0}, 100.0) is False
    assert P(good, 0.0) is False
