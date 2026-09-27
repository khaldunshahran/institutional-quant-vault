"""Long-only filter (QV_LONG_ONLY): SELL signals are dropped after the
inversion flip, BUY signals pass through. Confirmed by H1/H3 replay rounds."""
import pytest
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader


def _make_trader(monkeypatch, tmp_path, invert="0", long_only="0"):
    monkeypatch.setenv("QV_INVERT_SIGNALS", invert)
    monkeypatch.setenv("QV_LONG_ONLY", long_only)
    t = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    logged = []
    t._log_decision = lambda s, o, r, d=None: logged.append((s, o, r, d))
    return t, logged


def _sig(side):
    return {"symbol": "BTCUSDT", "side": side, "price": 60000.0,
            "setup": "SNIPER_TREND_LONG", "signal_inverted": False}


def test_long_only_drops_sell_normal_arm(monkeypatch, tmp_path):
    t, logged = _make_trader(monkeypatch, tmp_path, invert="0", long_only="1")
    assert t.long_only is True
    assert t.invert_signals is False
    t._open_position(_sig("SELL"))
    assert "BTCUSDT" not in t.open_positions
    assert "BTCUSDT" not in t.pending_entries
    rej = [d for d in logged if d[1] == "REJECTED" and d[2] == "LONG_ONLY_FILTER"]
    assert len(rej) == 1
    assert rej[0][3]["side"] == "SELL"


def test_long_only_keeps_buy_normal_arm(monkeypatch, tmp_path):
    t, logged = _make_trader(monkeypatch, tmp_path, invert="0", long_only="1")
    # prevent a real order: make the fill simulator refuse immediately
    t.execution_adapter.fill_simulator.place_maker_order = lambda *a, **k: {"status": "REJECTED", "reason": "test"}
    t._open_position(_sig("BUY"))
    rej = [d for d in logged if d[2] == "LONG_ONLY_FILTER"]
    assert rej == []


def test_long_only_applies_after_inversion(monkeypatch, tmp_path):
    # Inverted arm: original BUY -> flipped SELL -> dropped by long-only;
    # original SELL -> flipped BUY -> kept.
    t, logged = _make_trader(monkeypatch, tmp_path, invert="1", long_only="1")
    assert t._signal_side("BUY") == "SELL"
    assert t._signal_side("SELL") == "BUY"
    t._open_position(_sig(t._signal_side("BUY")))   # flipped SELL -> dropped
    t._open_position(_sig(t._signal_side("SELL")))  # flipped BUY -> passes filter
    rej = [d for d in logged if d[2] == "LONG_ONLY_FILTER"]
    assert len(rej) == 1
    assert rej[0][3]["side"] == "SELL"
    assert rej[0][3]["signal_inverted"] is True


def test_long_only_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("QV_LONG_ONLY", raising=False)
    monkeypatch.delenv("QV_INVERT_SIGNALS", raising=False)
    t = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    assert t.long_only is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE"])
def test_long_only_flag_parsing(monkeypatch, tmp_path, val):
    t, _ = _make_trader(monkeypatch, tmp_path, long_only=val)
    assert t.long_only is True
