#!/usr/bin/env python3
"""
decision_calibration.py — calibrate the SIGNAL against reality. (v2 merged)

Joins runtime/decision_log.jsonl (every APPROVED/REJECTED/SKIPPED decision:
setup, JEV conviction, trap risk) with runtime/autonomous_trade_history.json
(actual outcomes) and answers:

  1. DECISION FUNNEL:      how many signals survive each gate?
  2. PER-SETUP ATTRIBUTION: which setups make/lose money?
  3. CONVICTION CALIBRATION: do high-conviction trades win more?
  4. TRAP-RISK CALIBRATION:  does low trap-risk predict winners?
  5. EXIT REASONS / SIDES / SYMBOLS: where does the money go?
  6. JEV VETO FUNNEL:        what the brain rejects (outcomes unknowable)

Usage:
    python decision_calibration.py --runtime runtime
    python decision_calibration.py --runtime runtime --since 7d
    python decision_calibration.py --runtime runtime --since "2026-09-21 17:04"

READ-ONLY. Changes nothing on the live engine.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_since(s):
    s = s.strip()
    mm = re.fullmatch(r"(\d+)\s*([dh])", s, re.IGNORECASE)
    if mm:
        mult = {"d": 86400, "h": 3600}[mm.group(2).lower()]
        return datetime.now(timezone.utc) - timedelta(seconds=int(mm.group(1)) * mult)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    print(f"bad --since value: {s!r}", file=sys.stderr)
    sys.exit(2)


def load_history(runtime, since):
    path = os.path.join(runtime, "autonomous_trade_history.json")
    with open(path, encoding="utf-8") as f:
        trades = json.load(f)
    out = []
    for t in trades:
        try:
            closed = datetime.strptime(str(t["closed_at"]), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if since and closed < since:
            continue
        dur = max(float(t.get("duration_sec") or 0), 0)
        try:
            entry = float(t["entry_price"])
        except Exception:
            continue
        out.append({
            "symbol": str(t.get("symbol")),
            "side": str(t.get("side")),
            "entry": entry,
            "pnl": float(t.get("pnl_usd") or 0),
            "exit_reason": str(t.get("exit_reason") or "UNKNOWN"),
            "opened_epoch": closed.timestamp() - dur,
            "duration_min": dur / 60.0,
            "signal_inverted": bool(t.get("signal_inverted", False)),
            "setup": t.get("setup"),
            "conv": t.get("jev_conviction"),
            "trap": t.get("jev_trap_risk"),
            "mode": t.get("jev_engine_mode") or t.get("mode"),
        })
    return out


def load_decisions(runtime, since_epoch):
    path = os.path.join(runtime, "decision_log.jsonl")
    approvals = defaultdict(list)
    vetoes = defaultdict(int)
    appr_by_setup = defaultdict(int)
    outcomes = Counter()
    reasons = Counter()
    n_approved = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = float(r.get("ts_epoch") or 0)
            if since_epoch and ts < since_epoch:
                continue
            outcome, reason = r.get("outcome"), r.get("reason")
            outcomes[outcome] += 1
            reasons[reason] += 1
            det = r.get("details") or {}
            if outcome == "APPROVED" and reason == "SIGNAL_ACCEPTED":
                n_approved += 1
                setup = det.get("setup") or "UNKNOWN"
                appr_by_setup[setup] += 1
                try:
                    entry = float(det.get("entry_price"))
                except Exception:
                    continue
                approvals[str(r.get("symbol"))].append({
                    "ts": ts, "entry": entry, "setup": setup,
                    "conv": det.get("jev_conviction"),
                    "trap": det.get("jev_trap_risk"),
                    "mode": det.get("jev_engine_mode"),
                })
            elif outcome == "REJECTED" and reason == "JEV_VETO":
                vetoes[det.get("setup") or "UNKNOWN"] += 1
    return approvals, vetoes, appr_by_setup, n_approved, outcomes, reasons


def summarize(rows):
    pnls = [r["pnl"] for r in rows]
    n = len(pnls)
    net = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else 0.0)
    return {"n": n, "net": net, "exp": net / n if n else 0.0,
            "wins": len(wins), "win%": len(wins) / n * 100 if n else 0.0, "pf": pf}


def pf_str(pf):
    return "   inf" if pf == float("inf") else f"{pf:>6.2f}"


def norm_side(s):
    """Engines log BUY/SELL or LONG/SHORT depending on version."""
    s = str(s).upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    return s


def bucket(rows, key, edges):
    labels = [f"<={edges[0]}"] + \
             [f"{edges[i]}-{edges[i+1]}" for i in range(len(edges) - 1)] + \
             [f">{edges[-1]}"]
    groups = {lb: [] for lb in labels}
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        if v <= edges[0]:
            groups[labels[0]].append(r)
        elif v > edges[-1]:
            groups[labels[-1]].append(r)
        else:
            for i in range(len(edges) - 1):
                if edges[i] < v <= edges[i + 1]:
                    groups[labels[i + 1]].append(r)
                    break
    return [(lb, groups[lb]) for lb in labels]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.path.join(REPO_ROOT, "runtime"))
    ap.add_argument("--since", default=None, help="'7d', '24h', or '2026-09-21 17:04' (UTC)")
    ap.add_argument("--window-min", type=float, default=10.0)
    ap.add_argument("--price-tol", type=float, default=0.002)
    args = ap.parse_args()

    since = parse_since(args.since) if args.since else None
    since_epoch = since.timestamp() if since else 0

    trades = load_history(args.runtime, since)
    if not trades:
        print("no closed trades in window.")
        return
    approvals, vetoes, appr_by_setup, n_approved, outcomes, reasons = \
        load_decisions(args.runtime, since_epoch)

    matched, unmatched = [], 0
    for t in trades:
        # If the trade already has the decision fields directly logged, use them
        if t.get("setup"):
            matched.append(dict(t))
            continue
            
        best, best_dt = None, None
        for d in approvals.get(t["symbol"], []):
            dt = abs(d["ts"] - t["opened_epoch"])
            if dt <= args.window_min * 60 and (best_dt is None or dt < best_dt):
                if abs(d["entry"] - t["entry"]) / t["entry"] <= args.price_tol:
                    best, best_dt = d, dt
        if best is None:
            unmatched += 1
            continue
        r = dict(t)
        r.update({"setup": best["setup"], "conv": best["conv"],
                  "trap": best["trap"], "mode": best["mode"]})
        matched.append(r)

    print()
    print("=" * 78)
    print("DECISION CALIBRATION — does the signal predict reality?")
    print("=" * 78)
    if since:
        print(f"  window: since {since.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  trades: {len(trades)} | joined to APPROVED decisions: {len(matched)} "
          f"({unmatched} unmatched)")
    if matched and unmatched / len(trades) > 0.2:
        print("  !! join rate is low — attribution below may be unrepresentative.")
    live = sum(1 for r in matched if r.get("mode") == "typesafe-jev-system-one")
    print(f"  JEV engine mode on matched trades: {live}/{len(matched)} live typesafe")
    if any(r.get("signal_inverted") for r in matched):
        inv = sum(1 for r in matched if r.get("signal_inverted"))
        print(f"  (includes {inv} signal_inverted trades — direction was flipped)")

    # 1. Funnel
    print()
    print("  --- 1. DECISION FUNNEL ---")
    total = sum(outcomes.values())
    for o in ["APPROVED", "REJECTED", "SKIPPED"]:
        c = outcomes.get(o, 0)
        print(f"  {o:<10} {c:>7d}  ({c / total * 100 if total else 0:5.1f}%)")
    print("  top reasons:")
    for reason, c in reasons.most_common(8):
        print(f"    {reason:<32} {c:>6d}  ({c / total * 100 if total else 0:4.1f}%)")

    # 2. Per-setup attribution
    print()
    print("  --- 2. PER-SETUP ATTRIBUTION (matched trades only) ---")
    print(f"  {'setup':<22}{'n':>5}{'net':>11}{'exp/trd':>9}{'win%':>7}{'PF':>6}")
    by_setup = defaultdict(list)
    for r in matched:
        by_setup[r["setup"]].append(r)
    for setup in sorted(by_setup, key=lambda s: summarize(by_setup[s])["net"]):
        s = summarize(by_setup[setup])
        print(f"  {setup:<22}{s['n']:>5}{s['net']:>+11.2f}{s['exp']:>+9.2f}"
              f"{s['win%']:>6.1f}%{pf_str(s['pf'])}")

    # 3. Conviction calibration
    print()
    print("  --- 3. JEV CONVICTION vs OUTCOME ---")
    print(f"  {'conviction':<12}{'n':>5}{'net':>11}{'exp/trd':>9}{'win%':>7}")
    for lb, rows in bucket(matched, "conv", [3.4, 4.0]):
        s = summarize(rows)
        print(f"  {lb:<12}{s['n']:>5}{s['net']:>+11.2f}{s['exp']:>+9.2f}{s['win%']:>6.1f}%")

    # 4. Trap-risk calibration
    print()
    print("  --- 4. TRAP RISK vs OUTCOME ---")
    print(f"  {'trap_risk':<12}{'n':>5}{'net':>11}{'exp/trd':>9}{'win%':>7}")
    for lb, rows in bucket(matched, "trap", [0.15, 0.30]):
        s = summarize(rows)
        print(f"  {lb:<12}{s['n']:>5}{s['net']:>+11.2f}{s['exp']:>+9.2f}{s['win%']:>6.1f}%")

    # 5. Exit reasons
    print()
    print("  --- 5. EXIT REASON BREAKDOWN ---")
    print(f"  {'exit_reason':<24}{'n':>5}{'net':>11}{'exp/trd':>9}{'avg_dur':>8}")
    by_exit = defaultdict(list)
    for r in matched:
        by_exit[r["exit_reason"]].append(r)
    for ex in sorted(by_exit, key=lambda e: summarize(by_exit[e])["net"]):
        s = summarize(by_exit[ex])
        avg_dur = sum(r["duration_min"] for r in by_exit[ex]) / len(by_exit[ex])
        print(f"  {ex:<24}{s['n']:>5}{s['net']:>+11.2f}{s['exp']:>+9.2f}{avg_dur:>7.0f}m")

    # 6. Sides (log stores BUY/SELL or LONG/SHORT depending on engine version)
    print()
    print("  --- 6. SIDE ANALYSIS ---")
    for side in ["LONG", "SHORT"]:
        rows = [r for r in matched if norm_side(r["side"]) == side]
        if not rows:
            continue
        s = summarize(rows)
        print(f"  {side:<5} n={s['n']:>4}  win%={s['win%']:>5.1f}%  "
              f"exp={s['exp']:>+8.2f}/trade  net={s['net']:>+10.2f}")

    # 7. Symbol leaderboard
    print()
    print("  --- 7. SYMBOL LEADERBOARD ---")
    print(f"  {'symbol':<14}{'n':>5}{'net':>11}{'exp/trd':>9}{'win%':>7}")
    by_sym = defaultdict(list)
    for r in matched:
        by_sym[r["symbol"]].append(r)
    for sym in sorted(by_sym, key=lambda x: summarize(by_sym[x])["net"]):
        s = summarize(by_sym[sym])
        print(f"  {sym:<14}{s['n']:>5}{s['net']:>+11.2f}{s['exp']:>+9.2f}{s['win%']:>6.1f}%")

    # 8. Veto funnel
    print()
    print("  --- 8. JEV VETO FUNNEL ---")
    total_veto = sum(vetoes.values())
    print(f"  vetoes: {total_veto} | approvals: {n_approved}")
    if total_veto:
        print(f"  {'setup':<22}{'vetoed':>8}{'approved':>9}")
        for setup in sorted(set(list(vetoes) + list(appr_by_setup))):
            print(f"  {setup:<22}{vetoes.get(setup, 0):>8}{appr_by_setup.get(setup, 0):>9}")

    # 9. Summary
    s = summarize(matched)
    wins_pnl = sum(r["pnl"] for r in matched if r["pnl"] > 0)
    loss_pnl = sum(r["pnl"] for r in matched if r["pnl"] < 0)
    n_loss = sum(1 for r in matched if r["pnl"] < 0)
    print()
    print("  --- 9. SUMMARY ---")
    print(f"  matched trades: {s['n']} | wins {s['wins']} / losses {n_loss} | "
          f"win% {s['win%']:.1f}%")
    print(f"  avg win ${wins_pnl / s['wins']:+.2f} | avg loss ${loss_pnl / n_loss:+.2f} | "
          f"PF {pf_str(s['pf'])}")
    print(f"  net ${s['net']:+,.2f} | expectancy ${s['exp']:+.2f}/trade")

    print()
    print("  Read it like this:")
    print("  - A setup with deeply negative expectancy is the bleed: kill or fix it.")
    print("  - If high-conviction trades don't beat low-conviction ones, the JEV")
    print("    veto is expensive noise (~700ms latency per decision for no edge).")
    print("  - Vetoed trades never traded, so their outcomes are unknowable: the")
    print("    veto's 'value' is unproven in both directions. Only test whether")
    print("    APPROVED conviction discriminates.")
    print("  Caveat: the trade<->decision join is by (symbol, price, time); check")
    print("  the join rate at the top before trusting thin buckets.")


if __name__ == "__main__":
    main()
