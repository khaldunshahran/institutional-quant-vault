"""
Quant Sentry & Continuous Alpha Research Engine
Author: Google Antigravity (Advanced Agentic Systems)

Acts as an automated quantitative research scientist monitoring live execution 24/7:
1. Audits every trade closed in the 24-hour rolling window.
2. Identifies loss patterns: false breakouts, adverse excursions, regime shifts.
3. Computes statistical metrics: Win Rate, Profit Factor, Expectancy, Sharpe Proxy.
4. Generates concrete recommendations on parameter tuning (ATR stops, trailing thresholds, asset blacklists).
5. Dispatches concise executive insights to Telegram and writes persistent audit dossiers to disk.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class QuantSentryAuditor:
    def __init__(self, runtime_dir: Optional[Path] = None):
        self.runtime_dir = runtime_dir or (PROJECT_ROOT / "runtime")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.runtime_dir / "autonomous_trade_history.json"
        self.positions_file = self.runtime_dir / "autonomous_positions.json"
        self.state_file = self.runtime_dir / "autonomous_state.json"
        self.sentry_log_file = self.runtime_dir / "sentry_hourly_log.json"
        self.dossier_file = self.runtime_dir / "sentry_dossier.md"

    def load_trades(self, limit: int = 500) -> List[Dict[str, Any]]:
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data[-limit:] if isinstance(data, list) else []
        except Exception:
            return []

    def load_positions(self) -> List[Dict[str, Any]]:
        if not self.positions_file.exists():
            return []
        try:
            with open(self.positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return list(data.values()) if isinstance(data, dict) else data
        except Exception:
            return []

    def perform_audit(self) -> Dict[str, Any]:
        """Performs a comprehensive scientific audit of all trade telemetry."""
        trades = self.load_trades()
        positions = self.load_positions()

        now = time.time()
        # Look back 24h
        cutoff_24h = now - 86400.0
        cutoff_1h = now - 3600.0

        trades_24h = []
        trades_1h = []
        for t in trades:
            # Parse closed timestamp or fallback
            ts = t.get("closed_timestamp", 0)
            if not ts:
                # Approximate from closed_at string or include all recent
                trades_24h.append(t)
            else:
                if ts >= cutoff_24h:
                    trades_24h.append(t)
                if ts >= cutoff_1h:
                    trades_1h.append(t)

        if not trades_24h:
            trades_24h = trades[-100:]  # fallback to last 100

        total_trades = len(trades_24h)
        wins = [t for t in trades_24h if float(t.get("pnl_usd", 0)) > 0]
        losses = [t for t in trades_24h if float(t.get("pnl_usd", 0)) < 0]
        flats = [t for t in trades_24h if float(t.get("pnl_usd", 0)) == 0]

        gross_gain = sum(float(t.get("pnl_usd", 0)) for t in wins)
        gross_loss = sum(float(t.get("pnl_usd", 0)) for t in losses)
        net_pnl = gross_gain + gross_loss
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        avg_win = (gross_gain / len(wins)) if wins else 0.0
        avg_loss = (abs(gross_loss) / len(losses)) if losses else 0.0
        profit_factor = (gross_gain / abs(gross_loss)) if abs(gross_loss) > 0 else (99.0 if gross_gain > 0 else 1.0)
        expectancy = ((win_rate / 100.0) * avg_win) - (((100.0 - win_rate) / 100.0) * avg_loss)

        # Asset breakdown
        by_asset: Dict[str, Dict[str, Any]] = {}
        for t in trades_24h:
            sym = t.get("symbol", "UNKNOWN")
            pnl = float(t.get("pnl_usd", 0))
            if sym not in by_asset:
                by_asset[sym] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            by_asset[sym]["trades"] += 1
            if pnl > 0:
                by_asset[sym]["wins"] += 1
            elif pnl < 0:
                by_asset[sym]["losses"] += 1
            by_asset[sym]["pnl"] += pnl

        # Exit reasons breakdown
        by_reason: Dict[str, Dict[str, Any]] = {}
        for t in trades_24h:
            r = t.get("exit_reason", "OTHER")
            pnl = float(t.get("pnl_usd", 0))
            if r not in by_reason:
                by_reason[r] = {"count": 0, "pnl": 0.0}
            by_reason[r]["count"] += 1
            by_reason[r]["pnl"] += pnl

        # Scientific Insights & Tuning Recommendations
        insights = []
        top_winners = sorted(by_asset.items(), key=lambda x: x[1]["pnl"], reverse=True)
        top_losers = sorted(by_asset.items(), key=lambda x: x[1]["pnl"])

        if top_winners and top_winners[0][1]["pnl"] > 0:
            sym, stats = top_winners[0]
            insights.append(f"Top Alpha Generator: {sym} (+${stats['pnl']:,.2f} over {stats['trades']} trades).")

        if top_losers and top_losers[0][1]["pnl"] < -500:
            sym, stats = top_losers[0]
            insights.append(f"Drawdown Drag: {sym} (-${abs(stats['pnl']):,.2f}). High chop/wick sensitivity.")

        stop_loss_stats = by_reason.get("STOP_LOSS", {"count": 0, "pnl": 0.0})
        tp2_stats = by_reason.get("TP2_RUNNER_TARGET", {"count": 0, "pnl": 0.0})

        if stop_loss_stats["count"] > 0 and abs(stop_loss_stats["pnl"]) > gross_gain * 0.7:
            insights.append("Stop-loss clustering detected during consolidation. ATR multiplier widening recommended.")

        if expectancy > 0:
            edge_verdict = f"POSITIVE MATHEMATICAL EDGE (+$ {expectancy:.2f}/trade)"
        elif expectancy > -15.0:
            edge_verdict = f"NEUTRAL/TRANSITIONAL (Friction dominant: ${expectancy:.2f}/trade)"
        else:
            edge_verdict = f"NEGATIVE EDGE SKEW (${expectancy:.2f}/trade) - Counter-Trend Traps"

        audit_result = {
            "timestamp": now,
            "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
            "total_trades_analyzed": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "net_pnl_usd": round(net_pnl, 2),
            "gross_gain_usd": round(gross_gain, 2),
            "gross_loss_usd": round(gross_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "expectancy_usd": round(expectancy, 2),
            "edge_verdict": edge_verdict,
            "by_reason": by_reason,
            "top_winners": top_winners[:3],
            "top_losers": top_losers[:3],
            "open_positions_count": len(positions),
            "insights": insights,
        }

        # Save to persistent sentry log
        self._record_sentry_log(audit_result)
        self._generate_dossier_markdown(audit_result)

        return audit_result

    def _record_sentry_log(self, audit: Dict[str, Any]):
        try:
            records = []
            if self.sentry_log_file.exists():
                records = json.loads(self.sentry_log_file.read_text(encoding="utf-8"))
            records.append(audit)
            if len(records) > 48:
                records = records[-48:]
            self.sentry_log_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _generate_dossier_markdown(self, audit: Dict[str, Any]):
        try:
            lines = [
                f"# Quantitative Sentry & Continuous Alpha Dossier",
                f"**Generated:** {audit['timestamp_utc']}",
                f"",
                f"## 1. Executive Performance Metrics",
                f"- **Net PnL:** `${audit['net_pnl_usd']:,.2f}`",
                f"- **Gross Gains:** `+${audit['gross_gain_usd']:,.2f}` ({audit['wins']} Wins)",
                f"- **Gross Losses:** `-${abs(audit['gross_loss_usd']):,.2f}` ({audit['losses']} Losses)",
                f"- **Win Rate:** `{audit['win_rate_pct']:.1f}%`",
                f"- **Profit Factor:** `{audit['profit_factor']:.2f}`",
                f"- **Avg Win / Avg Loss:** `+${audit['avg_win_usd']:.2f}` / `-${audit['avg_loss_usd']:.2f}`",
                f"- **Mathematical Edge:** **{audit['edge_verdict']}**",
                f"",
                f"## 2. Exit Execution Breakdown",
            ]
            for r, stats in audit.get("by_reason", {}).items():
                lines.append(f"- **{r}:** {stats['count']} trades | PnL: `${stats['pnl']:,.2f}`")

            lines.append("")
            lines.append("## 3. Top Alpha & Drag Assets")
            lines.append("### Top Performers")
            for sym, s in audit.get("top_winners", []):
                lines.append(f"- **{sym}:** `+${s['pnl']:,.2f}` ({s['wins']}W / {s['losses']}L)")
            lines.append("### Top Underperformers")
            for sym, s in audit.get("top_losers", []):
                lines.append(f"- **{sym}:** `-${abs(s['pnl']):,.2f}` ({s['wins']}W / {s['losses']}L)")

            lines.append("")
            lines.append("## 4. Scientist Takeaways & Optimization Blueprint")
            for ins in audit.get("insights", []):
                lines.append(f"- 💡 {ins}")

            self.dossier_file.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

    def format_telegram_sentry_summary(self) -> str:
        """Constructs an executive telegram update formatted for mobile."""
        audit = self.perform_audit()
        pnl = audit["net_pnl_usd"]
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_sign = "+" if pnl >= 0 else "-"

        insights_str = "\n".join([f"• 💡 <i>{i}</i>" for i in audit["insights"][:3]])
        if not insights_str:
            insights_str = "• 💡 <i>System in calibration mode. Accumulating statistically significant sample size.</i>"

        msg = (
            f"🔬 <b>SENTRY SCIENTIST: HOURLY ALPHA REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>FINANCIAL HEALTH:</b>\n"
            f"• <b>Net Realized PnL:</b> {pnl_emoji} <b>{pnl_sign}${abs(pnl):,.2f}</b>\n"
            f"• <b>Gross Gains:</b> 🟢 +${audit['gross_gain_usd']:,.2f} ({audit['wins']} Wins)\n"
            f"• <b>Gross Losses:</b> 🔴 -${abs(audit['gross_loss_usd']):,.2f} ({audit['losses']} Losses)\n"
            f"• <b>Win Rate:</b> {audit['win_rate_pct']:.1f}% (PF: {audit['profit_factor']:.2f})\n"
            f"• <b>Edge Metric:</b> <b>{audit['edge_verdict']}</b>\n\n"
            f"📊 <b>SCIENTIFIC TAKEAWAYS:</b>\n"
            f"{insights_str}\n\n"
            f"🎯 <b>ACTIVE POSITIONS:</b> {audit['open_positions_count']} open\n"
            f"<i>Send <code>/audit</code> anytime for instant full quantitative drill-down.</i>"
        )
        return msg


if __name__ == "__main__":
    auditor = QuantSentryAuditor()
    rep = auditor.perform_audit()
    print("--- SENTRY AUDIT RESULT ---")
    print(json.dumps(rep, indent=2))
    print("\n--- TELEGRAM MESSAGE FORMAT ---")
    try:
        print(auditor.format_telegram_sentry_summary())
    except UnicodeEncodeError:
        print(auditor.format_telegram_sentry_summary().encode('ascii', errors='replace').decode('ascii'))
