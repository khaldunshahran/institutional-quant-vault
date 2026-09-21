import pytest
from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader

def test_signal_inversion(monkeypatch, tmp_path):
    # Enable inversion flag
    monkeypatch.setenv("QV_INVERT_SIGNALS", "true")
    
    # Initialize trader with test runtime dir
    trader = AutonomousMultiAssetTrader(runtime_dir=str(tmp_path), auto_start=False)
    
    # Verify the flag was parsed correctly
    assert getattr(trader, "invert_signals", False) is True

    # Let's mock _log_decision
    logged_decisions = []
    def mock_log_decision(symbol, outcome, reason, details=None):
        logged_decisions.append((symbol, outcome, reason, details))
    trader._log_decision = mock_log_decision

    # Let's directly call _create_position with a mock signal that is inverted
    mock_signal = {
        "symbol": "BTC_USDC",
        "side": "SELL",
        "original_side": "BUY",
        "signal_inverted": True,
        "sl_pct": 0.015,
        "tp1_pct": 0.012,
        "tp2_pct": 0.025,
        "setup": "MOCK_SETUP"
    }

    # Mock _record_fill_event to return True
    trader._record_fill_event = lambda *args, **kwargs: True
    
    # Mock _save_positions
    trader._save_positions = lambda: None
    trader.pending_entries = {"BTC_USDC": {}}

    trader._create_position(
        signal=mock_signal,
        side="SELL",  # inverted side
        qty=0.1,
        price=60000.0,
        entry_fee_usd=1.0
    )

    # Check open_positions
    pos = trader.open_positions["BTC_USDC"]
    assert pos["side"] == "SHORT"
    assert pos["signal_inverted"] is True
    assert pos["original_side"] == "BUY"

    # Check log decision for APPROVED
    approved_logs = [d for d in logged_decisions if d[1] == "APPROVED"]
    assert len(approved_logs) == 1
    log_details = approved_logs[0][3]
    assert log_details["signal_inverted"] is True
    assert log_details["original_side"] == "BUY"

    # Verify SL/TP logic based on inverted side (SELL -> SHORT)
    # Price was 60000. For a short, SL should be higher, TP lower.
    assert pos["stop_loss"] > 60000.0
    assert pos["tp1"] < 60000.0

    print("Test passed: Inverted side properly computed SL/TP and persisted metadata.")
