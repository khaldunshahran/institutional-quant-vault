# 24-Hour Conservative Strategy Replay Simulation Report

**Date:** Sunday, August 30, 2026 (00:00:00 UTC to 23:59:59 UTC)  
**Execution Mode:** Realistic Live Replay (`scripts/test_btc_5m_session_exit_sl.py` mechanics)  
**Intervals Scanned:** 288 (100% full 24h coverage)  
**Rule Filters:** BTC move >= $70, Stronger side ask >= 0.70, Stake $5.00, SL -25%, Pre-Close Exit 20s  

---

## Executive Performance Summary

| Metric | Strict Rule (12 Trades Cap) | Uncapped (All Signals) |
| :--- | :--- | :--- |
| **Total Trades** | **12** | **52** |
| **Wins** | **12** | **49** |
| **Losses / Stop-Outs** | **0** | **3** (3 SL triggered) |
| **Win Rate** | **100.0%** | **94.2%** |
| **Net Realized PnL** | **$+10.55** | **$+39.31** |
| **Max Drawdown** | — | **$1.42** |

---

## Honest Simulation Observations

1. **Pre-Close Exit Reality**: Exiting 20s before expiry (as `test_btc_5m_session_exit_sl.py` does) sells to the CLOB orderbook at **$0.84 - $0.92**, not $1.00. This avoids last-second binary flip risk but reduces payout per win.
2. **Stop-Loss Protection**: In volatile evening sessions (e.g. `21:20`, `23:15`, `23:40`), BTC sharply retraced after the initial impulse. The **-25% stop-loss triggered**, capping downside losses to ~$1.50 per trade rather than losing the full $5.00 stake.
3. **Strict Cap Advantage**: The morning/afternoon trend on Sunday was exceptionally clean. The strict 12-trade cap completed early in the day before the late-night chop, yielding a clean **12W / 0L** session with **+$10.55** profit.

---

## Traded Intervals Log (First 15 Traded Intervals)

| Time (UTC) | Signal | BTC Impulse | Entry Ask | Exit Bid | Exit Type | Net PnL | Cum. PnL |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: | :---: |
| `00:15` | **UP** | $+84.5 | $0.740 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.22** | $+1.22 |
| `01:05` | **DOWN** | $-80.0 | $0.735 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.71** | $+1.93 |
| `04:00` | **UP** | $+75.3 | $0.730 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.75** | $+2.68 |
| `05:00` | **UP** | $+78.3 | $0.733 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.27** | $+3.95 |
| `08:35` | **DOWN** | $-85.9 | $0.741 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.21** | $+5.16 |
| `10:05` | **UP** | $+74.3 | $0.729 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.76** | $+5.92 |
| `12:00` | **UP** | $+147.9 | $0.803 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.73** | $+6.65 |
| `12:05` | **UP** | $+187.8 | $0.843 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.46** | $+7.11 |
| `12:10` | **UP** | $+103.4 | $0.758 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.54** | $+7.65 |
| `12:15` | **DOWN** | $-80.3 | $0.735 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.26** | $+8.91 |
| `12:55` | **UP** | $+95.9 | $0.751 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.13** | $+10.04 |
| `13:40` | **UP** | $+107.1 | $0.762 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.51** | $+10.55 |
| `13:45` | **UP** | $+127.0 | $0.782 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.88** | $+11.43 |
| `14:15` | **DOWN** | $-94.5 | $0.750 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.14** | $+12.57 |
| `14:35` | **DOWN** | $-111.4 | $0.766 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.00** | $+13.57 |
| `15:05` | **UP** | $+125.3 | $0.780 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.90** | $+14.47 |
| `15:10` | **DOWN** | $-103.9 | $0.759 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.06** | $+15.53 |
| `15:20` | **UP** | $+71.2 | $0.726 | $0.84 | TIME_EXIT_20S (✅ WIN) | **$+0.78** | $+16.31 |
| `16:00` | **UP** | $+103.8 | $0.759 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.06** | $+17.37 |
| `16:05` | **DOWN** | $-112.7 | $0.768 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.99** | $+18.36 |
| `16:10` | **UP** | $+282.0 | $0.845 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.44** | $+18.80 |
| `16:30` | **UP** | $+131.3 | $0.786 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.85** | $+19.65 |
| `16:55` | **UP** | $+127.7 | $0.783 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.88** | $+20.53 |
| `17:00` | **DOWN** | $-93.9 | $0.749 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+1.14** | $+21.67 |
| `17:15` | **DOWN** | $-164.7 | $0.820 | $0.92 | TIME_EXIT_20S (✅ WIN) | **$+0.61** | $+22.28 |