"""Tests for the hard paper-only lock: live trading is refused unless the
user explicitly confirms via QUANT_VAULT_ENABLE_LIVE_TRADING, and the paper
path never touches a private order endpoint."""
from unittest.mock import MagicMock

import pytest

from scripts.binance_execution_adapter import BinanceExecutionAdapter
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
from test_risk_and_correlation import StubFillSimulator

CONFIRM = BinanceExecutionAdapter.LIVE_CONFIRMATION_VALUE


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("QUANT_VAULT_ENABLE_LIVE_TRADING", raising=False)
    # Keep any real credentials out of these tests.
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)


def test_live_init_refused_without_confirmation():
    with pytest.raises(RuntimeError, match="LIVE mode REFUSED"):
        BinanceExecutionAdapter(mode="live")


def test_set_mode_live_refused_without_confirmation():
    adapter = BinanceExecutionAdapter(mode="paper")
    res = adapter.set_mode("live")
    assert res["success"] is False
    assert "QUANT_VAULT_ENABLE_LIVE_TRADING" in res["error"]
    assert adapter.mode == "paper"


def test_keys_alone_do_not_enable_live(monkeypatch):
    # Even WITH credentials present, live is refused without the flag.
    monkeypatch.setenv("BINANCE_API_KEY", "x" * 64)
    monkeypatch.setenv("BINANCE_API_SECRET", "y" * 64)
    with pytest.raises(RuntimeError, match="LIVE mode REFUSED"):
        BinanceExecutionAdapter(mode="live")
    adapter = BinanceExecutionAdapter(mode="paper")
    assert adapter.set_mode("live")["success"] is False


def test_live_allowed_with_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("QUANT_VAULT_ENABLE_LIVE_TRADING", CONFIRM)
    monkeypatch.setenv("BINANCE_API_KEY", "x" * 64)
    monkeypatch.setenv("BINANCE_API_SECRET", "y" * 64)
    adapter = BinanceExecutionAdapter(mode="paper")
    res = adapter.set_mode("live")
    assert res["success"] is True and adapter.mode == "live"
    adapter2 = BinanceExecutionAdapter(mode="live")
    assert adapter2.mode == "live"


def test_trader_start_refuses_live_without_confirmation(tmp_path):
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    trader.telegram_bot = MagicMock()
    trader.episodic_memory = MagicMock()
    # Simulate a live-mode adapter smuggled past construction.
    trader.execution_adapter.mode = "live"
    with pytest.raises(RuntimeError, match="paper-only guard"):
        trader.start()
    assert trader.is_running is False


def test_paper_execute_never_touches_private_endpoints(tmp_path):
    adapter = BinanceExecutionAdapter(mode="paper", ledger_path=str(tmp_path / "orders.json"))
    adapter.fill_simulator = StubFillSimulator()  # no network at all

    def _boom(params):
        raise AssertionError("private order endpoint must never be touched in paper mode")

    adapter._sign_query = _boom
    res = adapter.execute_order("BTCUSDT", "BUY", 0.1, 60000.0, order_type="MARKET")
    assert res["success"] is True
    assert res["order"]["execution_mode"] == "PAPER"

    res = adapter.execute_order("BTCUSDT", "BUY", 0.1, 60000.0, order_type="LIMIT_MAKER")
    assert res["success"] is True
    assert res["order"]["execution_mode"] == "PAPER"
