"""Tests for the UNCONDITIONAL paper-only lock.

Live trading is permanently disabled in this build. There is no
confirmation flag, no environment variable, and no credential combination
that enables it: construction, set_mode(), trader start, and the order
boundary all refuse live execution, and no HTTP request to a private
order endpoint can be produced.
"""
import urllib.request
from unittest.mock import MagicMock

import pytest

from scripts.binance_execution_adapter import BinanceExecutionAdapter
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from test_risk_and_correlation import StubFillSimulator


@pytest.fixture(autouse=True)
def hostile_env(monkeypatch):
    """The strongest possible 'enable live' attempt: confirmation flag AND
    credentials present. Live must STILL be refused."""
    monkeypatch.setenv("QUANT_VAULT_ENABLE_LIVE_TRADING", "I_UNDERSTAND_THE_RISK")
    monkeypatch.setenv("BINANCE_API_KEY", "x" * 64)
    monkeypatch.setenv("BINANCE_API_SECRET", "y" * 64)


def test_no_confirmation_mechanism_exists():
    """Pins the removal: there must be no flag, method, or constant that
    could ever re-enable live trading."""
    assert not hasattr(BinanceExecutionAdapter, "live_trading_allowed")
    assert not hasattr(BinanceExecutionAdapter, "LIVE_CONFIRMATION_VALUE")


def test_live_init_refused_unconditionally():
    with pytest.raises(RuntimeError, match="PERMANENTLY DISABLED"):
        BinanceExecutionAdapter(mode="live")


def test_unknown_mode_refused_at_construction():
    with pytest.raises(RuntimeError, match="supports 'paper' only"):
        BinanceExecutionAdapter(mode="bogus")


def test_set_mode_live_refused_unconditionally():
    adapter = BinanceExecutionAdapter(mode="paper")
    res = adapter.set_mode("live")
    assert res["success"] is False
    assert "PERMANENTLY DISABLED" in res["error"]
    assert adapter.mode == "paper"


def test_set_mode_rejects_unknown_modes():
    adapter = BinanceExecutionAdapter(mode="paper")
    res = adapter.set_mode("demo")
    assert res["success"] is False
    assert adapter.mode == "paper"


def test_set_mode_paper_is_idempotent():
    adapter = BinanceExecutionAdapter(mode="paper")
    res = adapter.set_mode("paper")
    assert res["success"] is True and adapter.mode == "paper"


def test_trader_start_refuses_smuggled_live_adapter(tmp_path):
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    # Simulate a live-mode adapter smuggled past construction.
    trader.execution_adapter.mode = "live"
    with pytest.raises(RuntimeError, match="paper-only guard"):
        trader.start()
    assert trader.is_running is False


def test_paper_execute_never_touches_network(tmp_path, monkeypatch):
    adapter = BinanceExecutionAdapter(mode="paper", ledger_path=str(tmp_path / "orders.json"))
    adapter.fill_simulator = StubFillSimulator()  # no network at all

    def _boom(*a, **k):
        raise AssertionError("no HTTP request may be issued by the paper-only adapter")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    res = adapter.execute_order("BTCUSDT", "BUY", 0.1, 60000.0, order_type="MARKET")
    assert res["success"] is True
    assert res["order"]["execution_mode"] == "PAPER"

    res = adapter.execute_order("BTCUSDT", "BUY", 0.1, 60000.0, order_type="LIMIT_MAKER")
    assert res["success"] is True
    assert res["order"]["execution_mode"] == "PAPER"


def test_smuggled_live_mode_cannot_send_orders(tmp_path, monkeypatch):
    """Even with mode hand-set to 'live' AND credentials AND confirmation
    in the environment, the order boundary refuses without touching the
    network."""
    adapter = BinanceExecutionAdapter(mode="paper", ledger_path=str(tmp_path / "orders.json"))
    adapter.mode = "live"  # smuggled past construction

    def _boom(*a, **k):
        raise AssertionError("private order endpoint must never be touched")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    res = adapter.execute_order("BTCUSDT", "BUY", 0.1, 60000.0)
    assert res["success"] is False
    assert "PERMANENTLY DISABLED" in res["error"]


def test_status_never_reports_live(tmp_path):
    adapter = BinanceExecutionAdapter(mode="paper", ledger_path=str(tmp_path / "orders.json"))
    st = adapter.get_status()
    assert st["mode"] == "PAPER"
    assert st["is_live"] is False
