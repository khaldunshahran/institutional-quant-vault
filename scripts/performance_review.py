#!/usr/bin/env python3
"""
performance_review.py — CTO-style review of the paper trading engine.

Reads the engine's own books (no new data needed) and prints a plain-English
report: what is making money, what is losing it, and which experiments to try.

Usage:
    python scripts/performance_review.py
    python scripts/performance_review.py --runtime runtime --out reports

Paper-only. Read-only: never touches live trading, never modifies runtime/.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_history(runtime_dir):
    path = os.path.join(runtime_dir, "autonomous_trade_history.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_decisions(runtime_dir, max_lines=200000):
    path = os.path.join(runtime_dir, "decision_log.jsonl")
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def parse_ts(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def money(x):
    sign = "-" if x < 0 else ("+" if x > 0 else "")
    return f"{sign}${abs(x):,.2f}"


def pct(x):
    return f"{x:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.path.join(REPO_ROOT, "runtime"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "reports"))
    args = ap.parse_args()

    history = load_history(args.runtime)
    decisions = load_decisions(args.runtime)

    lines = []
    add = lines.append
    add("=" * 64)
    add("PAPER TRADING REVIEW  —  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    add("=" * 64)

    if not history:
        add("")
        add("No closed trades found. The engine hasn't finished any paper trades yet.")
        add("Run the engine for a while, then run this review again.")
        report = "\n".join(lines)
        print(report)
        return

    pnls = [float(t.get("pnl_usd") or 0) for t in history]
    n = len(pnls)
    net = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n * 100 if n else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else 0
    expectancy = net / n if n else 0
    avg_win = gross_win / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    # Max drawdown on cumulative curve (by close order)
    ordered = sorted(history, key=lambda t: str(t.get("closed_at") or ""))
    running, peak, max_dd = 0.0, 0.0, 0.0
    for t in ordered:
        running += float(t.get("pnl_usd") or 0)
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    # Streaks
    best_streak = worst_streak = cur = 0
    cur_sign = 0
    for p in pnls:
        s = 1 if p > 0 else (-1 if p < 0 else 0)
        if s == cur_sign and s != 0:
            cur += 1
        else:
            cur, cur_sign = (1 if s != 0 else 0), s
        if s > 0:
            best_streak = max(best_streak, cur)
        elif s < 0:
            worst_streak = max(worst_streak, cur)

    first_ts = parse_ts(ordered[0].get("closed_at"))
    last_ts = parse_ts(ordered[-1].get("closed_at"))
    span = ""
    if first_ts and last_ts:
        days = max((last_ts - first_ts).total_seconds() / 86400.0, 1 / 24)
        span = f" over {days:.1f} days ({n / days:.0f} trades/day)"

    add("")
    add(f"In one sentence: {n} paper trades{span}, net {money(net)}, "
        f"win rate {pct(win_rate)}, profit factor {pf:.2f}, expectancy {money(expectancy)}/trade.")
    if pf < 1.0:
        add("Verdict: no edge demonstrated yet. The engine loses money on average per trade.")
    elif pf < 1.5:
        add("Verdict: tiny edge at best — not enough to trust. Needs a bigger sample and lower costs.")
    else:
        add("Verdict: positive edge in this sample. Still paper-only until it survives much longer.")

    add("")
    add("-" * 64)
    add("OVERALL")
    add("-" * 64)
    add(f"  Trades: {n}   Wins: {len(wins)}   Losses: {len(losses)}")
    add(f"  Net PnL: {money(net)}   Avg win: {money(avg_win)}   Avg loss: {money(avg_loss)}")
    add(f"  Profit factor: {pf:.2f}   Expectancy: {money(expectancy)}/trade")
    add(f"  Max drawdown: ${max_dd:,.2f}")
    add(f"  Best win streak: {best_streak}   Worst loss streak: {worst_streak}")

    def table(title, rows, headers):
        add("")
        add("-" * 64)
        add(title)
        add("-" * 64)
        widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
        fmt = "  ".join("{:<%d}" % w for w in widths)
        add("  " + fmt.format(*headers))
        for r in rows:
            add("  " + fmt.format(*[str(x) for x in r]))

    # By symbol
    by_sym = defaultdict(lambda: [0, 0.0, 0])
    for t in history:
        s = str(t.get("symbol") or "?")
        p = float(t.get("pnl_usd") or 0)
        by_sym[s][0] += 1
        by_sym[s][1] += p
        if p > 0:
            by_sym[s][2] += 1
    sym_rows = sorted(
        ((s, v[0], money(v[1]), pct(v[2] / v[0] * 100 if v[0] else 0)) for s, v in by_sym.items()),
        key=lambda r: float(r[2].replace("$", "").replace(",", "").replace("+", "")) if r[2] not in ("$0.00",) else 0,
    )
    table("BY SYMBOL (worst first)", sym_rows, ["Symbol", "Trades", "Net", "Win%"])

    # By side
    by_side = defaultdict(lambda: [0, 0.0, 0])
    for t in history:
        s = str(t.get("side") or "?")
        p = float(t.get("pnl_usd") or 0)
        by_side[s][0] += 1
        by_side[s][1] += p
        if p > 0:
            by_side[s][2] += 1
    side_rows = [(s, v[0], money(v[1]), pct(v[2] / v[0] * 100 if v[0] else 0)) for s, v in sorted(by_side.items())]
    table("BY SIDE", side_rows, ["Side", "Trades", "Net", "Win%"])

    # By exit reason
    by_exit = defaultdict(lambda: [0, 0.0])
    for t in history:
        e = str(t.get("exit_reason") or "?")
        by_exit[e][0] += 1
        by_exit[e][1] += float(t.get("pnl_usd") or 0)
    exit_rows = sorted(
        ((e, v[0], money(v[1]), money(v[1] / v[0] if v[0] else 0)) for e, v in by_exit.items()),
        key=lambda r: v_sort_key(r[2]),
    )
    table("BY EXIT REASON (worst first)", exit_rows, ["Exit reason", "Trades", "Net", "Avg/trade"])

    # By hour of close (UTC)
    by_hour = defaultdict(lambda: [0, 0.0])
    for t in history:
        ts = parse_ts(t.get("closed_at"))
        if ts:
            by_hour[ts.hour][0] += 1
            by_hour[ts.hour][1] += float(t.get("pnl_usd") or 0)
    hour_rows = [(f"{h:02d}:00 UTC", v[0], money(v[1])) for h, v in sorted(by_hour.items())]
    table("BY HOUR OF CLOSE (UTC)", hour_rows, ["Hour", "Trades", "Net"])

    # Duration: winners vs losers
    dur_w = [float(t.get("duration_sec") or 0) for t in history if float(t.get("pnl_usd") or 0) > 0]
    dur_l = [float(t.get("duration_sec") or 0) for t in history if float(t.get("pnl_usd") or 0) < 0]
    add("")
    add("-" * 64)
    add("HOLD TIME")
    add("-" * 64)
    if dur_w:
        add(f"  Winners held avg: {sum(dur_w) / len(dur_w) / 60:.0f} min")
    if dur_l:
        add(f"  Losers held avg:  {sum(dur_l) / len(dur_l) / 60:.0f} min")
    if dur_w and dur_l and sum(dur_l) / len(dur_l) > sum(dur_w) / len(dur_w) * 1.5:
        add("  Note: losers are held much longer than winners — classic 'hope' pattern.")

    # Decision log summary
    if decisions:
        outcomes = Counter(str(d.get("outcome") or "?") for d in decisions)
        reasons = Counter(str(d.get("reason") or "?") for d in decisions if d.get("outcome") == "SKIPPED")
        approved = sum(1 for d in decisions if d.get("outcome") == "APPROVED")
        add("")
        add("-" * 64)
        add(f"DECISION LOG (last {len(decisions)} entries)")
        add("-" * 64)
        add(f"  Approved entries: {approved} / {len(decisions)} ({approved / len(decisions) * 100:.1f}%)")
        for reason, cnt in reasons.most_common(5):
            add(f"  Skipped ({reason}): {cnt}")

    # Auto-insights
    add("")
    add("=" * 64)
    add("WHAT I NOTICE (auto-generated — verify before acting)")
    add("=" * 64)
    notes = []
    if abs(net) > 0:
        worst_sym = min(by_sym.items(), key=lambda kv: kv[1][1])
        share = abs(worst_sym[1][1] / net * 100) if net else 0
        if share > 15:
            notes.append(f"{worst_sym[0]} alone is responsible for {share:.0f}% of the net result "
                         f"({money(worst_sym[1][1])} over {worst_sym[1][0]} trades). "
                         f"Consider excluding it from the universe as an experiment.")
    if by_side:
        sides = sorted(by_side.items(), key=lambda kv: kv[1][1])
        if len(sides) > 1 and sides[0][1][1] < 0 < sides[-1][1][1]:
            notes.append(f"{sides[0][0]} trades lose ({money(sides[0][1][1])}) while "
                         f"{sides[-1][0]} trades make ({money(sides[-1][1][1])}). "
                         f"Experiment: paper-trade only the winning side for a week.")
    if by_exit:
        worst_exit = min(by_exit.items(), key=lambda kv: kv[1][1])
        if worst_exit[1][1] < 0 and worst_exit[1][0] >= max(5, n * 0.05):
            notes.append(f"Exit reason '{worst_exit[0]}' produced {money(worst_exit[1][1])} "
                         f"across {worst_exit[1][0]} trades. That exit rule deserves its own review.")
    if avg_loss and avg_win and abs(avg_loss) > avg_win * 1.5:
        notes.append(f"Average loss ({money(avg_loss)}) is much bigger than average win "
                     f"({money(avg_win)}). The math can't work long-term unless wins get bigger "
                     f"or losses get cut faster.")
    if worst_streak >= 8:
        notes.append(f"Worst losing streak was {worst_streak} trades. Make sure position sizing "
                     f"could survive that happening twice in a row.")
    if not notes:
        notes.append("Nothing jumps out mechanically — the sample may just be too small or too mixed. "
                     "Give it more trades, then re-run.")
    for i, note in enumerate(notes, 1):
        add("")
        add(f"  {i}. " + note)

    add("")
    add("=" * 64)
    add("Reminder: paper only. No live trading. A losing paper strategy must")
    add("never go live — fix the edge first, then keep it on paper longer.")
    add("=" * 64)

    report = "\n".join(lines)
    print(report)

    os.makedirs(args.out, exist_ok=True)
    fname = datetime.now(timezone.utc).strftime("performance_review_%Y%m%d_%H%M.md")
    out_path = os.path.join(args.out, fname)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nSaved to {out_path}")
    except Exception as e:
        print(f"\nCould not save report: {e}", file=sys.stderr)


def v_sort_key(money_str):
    try:
        return float(money_str.replace("$", "").replace(",", "").replace("+", ""))
    except Exception:
        return 0.0


if __name__ == "__main__":
    main()
