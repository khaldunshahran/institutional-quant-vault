#!/usr/bin/env python3
"""
Latency Benchmark: Compares legacy sequential HTTP polling against
the upgraded StreamManager (WebSocket streaming + Parallel Keep-Alive connection pooling).
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import requests
from scripts.stream_manager import StreamManager
from scripts.test_btc_5m_session_exit_sl import resolve_active_current_5m_market, market_side_prices

def run_benchmark():
    print("=" * 60)
    print("  BTC 5M Trading Engine Latency & Transmission Benchmark  ")
    print("=" * 60)

    # 1. Resolve active market
    m = resolve_active_current_5m_market()
    assert m is not None, "Active 5M market must be available"
    _, _, up_t, dn_t, slug, _ = market_side_prices(m)
    print(f"Active Market: {slug}")
    print(f"UP Token:     {up_t[:16]}...")
    print(f"DOWN Token:   {dn_t[:16]}...\n")

    # --- BENCHMARK 1: Binance Price Telemetry ---
    print("[1] Binance BTC Spot & 5M Impulse Telemetry:")
    # Legacy: Raw HTTP GET
    t0 = time.time()
    for _ in range(3):
        _ = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=3.0)
    t_legacy_binance = ((time.time() - t0) / 3) * 1000

    # Upgraded: StreamManager in-memory WebSocket lookup
    sm = StreamManager.get_instance()
    time.sleep(2.0) # warm up stream
    t0 = time.time()
    for _ in range(100):
        spot, open_px, imp = sm.get_btc_telemetry(int(time.time()))
    t_ws_binance = ((time.time() - t0) / 100) * 1000

    print(f"  Legacy Raw HTTP REST:       {t_legacy_binance:8.2f} ms")
    print(f"  Upgraded WebSocket Stream:  {t_ws_binance:8.4f} ms")
    binance_speedup = t_legacy_binance / max(0.001, t_ws_binance)
    print(f"  >> Speedup: {binance_speedup:,.0f}x faster! (Saved ~{t_legacy_binance - t_ws_binance:.1f}ms)\n")

    # --- BENCHMARK 2: Polymarket Orderbooks Query ---
    print("[2] Polymarket CLOB UP & DOWN Orderbooks:")
    # Legacy: Sequential raw HTTP GET
    t0 = time.time()
    for _ in range(2):
        _ = requests.get(f"https://clob.polymarket.com/book?token_id={up_t}", timeout=3.0).json()
        _ = requests.get(f"https://clob.polymarket.com/book?token_id={dn_t}", timeout=3.0).json()
    t_legacy_pm = ((time.time() - t0) / 2) * 1000

    # Upgraded: Parallel ThreadPool + Persistent Keep-Alive Session
    t0 = time.time()
    for _ in range(3):
        _ = sm.get_full_orderbooks(up_t, dn_t)
    t_par_pm = ((time.time() - t0) / 3) * 1000

    print(f"  Legacy Sequential HTTP:     {t_legacy_pm:8.2f} ms")
    print(f"  Upgraded Parallel Pool:     {t_par_pm:8.2f} ms")
    pm_speedup = t_legacy_pm / max(0.001, t_par_pm)
    print(f"  >> Speedup: {pm_speedup:.2f}x faster! (Saved ~{t_legacy_pm - t_par_pm:.1f}ms)\n")

    # --- BENCHMARK 3: End-to-End Decision Cycle Latency ---
    print("[3] Total End-to-End Decision Cycle Latency:")
    t_legacy_total = t_legacy_binance + t_legacy_pm
    t_upgraded_total = t_ws_binance + t_par_pm

    print(f"  Total Legacy Cycle:         {t_legacy_total:8.2f} ms")
    print(f"  Total Upgraded Cycle:       {t_upgraded_total:8.2f} ms")
    total_speedup = t_legacy_total / max(0.001, t_upgraded_total)
    print(f"  >> OVERALL SPEEDUP: {total_speedup:.2f}x faster!")
    print(f"  >> Net Latency Reduction: -{t_legacy_total - t_upgraded_total:.1f} ms per tick")
    print("=" * 60 + "\n")

    sm.stop()

if __name__ == "__main__":
    run_benchmark()
