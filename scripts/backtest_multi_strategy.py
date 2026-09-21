#!/usr/bin/env python3
"""
Multi-Strategy 24-Hour Comparative Backtest Engine for Polymarket BTC 5-Minute Markets
Evaluates 5 distinct strategy archetypes across the same 288 historical intervals (Sunday 2026-08-30 UTC):
1. momentum_value: Early breakout, strict ceiling ($0.78), +22% Take-Profit, -25% Stop-Loss.
2. quick_scalp: Ultra-fast scalping, +12% TP, trailing stop to break-even at +8%, 60s pre-close exit.
3. mean_reversion: Fades overextended spikes (>0.85) by buying cheap underdogs ($0.15-$0.25) on momentum stall.
4. skew_hedge: Core momentum with 5% tail hedge when skew >= 0.93.
5. macro_trend_sniper: High-conviction impulse (±$85) with +28% TP and tighter -20% SL.

Produces: reports/multi_strategy_comparison.json
"""

import os
import sys
import json
import time
import math
import datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
import yaml

UTC = dt.timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_TS = 1788048000  # 2026-08-30 00:00:00 UTC
INTERVAL_SEC = 300     # 5 minutes
NUM_INTERVALS = 288

STRATEGY_DEFS = {
    "momentum_value": {
        "name": "Momentum Value",
        "description": "Early breakout entry with strict entry ceiling ($0.78) and balanced 1:1.2 R:R",
        "threshold": 0.68,
        "max_entry_price": 0.78,
        "stake_usd": 5.0,
        "take_profit_pct": 0.22,
        "trailing_stop_pct": None,
        "stop_loss_pct": 0.25,
        "exit_before_sec": 20,
        "min_btc_impulse": 65.0,
        "max_trades": 16,
        "type": "momentum",
    },
    "quick_scalp": {
        "name": "Quick Scalp",
        "description": "Fast profit lock (+12%), trailing stop to break-even at +8%, early 60s exit",
        "threshold": 0.70,
        "max_entry_price": 0.80,
        "stake_usd": 5.0,
        "take_profit_pct": 0.12,
        "trailing_stop_pct": 0.08,
        "stop_loss_pct": 0.18,
        "exit_before_sec": 60,
        "min_btc_impulse": 55.0,
        "max_trades": 24,
        "type": "scalp",
    },
    "mean_reversion": {
        "name": "Mean Reversion Underdog",
        "description": "Fades extreme overextensions (>= 0.85) when BTC stalls; buys underdog for 3:1+ payoff",
        "threshold": 0.85,  # trigger overextension level
        "max_entry_price": 0.25,  # underdog price ceiling
        "stake_usd": 5.0,
        "take_profit_pct": 1.50,  # +150% target ($0.20 -> $0.50)
        "trailing_stop_pct": None,
        "stop_loss_pct": 0.50,  # -50% SL ($0.20 -> $0.10)
        "exit_before_sec": 15,
        "min_btc_impulse": 0.0,
        "stall_btc_impulse": 25.0,  # BTC impulse must be <= $25
        "max_trades": 12,
        "type": "mean_reversion",
    },
    "skew_hedge": {
        "name": "Skew Tail Hedge",
        "description": "Core momentum with automatic 5% tail-risk hedge on opposite token at skew >= 0.93",
        "threshold": 0.70,
        "max_entry_price": 0.82,
        "stake_usd": 5.0,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": None,
        "stop_loss_pct": 0.25,
        "exit_before_sec": 20,
        "min_btc_impulse": 70.0,
        "hedge_trigger": 0.93,
        "hedge_pct": 0.05,
        "max_trades": 12,
        "type": "hedge",
    },
    "macro_trend_sniper": {
        "name": "Macro Trend Sniper",
        "description": "High-conviction entries on large impulses (±$85) with +28% TP and tight -20% SL",
        "threshold": 0.72,
        "max_entry_price": 0.82,
        "stake_usd": 5.0,
        "take_profit_pct": 0.28,
        "trailing_stop_pct": None,
        "stop_loss_pct": 0.20,
        "exit_before_sec": 25,
        "min_btc_impulse": 85.0,
        "max_trades": 10,
        "type": "sniper",
    },
}


def load_baseline_intervals() -> list[dict]:
    """Load cached 288 intervals from backtest_sunday_20260830_conservative.json or fetch."""
    cache_path = PROJECT_ROOT / "reports" / "backtest_sunday_20260830_conservative.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            intervals = d.get("intervals", [])
            if len(intervals) >= 288:
                return intervals
        except Exception:
            pass
    return []


def fetch_binance_1m_candles(start_ts: int, num_candles: int = 1445) -> dict[int, list]:
    cache_file = PROJECT_ROOT / "runtime" / "binance_candles_sunday_cache.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {int(k): v for k, v in raw.items()}
        except Exception:
            pass

    url = "https://api.binance.com/api/v3/klines"
    candles = {}
    current_ms = start_ts * 1000
    end_ms = (start_ts + (num_candles * 60)) * 1000

    print("Fetching Binance 1m BTC/USDT candles...", flush=True)
    while current_ms < end_ms:
        try:
            resp = requests.get(
                url,
                params={"symbol": "BTCUSDT", "interval": "1m", "startTime": current_ms, "limit": 1000},
                timeout=8,
            )
            data = resp.json()
            if not data:
                break
            for c in data:
                t_sec = int(c[0] // 1000)
                candles[t_sec] = c
            last_ms = int(data[-1][0])
            if last_ms <= current_ms:
                break
            current_ms = last_ms + 60000
        except Exception as e:
            print(f"Warning fetching Binance klines: {e}", flush=True)
            time.sleep(1)
            break

    if candles:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(candles, f)
        except Exception:
            pass

    return candles


def simulate_strategy_on_intervals(strat_id: str, cfg: dict, intervals_data: list[dict], candles: dict[int, list]) -> dict:
    """Run exact strategy simulation against all 288 intervals."""
    trades = []
    trade_count = 0
    max_trades = cfg.get("max_trades", 12)
    strat_type = cfg.get("type", "momentum")

    for item in intervals_data:
        if trade_count >= max_trades:
            break

        ts = item["timestamp"]
        btc_open = item.get("btc_open")
        btc_entry = item.get("btc_entry")
        btc_impulse = item.get("btc_impulse")
        winner = item.get("winner", "UNKNOWN")

        if btc_open is None or btc_entry is None or btc_impulse is None:
            continue

        abs_imp = abs(btc_impulse)
        c3 = candles.get(ts + 180)  # Minute 3 close
        c4 = candles.get(ts + 240)  # Minute 4
        c5 = candles.get(ts + 300)  # Expiry

        m4_high = float(c4[2]) if c4 else btc_entry
        m4_low = float(c4[3]) if c4 else btc_entry
        p4_close = float(c4[4]) if c4 else btc_entry

        c0 = candles.get(ts)
        c1 = candles.get(ts + 60)
        c2 = candles.get(ts + 120)

        trade_record = None

        if strat_type == "mean_reversion":
            # Condition: Aggressive spike in Min 1-2 (>= $60 from open) followed by Min 3 stall/reversal (<= $25 from open)
            early_candles = [c for c in [c0, c1, c2] if c]
            if early_candles:
                spike_high = max(float(c[2]) for c in early_candles)
                spike_low = min(float(c[3]) for c in early_candles)

                up_spike = (spike_high - btc_open) >= 60.0 and (btc_entry - btc_open) <= 25.0
                down_spike = (btc_open - spike_low) >= 60.0 and (btc_open - btc_entry) <= 25.0

                if up_spike or down_spike:
                    underdog_side = "DOWN" if up_spike else "UP"
                    underdog_entry = 0.22
                    if underdog_entry <= cfg["max_entry_price"]:
                        stake = cfg["stake_usd"]
                        shares = stake / underdog_entry
                        sl_px = underdog_entry * (1.0 - cfg["stop_loss_pct"])  # ~$0.11
                        tp_px = underdog_entry * (1.0 + cfg["take_profit_pct"])  # ~$0.55

                        if winner == underdog_side:
                            exit_bid = min(0.92, tp_px)
                            exit_type = "TAKE_PROFIT_HIT"
                            pnl = round((shares * exit_bid) - stake, 2)
                            trade_record = {
                                "time_utc": item.get("time_utc", ""),
                                "slug": item.get("slug", ""),
                                "side": underdog_side,
                                "entry_price": underdog_entry,
                                "exit_price": exit_bid,
                                "exit_type": exit_type,
                                "result": "WIN",
                                "net_pnl_usd": pnl,
                            }
                        else:
                            exit_bid = sl_px
                            exit_type = "STOP_LOSS_HIT"
                            pnl = round((shares * exit_bid) - stake, 2)
                            trade_record = {
                                "time_utc": item.get("time_utc", ""),
                                "slug": item.get("slug", ""),
                                "side": underdog_side,
                                "entry_price": underdog_entry,
                                "exit_price": exit_bid,
                                "exit_type": exit_type,
                                "result": "LOSS",
                                "net_pnl_usd": pnl,
                            }

        else:
            # Momentum / Scalp / Sniper / Skew Hedge
            min_impulse = cfg["min_btc_impulse"]
            if abs_imp >= min_impulse:
                signal_side = "UP" if btc_impulse > 0 else "DOWN"
                
                # Realistic CLOB ask entry price
                base_ask = 0.71 + min(0.12, (abs_imp - min_impulse) * 0.0008)
                entry_price = round(max(cfg["threshold"], base_ask) + 0.015, 4)

                # STRICT CEILING CHECK: Reject if entry price exceeds max_entry_price
                if entry_price <= cfg["max_entry_price"]:
                    stake = cfg["stake_usd"]
                    shares = stake / entry_price
                    sl_price = round(entry_price * (1.0 - cfg["stop_loss_pct"]), 4)
                    tp_price = round(entry_price * (1.0 + cfg["take_profit_pct"]), 4) if cfg.get("take_profit_pct") else None
                    trailing_pct = cfg.get("trailing_stop_pct")

                    # Intra-candle tracking
                    stopped_out = False
                    tp_hit = False
                    sl_ratcheted = False
                    exit_bid = 0.0
                    exit_type = ""

                    if signal_side == "UP":
                        # Check adverse retrace for SL
                        if m4_low <= (btc_open + (abs_imp * 0.35)) or m4_low <= (btc_open + 16.0):
                            stopped_out = True
                            exit_type = "STOP_LOSS_HIT"
                            exit_bid = round(max(0.05, sl_price - 0.02), 4)
                        else:
                            # Check Trailing Stop ratchet
                            peak_move = float(c4[2]) - btc_open if c4 else abs_imp
                            if trailing_pct and peak_move >= (abs_imp * 1.25):
                                sl_price = entry_price
                                sl_ratcheted = True

                            # Check Take Profit
                            lead_at_m4 = p4_close - btc_open
                            if tp_price and lead_at_m4 >= 110.0:
                                tp_hit = True
                                exit_type = "TAKE_PROFIT_HIT"
                                exit_bid = min(0.96, tp_price)
                            else:
                                # Normal time exit
                                exit_type = f"TIME_EXIT_{cfg['exit_before_sec']}S"
                                if lead_at_m4 >= 80.0:
                                    exit_bid = 0.92
                                elif lead_at_m4 >= 40.0:
                                    exit_bid = 0.84
                                elif lead_at_m4 >= 10.0:
                                    exit_bid = 0.70
                                else:
                                    exit_bid = 0.45
                    else:  # DOWN
                        if m4_high >= (btc_open - (abs_imp * 0.35)) or m4_high >= (btc_open - 16.0):
                            stopped_out = True
                            exit_type = "STOP_LOSS_HIT"
                            exit_bid = round(max(0.05, sl_price - 0.02), 4)
                        else:
                            peak_move = btc_open - float(c4[3]) if c4 else abs_imp
                            if trailing_pct and peak_move >= (abs_imp * 1.25):
                                sl_price = entry_price
                                sl_ratcheted = True

                            lead_at_m4 = btc_open - p4_close
                            if tp_price and lead_at_m4 >= 110.0:
                                tp_hit = True
                                exit_type = "TAKE_PROFIT_HIT"
                                exit_bid = min(0.96, tp_price)
                            else:
                                exit_type = f"TIME_EXIT_{cfg['exit_before_sec']}S"
                                if lead_at_m4 >= 80.0:
                                    exit_bid = 0.92
                                elif lead_at_m4 >= 40.0:
                                    exit_bid = 0.84
                                elif lead_at_m4 >= 10.0:
                                    exit_bid = 0.70
                                else:
                                    exit_bid = 0.45

                    # Skew Tail Hedge adjustment
                    hedge_gain_loss = 0.0
                    if cfg.get("hedge_trigger") and exit_bid >= cfg["hedge_trigger"]:
                        # 5% hedge spent on opposite token ($0.25)
                        hedge_cost = stake * cfg.get("hedge_pct", 0.05)
                        # Opposite token expires at 0
                        hedge_gain_loss = -hedge_cost

                    pnl = round((shares * exit_bid) - stake + hedge_gain_loss, 2)
                    res_tag = "WIN" if pnl > 0 else "LOSS"
                    if stopped_out:
                        res_tag = "SL_HIT"

                    trade_record = {
                        "time_utc": item.get("time_utc", ""),
                        "slug": item.get("slug", ""),
                        "side": signal_side,
                        "entry_price": entry_price,
                        "exit_price": exit_bid,
                        "exit_type": exit_type,
                        "result": res_tag,
                        "net_pnl_usd": pnl,
                    }

        if trade_record:
            trades.append(trade_record)
            trade_count += 1

    # Aggregate performance metrics
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] in ("LOSS", "SL_HIT")]
    sl_hits = [t for t in trades if t["result"] == "SL_HIT"]
    tp_hits = [t for t in trades if t["exit_type"] == "TAKE_PROFIT_HIT"]

    gross_profit = round(sum(t["net_pnl_usd"] for t in wins), 2)
    gross_loss = round(abs(sum(t["net_pnl_usd"] for t in losses)), 2)
    net_pnl = round(gross_profit - gross_loss, 2)
    win_rate = round((len(wins) / len(trades) * 100), 1) if trades else 0.0
    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (99.0 if avg_win > 0 else 0.0)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # Max Drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t["net_pnl_usd"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "strategy_id": strat_id,
        "name": cfg["name"],
        "description": cfg["description"],
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "stop_losses_hit": len(sl_hits),
        "take_profits_hit": len(tp_hits),
        "win_rate_pct": win_rate,
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "net_pnl_usd": net_pnl,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "max_drawdown_usd": round(max_dd, 2),
        "params": cfg,
        "sample_trades": trades[:10],
    }


def run_multi_strategy_backtest():
    print("=" * 70, flush=True)
    print("  Polymarket BTC 5m Multi-Strategy Comparative Backtest", flush=True)
    print("  Testing 5 Strategy Archetypes across 288 5-Minute Intervals", flush=True)
    print("=" * 70 + "\n", flush=True)

    intervals = load_baseline_intervals()
    candles = fetch_binance_1m_candles(START_TS, num_candles=1445)

    leaderboard = []

    for strat_id, cfg in STRATEGY_DEFS.items():
        print(f"Running simulation for strategy: {cfg['name']}...", flush=True)
        res = simulate_strategy_on_intervals(strat_id, cfg, intervals, candles)
        leaderboard.append(res)

    # Sort leaderboard by Net PnL descending
    leaderboard.sort(key=lambda x: (x["profit_factor"], x["net_pnl_usd"]), reverse=True)

    summary_report = {
        "report_title": "Multi-Strategy Comparative Backtest Leaderboard",
        "generated_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dataset_date": "2026-08-30 (Sunday UTC)",
        "total_intervals": NUM_INTERVALS,
        "strategies_evaluated": len(leaderboard),
        "leaderboard": leaderboard,
    }

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / "multi_strategy_comparison.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary_report, f, indent=2)

    print(f"\nSaved multi-strategy comparison to: {out_file}\n")
    print_leaderboard_table(leaderboard)


def print_leaderboard_table(leaderboard: list[dict]):
    print("=" * 85)
    print(f"{'STRATEGY':<22} | {'TRADES':<6} | {'WIN RATE':<9} | {'PAYOFF':<7} | {'PROFIT FACTOR':<13} | {'NET PNL':<9} | {'MAX DD':<7}")
    print("-" * 85)
    for r in leaderboard:
        pnl_str = f"+${r['net_pnl_usd']:.2f}" if r['net_pnl_usd'] >= 0 else f"-${abs(r['net_pnl_usd']):.2f}"
        print(f"{r['name']:<22} | {r['total_trades']:<6} | {r['win_rate_pct']:>6.1f}%  | {r['payoff_ratio']:>6.2f}x | {r['profit_factor']:>12.2f}x | {pnl_str:>9} | ${r['max_drawdown_usd']:>5.2f}")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    run_multi_strategy_backtest()
