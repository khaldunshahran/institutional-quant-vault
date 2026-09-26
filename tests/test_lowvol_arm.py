"""Tests for the LOWVOL monthly paper arm (scripts/lowvol_arm.py).

The load-bearing test is test_replay_parity: the arm's rebalance engine must
reproduce the sprint-#22 replay's OOS numbers exactly (same data, same rules).
"""
import json
import math
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.lowvol_arm as lv

HIDDEN = os.path.expanduser(
    "~/workspace/goals/institutional-quant-vault-review-and-remediation/hidden_files")
DAILY_CACHE = os.path.join(HIDDEN, "research/bab/daily_cache.json")
RESULTS_JSON = os.path.join(HIDDEN, "research/lowvol/lowvol_results.json")


def synth_bars(start: date, closes: list[float]):
    return [(start.fromordinal(start.toordinal() + i), c) for i, c in enumerate(closes)]


# ---------------------------------------------------------------- unit
def test_month_end():
    assert lv.month_end(2024, 2) == date(2024, 2, 29)   # leap
    assert lv.month_end(2023, 2) == date(2023, 2, 28)
    assert lv.month_end(2026, 12) == date(2026, 12, 31)
    assert lv.month_end(2026, 1) == date(2026, 1, 31)


def test_prev_month_end():
    assert lv.prev_month_end(date(2026, 10, 1)) == date(2026, 9, 30)
    assert lv.prev_month_end(date(2026, 10, 7)) == date(2026, 9, 30)
    assert lv.prev_month_end(date(2026, 1, 5)) == date(2025, 12, 31)
    assert lv.prev_month_end(date(2024, 3, 1)) == date(2024, 2, 29)


def test_universe_is_30_spot():
    assert len(lv.UNIVERSE) == 30
    assert "XAUUSDT" not in lv.UNIVERSE
    assert "PAXGUSDT" in lv.UNIVERSE
    assert all(s.endswith("USDT") for s in lv.UNIVERSE)


def test_trailing_vol_formula():
    # flat series -> ~0 vol; known wiggle -> positive
    bars = synth_bars(date(2024, 1, 1), [100.0] * 70)
    assert lv.trailing_vol(bars) == pytest.approx(0.0, abs=1e-12)
    bars = synth_bars(date(2024, 1, 1), [100.0 * (1.01 ** i) for i in range(70)])
    v = lv.trailing_vol(bars)
    assert v == pytest.approx(0.0, abs=1e-9)  # constant drift -> zero stdev
    bars = synth_bars(date(2024, 1, 1),
                      [100.0 * (1.05 if i % 2 else 0.95) for i in range(70)])
    assert lv.trailing_vol(bars) > 0.05
    # insufficient history
    assert lv.trailing_vol(synth_bars(date(2024, 1, 1), [100.0] * 63)) is None
    assert lv.trailing_vol(synth_bars(date(2024, 1, 1), [100.0] * 64)) is not None


def test_compute_target_tercile():
    vols = {f"S{i:02d}": float(i) for i in range(30)}
    t = lv.compute_target(vols)
    assert t == [f"S{i:02d}" for i in range(10)]  # 10 lowest of 30
    assert lv.compute_target({f"S{i}": float(i) for i in range(14)}) == []  # < 15


def test_no_lookahead_truncation():
    """Feeding bars beyond the rebalance date must not change the outcome."""
    start = date(2024, 1, 1)
    # AAA: calm for 70 days, then explodes. 15 others: always choppy.
    # (>=64 bars so vols are defined; >=15 eligible so the tercile is non-empty.)
    a = [100 + 0.1 * i for i in range(70)] + [100 + 5 * i for i in range(30)]
    full = {"AAAUSDT": synth_bars(start, a)}
    for i in range(15):
        b = [100 * (1.03 if j % 2 else 0.97) for j in range(100)]
        full[f"S{i:02d}USDT"] = synth_bars(start, b)
    m_end = date.fromordinal(start.toordinal() + 69)
    led_full, _ = lv.run_rebalance(lv.new_ledger(), full, m_end)
    trunc = {s: [x for x in bars if x[0] <= m_end] for s, bars in full.items()}
    led_trunc, _ = lv.run_rebalance(lv.new_ledger(), trunc, m_end)
    assert led_full["positions"].keys() == led_trunc["positions"].keys()
    # sanity: with only Jan data visible, calm AAA must be the low-vol pick
    assert "AAAUSDT" in led_trunc["positions"]
    assert len(led_trunc["positions"]) == 16 // 3  # bottom tercile of 16


def test_multimonth_hold_is_one_trip():
    """A symbol staying in the tercile across rebalances = one continuous trip."""
    start = date(2023, 10, 1)
    calm = [100.0] * 300
    chop = [100 * (1.04 if i % 2 else 0.96) for i in range(300)]
    closes = {"AAAUSDT": synth_bars(start, calm)}
    for i in range(29):
        closes[f"S{i:02d}USDT"] = synth_bars(start, chop)
    led = lv.new_ledger()
    for m_end in [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)]:
        led, _ = lv.run_rebalance(led, closes, m_end)
        assert "AAAUSDT" in led["positions"]
    assert led["closed_trades"] == []  # held continuously, no trip closed
    assert led["positions"]["AAAUSDT"]["entry_date"] == "2024-01-31"


def test_exit_applies_single_fee():
    start = date(2023, 10, 1)
    calm = [100.0] * 300
    chop = [100 * (1.04 if i % 2 else 0.96) for i in range(300)]
    closes = {"AAAUSDT": synth_bars(start, calm)}
    for i in range(29):
        closes[f"S{i:02d}USDT"] = synth_bars(start, chop)
    led = lv.new_ledger()
    led, _ = lv.run_rebalance(led, closes, date(2024, 1, 31))
    assert "AAAUSDT" in led["positions"]
    entry_px = led["positions"]["AAAUSDT"]["entry_px"]
    # now make AAA the HIGHEST vol symbol so it exits next month
    wild = [100.0] * 123 + [100 * (1.5 if i % 2 else 0.5) for i in range(177)]
    closes["AAAUSDT"] = synth_bars(start, wild)
    led, actions = lv.run_rebalance(led, closes, date(2024, 2, 29))
    assert "AAAUSDT" not in led["positions"]
    assert len(led["closed_trades"]) == 1
    t = led["closed_trades"][0]
    assert t["fee"] == pytest.approx(9.0)
    qty = 10000.0 / entry_px
    assert t["net_pnl"] == pytest.approx(qty * (t["exit_px"] - entry_px) - 9.0)


# ---------------------------------------------------------------- parity with the replay
def _load_cache():
    D = json.load(open(DAILY_CACHE))
    out = {}
    for s, m in D.items():
        bars = sorted((date.fromisoformat(d), m[d][3]) for d in m)
        out[s] = bars
    return out


@pytest.mark.skipif(not os.path.exists(DAILY_CACHE), reason="research data not present")
def test_trailing_vol_matches_replay_formula():
    """Arm's trailing_vol == replay's trailing_vol on real data (all symbols/dates)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lowvol_replay",
        os.path.join(HIDDEN, "research/lowvol/lowvol_replay.py"))
    # the replay module runs on import; replicate its function inline instead
    D = json.load(open(DAILY_CACHE))
    syms = sorted(D.keys())
    PX = {s: sorted((date.fromisoformat(x), D[s][x][3]) for x in D[s]) for s in syms}
    IDX = {s: {d: i for i, (d, _) in enumerate(PX[s])} for s in syms}

    def replay_vol(s, d, lookback=63):
        i = IDX[s].get(d)
        if i is None or i < lookback:
            return None
        px = PX[s]
        rets = []
        for k in range(i - lookback + 1, i + 1):
            p1, p0 = px[k][1], px[k - 1][1]
            if p0 <= 0 or p1 <= 0:
                return None
            rets.append(math.log(p1 / p0))
        m = sum(rets) / len(rets)
        return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

    closes = _load_cache()
    checked = 0
    for s in syms:
        s_dates = {b[0] for b in closes[s]}
        for d in [date(2024, 1, 31), date(2024, 10, 31), date(2025, 6, 30), date(2026, 8, 31)]:
            if d not in s_dates:
                continue  # replay requires the date in the symbol's index; arm too
            bars = [b for b in closes[s] if b[0] <= d]
            a, b_ = replay_vol(s, d), lv.trailing_vol(bars)
            assert (a is None) == (b_ is None), (s, d)
            if a is not None:
                assert a == pytest.approx(b_, rel=1e-12), (s, d)
                checked += 1
    assert checked > 100


@pytest.mark.skipif(not os.path.exists(DAILY_CACHE), reason="research data not present")
def test_replay_parity_oos():
    """Drive run_rebalance over the replay's own data (all 31 symbols, incl.
    XAUUSDT) for 2024-01→2026-08 + final exit 2026-09-26. Trips and total net
    must match lowvol_results.json OOS within $1."""
    closes = _load_cache()
    syms31 = sorted(closes.keys())
    assert len(syms31) == 31

    def month_ends(y1, m1, y2, m2):
        out, y, m = [], y1, m1
        while (y, m) <= (y2, m2):
            me = lv.month_end(y, m)
            out.append(me)
            m += 1
            if m == 13:
                m, y = 1, y + 1
        return out

    led = lv.new_ledger()
    # NOTE: the replay modeled each position as an independent $10k notional with
    # no shared cash. The live arm cannot spend cash it lacks, so run_rebalance
    # enforces a shared-cash constraint (documented deviation). For engine parity
    # we fund the test ledger with effectively infinite cash, isolating the
    # signal/execution logic from the portfolio constraint (which is unit-tested
    # separately in test_cash_constraint_skips_entries).
    led["cash"] = 1_000_000_000.0
    for me in month_ends(2024, 1, 2026, 8):
        # stale-data rule identical to the replay: exit held symbols whose data
        # ended >5 days ago at their last close
        for s in sorted(list(led["positions"])):
            last_d = closes[s][-1][0]
            if last_d < me and (me - last_d).days > 5:
                pos = led["positions"][s]
                _, exit_px = closes[s][-1]
                qty = pos["qty"]
                led["cash"] += qty * exit_px - lv.FEE_PER_TRIP
                led["closed_trades"].append({
                    "symbol": s, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                    "exit_date": last_d.isoformat(), "exit_px": exit_px, "qty": qty,
                    "gross_pnl": qty * (exit_px - pos["entry_px"]),
                    "fee": lv.FEE_PER_TRIP,
                    "net_pnl": qty * (exit_px - pos["entry_px"]) - lv.FEE_PER_TRIP,
                })
                del led["positions"][s]
        led, _ = lv.run_rebalance(led, closes, me)
    # final exit at 2026-09-26 (replay's final_exit)
    fexit = date(2026, 9, 26)
    for s in sorted(list(led["positions"])):
        pos = led["positions"][s]
        prior = [b for b in closes[s] if b[0] <= fexit]
        exit_date, exit_px = prior[-1]
        qty = pos["qty"]
        led["cash"] += qty * exit_px - lv.FEE_PER_TRIP
        led["closed_trades"].append({
            "symbol": s, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
            "exit_date": exit_date.isoformat(), "exit_px": exit_px, "qty": qty,
            "gross_pnl": qty * (exit_px - pos["entry_px"]), "fee": lv.FEE_PER_TRIP,
            "net_pnl": qty * (exit_px - pos["entry_px"]) - lv.FEE_PER_TRIP,
        })
        del led["positions"][s]

    expected = json.load(open(RESULTS_JSON))["OOS"]["lowvol"]
    assert len(led["closed_trades"]) == expected["trips"], (
        f"trips {len(led['closed_trades'])} != replay {expected['trips']}")
    total = sum(t["net_pnl"] for t in led["closed_trades"])
    assert total == pytest.approx(expected["total_net"], abs=1.0), (
        f"net {total} != replay {expected['total_net']}")


def test_cash_constraint_skips_entries():
    """Shared-cash realism: with $5k cash, new entries are skipped (logged), not
    force-bought. Lowest-vol symbols get priority."""
    start = date(2023, 10, 1)
    closes = {}
    for i in range(30):
        # increasing vol with i: S00 calmest
        series = [100.0 + (0.01 * i if j % 2 == 0 else -0.01 * i) for j in range(300)]
        closes[f"S{i:02d}USDT"] = synth_bars(start, series)
    led = lv.new_ledger()
    led["cash"] = 5_000.0  # less than one $10k ticket
    led, actions = lv.run_rebalance(led, closes, date(2024, 1, 31))
    assert led["positions"] == {}
    assert len(actions["skipped_no_cash"]) == 10  # all 10 targets skipped
    assert led["cash"] == pytest.approx(5_000.0)
    # with exactly $10k, the single calmest symbol is bought
    led["cash"] = 10_000.0
    led, actions = lv.run_rebalance(led, closes, date(2024, 2, 29))
    assert list(led["positions"].keys()) == ["S00USDT"]
    assert actions["skipped_no_cash"] != []


# ---------------------------------------------------------------- cmd_rebalance behavior (mocked network)
def _mock_bars(monkeypatch, tmp_path, symbols, m_end, last_bar_date=None):
    bars = {}
    start = date(2024, 1, 1)
    n = (m_end - start).days + 1
    for i, s in enumerate(symbols):
        series = [100.0 + 0.05 * i + (0.5 if (j % 2) else -0.5) * (i % 5) for j in range(n)]
        last = last_bar_date or m_end
        bars[s] = [(start.fromordinal(start.toordinal() + j), series[j])
                   for j in range((last - start).days + 1)]
    monkeypatch.setattr(lv, "fetch_daily_closes",
                        lambda symbol, end_date, limit=100: bars[symbol])
    monkeypatch.setattr(lv, "LEDGER_PATH", str(tmp_path / "lowvol_ledger.json"))
    monkeypatch.setattr(lv, "RUNTIME_DIR", str(tmp_path))
    return bars


def test_cmd_rebalance_single_fire(tmp_path, monkeypatch):
    m_end = date(2026, 9, 30)
    _mock_bars(monkeypatch, tmp_path, lv.UNIVERSE, m_end)
    assert lv.cmd_rebalance(today=date(2026, 10, 2)) == 0
    led = lv.load_ledger()
    n_trades_1 = len(led["closed_trades"])
    n_pos_1 = len(led["positions"])
    assert led["last_rebalance"] == "2026-09"
    # second run same window -> no-op
    assert lv.cmd_rebalance(today=date(2026, 10, 3)) == 0
    led = lv.load_ledger()
    assert len(led["closed_trades"]) == n_trades_1
    assert len(led["positions"]) == n_pos_1
    assert led["last_rebalance"] == "2026-09"


def test_cmd_rebalance_outside_window(tmp_path, monkeypatch):
    m_end = date(2026, 9, 30)
    _mock_bars(monkeypatch, tmp_path, lv.UNIVERSE, m_end)
    assert lv.cmd_rebalance(today=date(2026, 10, 20)) == 0  # day 20 > 7
    led = lv.load_ledger()
    assert led["last_rebalance"] is None
    assert led["positions"] == {}


def test_cmd_rebalance_aborts_on_stale_data(tmp_path, monkeypatch):
    m_end = date(2026, 9, 30)
    bars = _mock_bars(monkeypatch, tmp_path, lv.UNIVERSE, m_end)
    # one symbol 2 days stale -> abort, ledger untouched
    syms = lv.UNIVERSE
    stale_sym = syms[0]
    bars[stale_sym] = bars[stale_sym][:-2]
    monkeypatch.setattr(lv, "fetch_daily_closes",
                        lambda symbol, end_date, limit=100: bars[symbol])
    assert lv.cmd_rebalance(today=date(2026, 10, 2)) == 1
    led = lv.load_ledger()
    assert led["last_rebalance"] is None
    assert led["positions"] == {}
    assert led["closed_trades"] == []
