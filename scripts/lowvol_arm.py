#!/usr/bin/env python3
"""Low-volatility long-only monthly PAPER arm (third arm, Sep-2026).

Strategy (locked from sprint #22 precommit — do not "tune" these):
  - Universe: 30 symbols (frozen 31 minus XAUUSDT, which has no Binance spot listing).
  - Signal: 63-trading-day trailing realized vol (sample stdev of daily log
    close-to-close returns), computed strictly from bars on/before the rebalance date.
  - Rebalance: monthly, at the previous month-end close. Hold the bottom tercile
    (floor(n_eligible/3)) by trailing vol, equal-weight, LONG ONLY, no leverage.
  - Sizing: $10,000 notional per position. Fee: $9 per round trip (charged on exit,
    exactly like the replay). No stops (diversification is the risk control). No JEV.
  - Start cash: $100,000 paper.

PAPER-ONLY BY CONSTRUCTION: this script uses ONLY Binance *public* klines endpoints
(no API keys are read, none are needed). It cannot place a real order — there is no
authenticated trading path anywhere in this file. The dashboard permanently displays
a PAPER badge.

Two modes:
  python scripts/lowvol_arm.py --rebalance   # monthly rebalance (scheduled task, daily;
                                             # executes only inside the first 7 days of a
                                             # month, at the previous month-end close)
  python scripts/lowvol_arm.py --serve        # dashboard on PORT (default 5002)

Ledger: runtime/lowvol_ledger.json (separate file — zero shared state with the
QuantArmNormal / QuantArmInverted sniper arms).
"""

import argparse
import html
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- locked params
UNIVERSE_31 = [
    "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "ATOMUSDT", "AVAXUSDT",
    "BNBUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETCUSDT", "ETHUSDT",
    "HBARUSDT", "ICPUSDT", "JUPUSDT", "LDOUSDT", "LINKUSDT", "LTCUSDT",
    "OPUSDT", "PAXGUSDT", "POLUSDT", "SEIUSDT", "SOLUSDT", "STXUSDT",
    "SUIUSDT", "TONUSDT", "TRXUSDT", "UNIUSDT", "VETUSDT", "XAUUSDT",
    "XRPUSDT",
]
# XAUUSDT has no Binance *spot* listing (futures-only). The arm trades spot
# (long-only monthly holds; the replay did not model funding, so perps would be
# unfaithful). Excluding it = 30 symbols. Documented deviation from the replay.
SPOT_EXCLUDE = {"XAUUSDT"}
UNIVERSE = [s for s in UNIVERSE_31 if s not in SPOT_EXCLUDE]
assert len(UNIVERSE) == 30, "universe must be 30 spot symbols"

VOL_LOOKBACK = 63            # trailing trading days for realized vol (literature standard)
NOTIONAL_PER_POSITION = 10_000.0
FEE_PER_TRIP = 9.0           # charged once, on exit — mirrors the replay exactly
START_CASH = 100_000.0
MIN_ELIGIBLE = 15            # below this, hold nothing (replay parity)
REBALANCE_WINDOW_DAYS = 7    # rebalance executes only within first 7 days of a month
MIN_FRESH_SYMBOLS = 28       # abort unless >=28/30 symbols have data through rebalance date
STALE_EXIT_DAYS = 5          # symbol with no bar for >5 days -> exit at last close (replay rule)
KLINES_LIMIT = 100           # > 64 bars needed (63 returns); buffer included

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "runtime")
LEDGER_PATH = os.path.join(RUNTIME_DIR, "lowvol_ledger.json")

BINANCE_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
HTTP_TIMEOUT = 20


# ---------------------------------------------------------------- pure helpers
def month_end(year: int, month: int) -> date:
    """Last calendar day of (year, month)."""
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def prev_month_end(today: date) -> date:
    """The month-end this run would rebalance at: last day of previous calendar month."""
    first_of_month = date(today.year, today.month, 1)
    return first_of_month - timedelta(days=1)


def trailing_vol(closes: list, lookback: int = VOL_LOOKBACK):
    """Sample stdev of log close-to-close returns over the trailing `lookback`
    returns ending at the LAST bar of `closes`.

    `closes`: ascending list of (date, close) floats. Returns None when fewer
    than lookback+1 bars are available or any price is non-positive.
    Formula is identical to the sprint-#22 replay harness (verified by parity test).
    NO-LOOKAHEAD: only bars with index <= the rebalance bar are ever passed in.
    """
    if len(closes) < lookback + 1:
        return None
    window = closes[-(lookback + 1):]
    rets = []
    for k in range(1, len(window)):
        p1, p0 = window[k][1], window[k - 1][1]
        if p0 <= 0 or p1 <= 0:
            return None
        rets.append(math.log(p1 / p0))
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def compute_target(vols: dict) -> list:
    """Bottom tercile by trailing vol. `vols`: {symbol: vol}. Returns sorted list.
    Mirrors the replay: n = floor(n_eligible/3); empty when eligible < MIN_ELIGIBLE."""
    if len(vols) < MIN_ELIGIBLE:
        return []
    n = len(vols) // 3
    return sorted(vols, key=lambda s: (vols[s], s))[:n]


def new_ledger() -> dict:
    return {
        "arm": "lowvol",
        "mode": "PAPER",
        "strategy": "lowvol-longonly-monthly",
        "strategy_params": {
            "universe": UNIVERSE,
            "vol_lookback": VOL_LOOKBACK,
            "notional_per_position": NOTIONAL_PER_POSITION,
            "fee_per_trip": FEE_PER_TRIP,
            "start_cash": START_CASH,
            "tercile": "bottom",
            "rebalance": "monthly@prev-month-end-close",
        },
        "cash": START_CASH,
        "positions": {},          # symbol -> {qty, entry_px, entry_date}
        "closed_trades": [],      # {symbol, entry_date, entry_px, exit_date, exit_px, qty, gross_pnl, fee, net_pnl}
        "rebalances": [],         # {month, at_utc, rebalance_date, eligible, target, sold, bought}
        "equity_marks": [],       # {date, equity}
        "last_rebalance": None,   # "YYYY-MM" of the last executed rebalance month-end
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def run_rebalance(ledger: dict, closes_by_symbol: dict, rebalance_date: date):
    """Execute one monthly rebalance. PURE LOGIC — no network, no disk.

    `closes_by_symbol`: {symbol: [(date, close), ...] ascending}; every symbol's
    last bar MUST be <= rebalance_date (callers guarantee this; bars after the
    rebalance date would be lookahead and are never passed in).
    Returns (ledger, actions). Mutates and returns the ledger dict.
    """
    month_label = rebalance_date.strftime("%Y-%m")

    # 1. trailing vols from bars on/before the rebalance date only.
    # Eligibility mirrors the replay exactly: the symbol must have a bar dated
    # precisely on the rebalance date (its IDX entry) AND >=64 bars for the vol.
    # A symbol whose data ended earlier is not sortable — the caller exits such
    # stale positions via the delisted path before reaching here.
    vols = {}
    for s in sorted(closes_by_symbol):
        bars = [b for b in closes_by_symbol[s] if b[0] <= rebalance_date]
        if not bars or bars[-1][0] != rebalance_date:
            continue
        v = trailing_vol(bars)
        if v is not None:
            vols[s] = v
    target = set(compute_target(vols))

    # close map at the rebalance date: EXACT bar date only (replay parity).
    # cmd_rebalance guarantees exact freshness for live runs; stale symbols are
    # exited before this function is reached. Never fall back to an older bar —
    # trading at a stale price would be a silent fill bug.
    bars_by_date = {}
    for s, bars in closes_by_symbol.items():
        bars_by_date[s] = {b[0]: b[1] for b in bars if b[0] <= rebalance_date}
    close_at = {}
    for s, m in bars_by_date.items():
        if rebalance_date in m:
            close_at[s] = (rebalance_date, m[rebalance_date])

    actions = {"sold": [], "bought": [], "skipped_no_cash": []}

    # 2. exits: held but not in target -> sell at rebalance close, fee on exit
    for s in sorted(list(ledger["positions"])):
        pos = ledger["positions"][s]
        if s in target:
            continue
        if s not in close_at:
            # no price at all — cannot exit cleanly; hold and flag (should not happen;
            # the caller exits >5d-stale symbols before reaching here)
            actions["sold"].append({"symbol": s, "error": "no_price_hold"})
            continue
        exit_date, exit_px = close_at[s]
        qty = pos["qty"]
        gross = qty * (exit_px - pos["entry_px"])
        net = gross - FEE_PER_TRIP
        ledger["cash"] += qty * exit_px - FEE_PER_TRIP
        ledger["closed_trades"].append({
            "symbol": s,
            "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
            "exit_date": exit_date.isoformat(), "exit_px": exit_px,
            "qty": qty, "gross_pnl": gross, "fee": FEE_PER_TRIP, "net_pnl": net,
        })
        del ledger["positions"][s]
        actions["sold"].append({"symbol": s, "exit_px": exit_px, "net_pnl": net})

    # 3. entries: in target but not held -> buy NOTIONAL at rebalance close.
    # Ordered by ASCENDING vol (lowest-vol first): if shared cash runs out, the
    # skipped symbols are the highest-vol of the tercile — the most
    # strategy-consistent choice. (The replay modeled independent $10k notionals
    # with no shared cash; a live portfolio cannot spend cash it lacks. This is
    # the one documented deviation: per-trip economics are unchanged, but the
    # arm may occasionally hold 9 instead of 10.)
    for s in sorted(target - set(ledger["positions"]), key=lambda x: (vols[x], x)):
        if s not in close_at:
            continue
        entry_date, entry_px = close_at[s]
        if entry_px <= 0:
            continue
        if ledger["cash"] < NOTIONAL_PER_POSITION:
            actions["skipped_no_cash"].append(s)
            continue
        qty = NOTIONAL_PER_POSITION / entry_px
        ledger["cash"] -= NOTIONAL_PER_POSITION
        ledger["positions"][s] = {
            "qty": qty, "entry_px": entry_px, "entry_date": entry_date.isoformat(),
        }
        actions["bought"].append({"symbol": s, "entry_px": entry_px, "qty": qty})

    # 4. mark equity at rebalance closes
    equity = ledger["cash"]
    for s, pos in ledger["positions"].items():
        if s in close_at:
            equity += pos["qty"] * close_at[s][1]
    ledger["equity_marks"].append({"date": rebalance_date.isoformat(), "equity": equity})

    ledger["rebalances"].append({
        "month": month_label,
        "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rebalance_date": rebalance_date.isoformat(),
        "eligible": len(vols),
        "target": sorted(target),
        "sold": actions["sold"],
        "bought": actions["bought"],
    })
    ledger["last_rebalance"] = month_label
    return ledger, actions

# ---------------------------------------------------------------- network (public only)
def fetch_daily_closes(symbol: str, end_date: date, limit: int = KLINES_LIMIT):
    """Daily spot klines for `symbol` ending at `end_date` (inclusive).

    Returns ascending [(date, close)] or raises. PUBLIC endpoints only — no keys.
    Tries multiple Binance hosts; the caller decides what a failure means.
    """
    end_ms = int(datetime(end_date.year, end_date.month, end_date.day,
                          23, 59, 59, 999000, tzinfo=timezone.utc).timestamp() * 1000)
    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": "1d", "limit": limit, "endTime": end_ms,
    })
    last_err = None
    for host in BINANCE_HOSTS:
        url = f"{host}/api/v3/klines?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lowvol-arm/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            bars = []
            for k in raw:
                # [openTime, open, high, low, close, volume, closeTime, ...]
                d = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date()
                bars.append((d, float(k[4])))
            bars.sort()
            # guard: never hand forward-dated bars to the strategy (lookahead tripwire)
            if bars and bars[-1][0] > end_date:
                raise RuntimeError(f"{symbol}: bar {bars[-1][0]} after end_date {end_date}")
            return bars
        except Exception as e:  # noqa: BLE001 - try next host
            last_err = e
    raise RuntimeError(f"{symbol}: all klines hosts failed ({last_err})")


# ---------------------------------------------------------------- ledger IO
def load_ledger() -> dict:
    if not os.path.exists(LEDGER_PATH):
        return new_ledger()
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("arm") != "lowvol":
        raise RuntimeError("lowvol ledger corrupt or foreign — refusing to touch it")
    return data


def save_ledger(ledger: dict) -> None:
    """Atomic write (tmp + rename) so a crash can never leave a half-written ledger."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    os.replace(tmp, LEDGER_PATH)


# ---------------------------------------------------------------- --rebalance
def cmd_rebalance(today: date | None = None) -> int:
    """Monthly rebalance entry point. Returns process exit code.

    Rules:
      * Rebalance date M = last day of the previous calendar month.
      * Skip (exit 0) when ledger.last_rebalance == M's YYYY-MM (single-fire).
      * Skip (exit 0) when today is past the first REBALANCE_WINDOW_DAYS of the
        month (missed window — wait for next month; never rebalance mid-month).
      * Abort (exit 1, ledger untouched) unless >= MIN_FRESH_SYMBOLS have their
        latest bar exactly on M. Symbols stale > STALE_EXIT_DAYS are exited at
        their last close instead (replay rule); the daily schedule retries aborts.
    """
    now = datetime.now(timezone.utc)
    today = today or now.date()
    m_end = prev_month_end(today)
    month_label = m_end.strftime("%Y-%m")

    ledger = load_ledger()
    if ledger.get("last_rebalance") == month_label:
        print(f"[lowvol] already rebalanced for {month_label} — single-fire skip")
        return 0
    if today.day > REBALANCE_WINDOW_DAYS:
        print(f"[lowvol] outside rebalance window (day {today.day}) — waiting for next month")
        return 0

    # fetch
    closes = {}
    fetch_errors = []
    for s in UNIVERSE:
        try:
            closes[s] = fetch_daily_closes(s, m_end)
        except Exception as e:  # noqa: BLE001 - collect and decide below
            fetch_errors.append((s, str(e)))
    if fetch_errors:
        print(f"[lowvol] ABORT: {len(fetch_errors)} fetch failures: "
              + ", ".join(f"{s} ({e})" for s, e in fetch_errors))
        return 1

    # freshness gate: every symbol's latest bar must be exactly the rebalance date,
    # except symbols whose data genuinely ended (>STALE_EXIT_DAYS ago -> delisted path)
    fresh, stale_exit, missing = {}, {}, []
    for s, bars in closes.items():
        last_d = bars[-1][0] if bars else None
        if last_d == m_end:
            fresh[s] = bars
        elif last_d is not None and (m_end - last_d).days > STALE_EXIT_DAYS:
            stale_exit[s] = bars
        else:
            missing.append(s)
    if missing:
        print(f"[lowvol] ABORT: {len(missing)} symbols without fresh data through "
              f"{m_end}: {missing} — ledger untouched, will retry tomorrow")
        return 1
    if len(fresh) < MIN_FRESH_SYMBOLS:
        print(f"[lowvol] ABORT: only {len(fresh)} fresh symbols (< {MIN_FRESH_SYMBOLS})")
        return 1

    # delisted path (replay rule): exit any held stale symbol at its last close
    for s in sorted(stale_exit):
        if s in ledger["positions"]:
            pos = ledger["positions"][s]
            exit_date, exit_px = stale_exit[s][-1]
            qty = pos["qty"]
            gross = qty * (exit_px - pos["entry_px"])
            net = gross - FEE_PER_TRIP
            ledger["cash"] += qty * exit_px - FEE_PER_TRIP
            ledger["closed_trades"].append({
                "symbol": s,
                "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                "exit_date": exit_date.isoformat(), "exit_px": exit_px,
                "qty": qty, "gross_pnl": gross, "fee": FEE_PER_TRIP,
                "net_pnl": net, "reason": "delisted/stale-data",
            })
            del ledger["positions"][s]
            print(f"[lowvol] stale-exit {s} at {exit_px} (last bar {exit_date})")

    ledger, actions = run_rebalance(ledger, fresh, m_end)
    save_ledger(ledger)
    n_b, n_s = len(actions["bought"]), len(actions["sold"])
    print(f"[lowvol] rebalanced {month_label} @ {m_end}: +{n_b} bought, -{n_s} sold, "
          f"eligible {len(fresh)}, cash {ledger['cash']:.2f}")
    return 0

# ---------------------------------------------------------------- live marks (dashboard only)
_MARKS_CACHE = {"at": 0.0, "marks": {}}
MARKS_TTL_SEC = 300


def _ticker_price(symbol: str):
    """Latest spot price via the public ticker endpoint (no keys)."""
    params = urllib.parse.urlencode({"symbol": symbol})
    last_err = None
    for host in BINANCE_HOSTS:
        try:
            req = urllib.request.Request(f"{host}/api/v3/ticker/price?{params}",
                                         headers={"User-Agent": "lowvol-arm/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return float(json.loads(resp.read().decode("utf-8"))["price"])
        except Exception as e:  # noqa: BLE001 - try next host
            last_err = e
    raise RuntimeError(f"{symbol}: ticker failed ({last_err})")


def live_marks(symbols: list) -> dict:
    """Best-effort current spot marks for dashboard display. Cached 5 min.
    Never raises; returns {} when the public API is unreachable (dashboard then
    falls back to entry prices with a stale note). Display-only — never used
    for fills."""
    now = time.time()
    if now - _MARKS_CACHE["at"] < MARKS_TTL_SEC and _MARKS_CACHE["marks"]:
        return _MARKS_CACHE["marks"]
    from concurrent.futures import ThreadPoolExecutor
    marks = {}
    with ThreadPoolExecutor(max_workers=min(10, len(symbols) or 1)) as ex:
        futs = {ex.submit(_ticker_price, s): s for s in symbols}
        for fut in futs:
            try:
                marks[futs[fut]] = fut.result(timeout=8)
            except Exception:
                pass
    if marks:
        _MARKS_CACHE.update(at=now, marks=marks)
    return marks or _MARKS_CACHE["marks"]


# ---------------------------------------------------------------- deploy id (permanent rule)
def resolve_deploy_info() -> dict:
    """Same contract as the sniper dashboards: git short SHA of the checkout this
    process runs from, plus full SHA / commit time / subject for the badge hover."""
    info = {"id": "unknown", "full": "unknown", "at": "unknown", "subject": ""}
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=PROJECT_ROOT,
                                  capture_output=True, text=True, timeout=10).stdout.strip()
        sha = git("rev-parse", "--short", "HEAD")
        if sha:
            info = {"id": sha, "full": git("rev-parse", "HEAD"),
                    "at": git("log", "-1", "--format=%ci") or "unknown",
                    "subject": git("log", "-1", "--format=%s")}
    except Exception:
        pass
    return info


DEPLOY_INFO = resolve_deploy_info()


# ---------------------------------------------------------------- --serve (dashboard)
def ledger_snapshot(ledger: dict, marks: dict) -> dict:
    equity = ledger["cash"]
    holdings = []
    for s in sorted(ledger["positions"]):
        pos = ledger["positions"][s]
        mark = marks.get(s)
        upnl = (mark - pos["entry_px"]) * pos["qty"] if mark else None
        if mark:
            equity += pos["qty"] * mark
        holdings.append({
            "symbol": s, "qty": pos["qty"], "entry_px": pos["entry_px"],
            "entry_date": pos["entry_date"], "mark": mark, "upnl": upnl,
        })
    return {"cash": ledger["cash"], "equity": equity, "holdings": holdings}


def render_dashboard(ledger: dict, snap: dict, live: bool) -> str:
    def money(x):
        return ("%.2f" % x) if x is not None else "—"

    dep = DEPLOY_INFO
    badge_title = f"full {dep['full']} · {dep['at']} · {html.escape(dep['subject'])}"
    closed = list(reversed(ledger.get("closed_trades", [])))
    total_net = sum(t["net_pnl"] for t in ledger.get("closed_trades", []))
    n_closed = len(closed)
    wins = sum(1 for t in ledger.get("closed_trades", []) if t["net_pnl"] > 0)

    # equity sparkline
    marks = ledger.get("equity_marks", [])
    svg = ""
    if len(marks) >= 2:
        w, h, pad = 560, 120, 8
        vals = [m["equity"] for m in marks]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        pts = []
        for i, v in enumerate(vals):
            x = pad + i * (w - 2 * pad) / (len(vals) - 1)
            y = h - pad - (v - lo) / span * (h - 2 * pad)
            pts.append(f"{x:.1f},{y:.1f}")
        svg = (f'<svg width="{w}" height="{h}" style="background:#0d1117;border-radius:8px">'
               f'<polyline points="{" ".join(pts)}" fill="none" stroke="#58a6ff" stroke-width="2"/>'
               f'<text x="{pad}" y="{pad + 10}" fill="#8b949e" font-size="10">'
               f'{money(hi)}</text>'
               f'<text x="{pad}" y="{h - pad}" fill="#8b949e" font-size="10">'
               f'{money(lo)}</text></svg>')

    hold_rows = "".join(
        f"<tr><td>{h['symbol']}</td><td>{h['qty']:.6f}</td>"
        f"<td>{money(h['entry_px'])}</td><td>{h['entry_date']}</td>"
        f"<td>{money(h['mark'])}</td>"
        f"<td style=\"color:{'#3fb950' if (h['upnl'] or 0) >= 0 else '#f85149'}\">"
        f"{money(h['upnl'])}</td></tr>"
        for h in snap["holdings"]
    ) or '<tr><td colspan="6">no open positions</td></tr>'

    trade_rows = "".join(
        f"<tr><td>{t['symbol']}</td><td>{t['entry_date']}</td><td>{money(t['entry_px'])}</td>"
        f"<td>{t['exit_date']}</td><td>{money(t['exit_px'])}</td>"
        f"<td style=\"color:{'#3fb950' if t['net_pnl'] >= 0 else '#f85149'}\">"
        f"{money(t['net_pnl'])}</td></tr>"
        for t in closed[:50]
    ) or '<tr><td colspan="6">no closed trades yet</td></tr>'

    reb_rows = "".join(
        f"<tr><td>{r['month']}</td><td>{r['rebalance_date']}</td><td>{r['eligible']}</td>"
        f"<td>{len(r['target'])}</td><td>{len(r['bought'])}</td><td>{len(r['sold'])}</td></tr>"
        for r in reversed(ledger.get("rebalances", []))
    ) or '<tr><td colspan="6">no rebalances yet</td></tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LOWVOL · low-volatility long-only (monthly) · PAPER</title>
<style>
body{{background:#010409;color:#e6edf3;font-family:system-ui,sans-serif;margin:0;padding:24px}}
header{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
.badge{{padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600}}
.paper{{background:#1a7f37;color:#fff}}
.deploy{{background:#21262d;color:#58a6ff;font-family:monospace;cursor:help}}
.stats{{display:flex;gap:24px;margin:20px 0;flex-wrap:wrap}}
.stat{{background:#0d1117;padding:12px 18px;border-radius:8px}}
.stat b{{font-size:20px;display:block}}
table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}}
th,td{{padding:8px 10px;border-bottom:1px solid #21262d;text-align:left}}
th{{color:#8b949e}}h2{{margin-top:28px}}
.note{{color:#8b949e;font-size:13px}}
</style></head><body>
<header>
<h1 style="margin:0">LOWVOL</h1>
<span class="badge paper">PAPER</span>
<span class="badge deploy" title="{badge_title}">Deploy {html.escape(dep['id'])}</span>
</header>
<p class="note">Low-volatility long-only · monthly rebalance at month-end close ·
bottom tercile of 63d trailing vol · $10k/position · $9/trip · no JEV ·
strategy locked by sprint #22 precommit. Forward paper — the true out-of-sample test.</p>
<div class="stats">
<div class="stat">Equity<b>${money(snap['equity'])}</b></div>
<div class="stat">Cash<b>${money(snap['cash'])}</b></div>
<div class="stat">Open positions<b>{len(snap['holdings'])}</b></div>
<div class="stat">Closed trips<b>{n_closed}</b> ({wins}W)</div>
<div class="stat">Realized net<b style="color:{'#3fb950' if total_net >= 0 else '#f85149'}">${money(total_net)}</b></div>
<div class="stat">Last rebalance<b>{html.escape(str(ledger.get('last_rebalance')))}</b></div>
</div>
<p class="note">marks: {"live spot (5-min cache)" if live else "entry prices — public price feed unreachable"}</p>
<h2>Equity</h2>{svg or '<p class="note">no marks yet</p>'}
<h2>Holdings</h2>
<table><tr><th>Symbol</th><th>Qty</th><th>Entry px</th><th>Entry date</th><th>Mark</th><th>uPnL $</th></tr>
{hold_rows}</table>
<h2>Closed trades (latest first)</h2>
<table><tr><th>Symbol</th><th>Entry</th><th>Entry px</th><th>Exit</th><th>Exit px</th><th>Net $</th></tr>
{trade_rows}</table>
<h2>Rebalance history</h2>
<table><tr><th>Month</th><th>Rebalance date</th><th>Eligible</th><th>Target</th><th>Bought</th><th>Sold</th></tr>
{reb_rows}</table>
<p class="note">server_time {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
</body></html>"""


class LowVolHandler(BaseHTTPRequestHandler):
    server_version = "LowVolArm/1.0"

    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            ledger = load_ledger()
        except Exception as e:  # noqa: BLE001 - dashboard must never crash on ledger
            ledger = None
            ledger_err = str(e)
        if path == "/api/status":
            body = {
                "arm": "lowvol",
                "mode": "PAPER",
                "strategy": "lowvol-longonly-monthly",
                "deploy": DEPLOY_INFO,
                "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "ledger": None if ledger is None else {
                    "cash": ledger["cash"],
                    "open_positions": len(ledger["positions"]),
                    "closed_trades": len(ledger["closed_trades"]),
                    "rebalances": len(ledger["rebalances"]),
                    "last_rebalance": ledger.get("last_rebalance"),
                    "realized_net": sum(t["net_pnl"] for t in ledger["closed_trades"]),
                },
                "error": None if ledger is None else None,
            }
            if ledger is None:
                body["error"] = f"ledger unreadable: {ledger_err}"
            self._send(200, json.dumps(body).encode(), "application/json")
            return
        if path in ("/", "/index.html"):
            if ledger is None:
                page = ("<h1>LOWVOL arm</h1><p>ledger unreadable: "
                        + html.escape(ledger_err) + "</p>")
            else:
                marks = live_marks(list(ledger["positions"].keys()))
                page = render_dashboard(ledger, ledger_snapshot(ledger, marks),
                                        live=bool(marks))
            self._send(200, page.encode(), "text/html; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")


def cmd_serve(port: int, host: str) -> int:
    srv = ThreadingHTTPServer((host, port), LowVolHandler)
    print(f"[lowvol] dashboard on http://{host}:{port} "
          f"(deploy {DEPLOY_INFO['id']}, PAPER-only)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LOWVOL monthly paper arm")
    ap.add_argument("--rebalance", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5002")))
    ap.add_argument("--host", default=os.environ.get("QV_BIND_HOST", "127.0.0.1"))
    ns = ap.parse_args(argv)
    if ns.rebalance and ns.serve:
        print("choose one of --rebalance / --serve", file=sys.stderr)
        return 2
    if ns.rebalance:
        return cmd_rebalance()
    if ns.serve:
        return cmd_serve(ns.port, ns.host)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
