"""
Telegram Push Alerting & Remote Interactive Command Bot - INSTITUTIONAL EDITION
Author: Google Antigravity (Advanced Agentic Systems)

Delivers real-time institutional push notifications and interactive mobile control:
- Entry executions with leverage, stops, EV, and running daily profit scorecard
- TP1 50% profit banking and fee-protected breakeven lock
- Real-time trade exit summaries with net realized PnL, duration, and today's total profit
- Daily €100 ($110) target reached celebration & -$40 circuit breaker warnings
- Two-way interactive command listener (long-polling):
    /today or "Update?" -> Instant daily profit & performance scorecard
    /status             -> Operational health, engine mode, circuit breaker
    /positions          -> Active positions with live mark prices & unrealized PnL
    /pause & /resume    -> Remote control of autonomous hunting daemon
    /close [SYMBOL]     -> Emergency position close from phone
    /help               -> Command directory
- Full outbox logging (runtime/telegram_outbox.json) with seamless network fallback
"""

import os
import sys
import time
import json
import random
import logging
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

logger = logging.getLogger(__name__)


def make_progress_bar(current: float, target: float, length: int = 10) -> str:
    """Generates a text-based progress bar for daily target tracking."""
    if target <= 0:
        return "[──────────] 0%"
    pct = max(0.0, (current / target) * 100.0)
    capped_pct = min(100.0, pct)
    filled = int(round((capped_pct / 100.0) * length))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct:.1f}%"


class TelegramAlertBot:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.repo_root = Path(__file__).resolve().parent.parent
        self._load_env_file()

        self.bot_token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.outbox_path = self.repo_root / "runtime" / "telegram_outbox.json"
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path = self.repo_root / "runtime" / "telegram_config.json"
        self.alerts_enabled = self._load_alerts_enabled()
        self.sent_count = 0

        # State providers and command callbacks
        self.state_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self.action_handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None

        # Polling and scheduler daemon variables
        self.is_polling = False
        self.polling_thread: Optional[threading.Thread] = None
        self.hourly_thread: Optional[threading.Thread] = None
        self.last_update_id = 0
        self._lock = threading.Lock()

    def _load_alerts_enabled(self) -> bool:
        """Loads alert toggle from disk. Defaults to True."""
        try:
            if self.config_path.exists():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                return bool(data.get("alerts_enabled", True))
        except Exception:
            pass
        return True

    def _save_alerts_enabled(self, enabled: bool):
        """Saves alert toggle to disk."""
        self.alerts_enabled = enabled
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps({"alerts_enabled": enabled, "updated_at": time.time()}, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save telegram config: {e}")

    # =========================================================================
    # CORE DISPATCH & LOGGING
    # =========================================================================

    def send_message(self, text: str, reply_to_id: Optional[int] = None, force: bool = False) -> Dict[str, Any]:
        """
        Sends an HTML-formatted message to Telegram or logs to outbox if unconfigured or muted.
        If force=True, sends regardless of alerts_enabled (used for direct user command responses).
        """
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        record = {
            "timestamp": time.time(),
            "timestamp_utc": now_utc,
            "text": text,
            "delivered": False,
        }

        # Check if alerts are muted and this is an automated push (not a direct user command)
        if not self.alerts_enabled and not force:
            record["muted"] = True
            self._log_outbox(record)
            return {"success": True, "mode": "ALERTS_MUTED", "sent_count": self.sent_count}

        if self.bot_token and self.chat_id:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload_dict = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if reply_to_id:
                    payload_dict["reply_to_message_id"] = reply_to_id

                payload = json.dumps(payload_dict).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    if resp.getcode() == 200:
                        record["delivered"] = True
                        self.sent_count += 1
                        self._log_outbox(record)
                        return {"success": True, "mode": "TELEGRAM_DELIVERED", "sent_count": self.sent_count}
            except Exception as e:
                # If failed with reply_to_id, retry once without reply_to_message_id
                if reply_to_id:
                    try:
                        payload_dict.pop("reply_to_message_id", None)
                        payload = json.dumps(payload_dict).encode("utf-8")
                        req = urllib.request.Request(
                            url,
                            data=payload,
                            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                        )
                        with urllib.request.urlopen(req, timeout=8.0) as resp:
                            if resp.getcode() == 200:
                                record["delivered"] = True
                                self.sent_count += 1
                                self._log_outbox(record)
                                return {"success": True, "mode": "TELEGRAM_DELIVERED", "sent_count": self.sent_count}
                    except Exception:
                        pass
                logger.warning(f"Telegram dispatch failed: {e}. Falling back to outbox.")

        # Fallback to Outbox
        self._log_outbox(record)
        return {"success": True, "mode": "OUTBOX_LOGGED", "sent_count": self.sent_count}

    def _log_outbox(self, record: Dict[str, Any]):
        try:
            items = []
            if self.outbox_path.exists():
                with open(self.outbox_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
            items.insert(0, record)
            if len(items) > 60:
                items = items[:60]
            with open(self.outbox_path, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to log telegram outbox: {e}")

    # =========================================================================
    # INSTITUTIONAL TRADE EVENT NOTIFIERS
    # =========================================================================

    def notify_entry(
        self,
        symbol: str,
        side: str,
        price: float,
        leverage: int,
        sl: float,
        tp1: float,
        tp2: float,
        ev_usd: float,
        rationale: str,
        notional_usd: float = 10000.0,
        margin_usd: float = 1000.0,
        daily_pnl: float = 0.0,
        daily_trades: int = 0,
        daily_target: float = 2000.0,
        open_count: int = 1
    ) -> Dict[str, Any]:
        """Dispatches an institutional trade entry notification with today's running scorecard."""
        side_emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
        side_clean = "LONG" if side in ("LONG", "BUY") else "SHORT"
        bar = make_progress_bar(daily_pnl, daily_target)
        pnl_sign = "+" if daily_pnl >= 0 else "-"
        pnl_emoji = "🟢" if daily_pnl > 0 else ("🔴" if daily_pnl < 0 else "⚪")

        px_fmt = f"${price:,.4f}" if price < 1.0 else f"${price:,.2f}"
        sl_fmt = f"${sl:,.4f}" if price < 1.0 else f"${sl:,.2f}"
        tp1_fmt = f"${tp1:,.4f}" if price < 1.0 else f"${tp1:,.2f}"
        tp2_fmt = f"${tp2:,.4f}" if price < 1.0 else f"${tp2:,.2f}"

        text = (
            f"<b>{side_emoji} NEW TRADE OPENED: {side_clean} {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Asset:</b> {symbol}\n"
            f"• <b>Side:</b> {side_clean} ({leverage}x Leverage)\n"
            f"• <b>Entry Price:</b> {px_fmt}\n"
            f"• <b>Position Size:</b> ${notional_usd:,.0f} notional (Margin: <b>${margin_usd:,.0f}</b>)\n"
            f"• <b>Stop Loss:</b> {sl_fmt} (Dynamic ATR)\n"
            f"• <b>Take Profit 1:</b> {tp1_fmt} (50% Scale & Break-Even)\n"
            f"• <b>Take Profit 2:</b> {tp2_fmt} (Runner Target)\n"
            f"• <b>Setup & Rationale:</b> <i>{rationale[:120]}</i>\n\n"
            f"📊 <b>TODAY'S RUNNING SCORECARD:</b>\n"
            f"• <b>Realized Net PnL:</b> {pnl_emoji} <b>{pnl_sign}${abs(daily_pnl):,.2f}</b>\n"
            f"• <b>Active Positions:</b> {open_count} concurrent\n"
            f"• <b>Daily Target ($2,000):</b> {bar}\n\n"
            f"<i>Send <code>/mute</code> to pause notifications</i>"
        )
        return self.send_message(text)

    def notify_tp1(
        self,
        symbol: str,
        side: str,
        banked_pnl: float,
        be_stop: float,
        daily_pnl: float = 0.0,
        daily_target: float = 2000.0
    ) -> Dict[str, Any]:
        """Dispatches a TP1 hit alert (50% banked & fee-protected breakeven locked)."""
        bar = make_progress_bar(daily_pnl, daily_target)
        pnl_sign = "+" if daily_pnl >= 0 else "-"
        be_fmt = f"${be_stop:,.4f}" if be_stop < 1.0 else f"${be_stop:,.2f}"

        text = (
            f"<b>💰 TAKE PROFIT 1 HIT: {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Banked Profit:</b> +${banked_pnl:,.2f}\n"
            f"• <b>Risk Status:</b> 🛡️ <b>FREE TRADE LOCKED</b>\n"
            f"• <b>New Stop Loss:</b> {be_fmt} (Fee-Protected Break-Even)\n"
            f"• <b>Remaining 50%:</b> Trailing stop running toward TP2\n\n"
            f"📈 <b>TODAY'S TOTAL PROFIT SO FAR:</b>\n"
            f"• <b>Net PnL:</b> {pnl_sign}${abs(daily_pnl):,.2f}\n"
            f"• <b>Target ($2,000):</b> {bar}"
        )
        return self.send_message(text)

    def notify_trade_closed(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl_usd: float,
        roe_pct: float,
        exit_reason: str,
        duration_sec: int = 0,
        fees_saved: float = 0.45,
        daily_pnl: float = 0.0,
        daily_trades: int = 0,
        wins: int = 0,
        losses: int = 0,
        gross_gain: float = 0.0,
        gross_loss: float = 0.0,
        daily_target: float = 2000.0,
        mode: str = "PAPER"
    ) -> Dict[str, Any]:
        """Dispatches a comprehensive trade closure notification with today's updated scorecard."""
        is_win = pnl_usd > 0
        is_be = abs(pnl_usd) <= 0.50 and "BREAK_EVEN" in exit_reason.upper()
        if is_win:
            pnl_emoji = "🟢"
            status_text = "PROFIT SECURED"
        elif is_be:
            pnl_emoji = "🛡️"
            status_text = "FEE-PROTECTED BREAK-EVEN"
        else:
            pnl_emoji = "🔴"
            status_text = "STOP LOSS TRIGGERED"

        dur_str = f"{duration_sec // 60}m {duration_sec % 60}s" if duration_sec > 0 else "< 1m"
        bar = make_progress_bar(daily_pnl, daily_target)
        pnl_sign = "+" if daily_pnl >= 0 else "-"
        wr = (wins / daily_trades * 100.0) if daily_trades > 0 else 0.0

        en_fmt = f"${entry_price:,.4f}" if entry_price < 1.0 else f"${entry_price:,.2f}"
        ex_fmt = f"${exit_price:,.4f}" if exit_price < 1.0 else f"${exit_price:,.2f}"

        # If gross_gain/loss not provided, calculate from state
        if gross_gain == 0.0 and gross_loss == 0.0:
            st = self._get_live_state()
            gross_gain = st.get("gross_gain", 0.0)
            gross_loss = st.get("gross_loss", 0.0)

        text = (
            f"<b>{pnl_emoji} TRADE CLOSED: {status_text}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Asset:</b> {symbol} ({side})\n"
            f"• <b>Entry:</b> {en_fmt} ➔ <b>Exit:</b> {ex_fmt}\n"
            f"• <b>Realized Net PnL:</b> <b>{'+' if pnl_usd>=0 else ''}${pnl_usd:,.2f}</b> ({'+' if roe_pct>=0 else ''}{roe_pct:.1f}% ROE on margin)\n"
            f"• <b>Exit Reason:</b> {exit_reason}\n"
            f"• <b>Duration:</b> {dur_str}\n\n"
            f"💰 <b>TODAY'S SCORECARD:</b>\n"
            f"• <b>Net PnL Today:</b> <b>{pnl_sign}${abs(daily_pnl):,.2f}</b>\n"
            f"• <b>Total Gains (Wins):</b> 🟢 +${gross_gain:,.2f}\n"
            f"• <b>Total Losses:</b> 🔴 -${abs(gross_loss):,.2f}\n"
            f"• <b>Today's Record:</b> {daily_trades} Trades ({wins}W / {losses}L • {wr:.0f}% WR)\n\n"
            f"<i>Send <code>/mute</code> to pause notifications</i>"
        )
        return self.send_message(text)

    def send_hourly_pnl_update(self) -> Dict[str, Any]:
        """Dispatches an hourly performance update with Total Net PnL, Total Gain, Total Loss, and Scientist Insights."""
        state = self._get_live_state()
        daily_pnl = float(state.get("daily_pnl_usd", 0.0))
        gross_gain = float(state.get("gross_gain", 0.0))
        gross_loss = float(state.get("gross_loss", 0.0))
        wins = int(state.get("wins", 0))
        losses = int(state.get("losses", 0))
        trades_count = int(state.get("daily_trades_count", wins + losses))
        wr = (wins / trades_count * 100.0) if trades_count > 0 else 0.0
        positions = state.get("open_positions", [])

        # Run Quant Sentry Auditor for deep telemetry insights
        insights_str = ""
        try:
            from scripts.quant_sentry_auditor import QuantSentryAuditor
            auditor = QuantSentryAuditor()
            audit = auditor.perform_audit()
            ins_list = audit.get("insights", [])
            if ins_list:
                insights_str = "\n\n🔬 <b>SCIENTIST ALPHA INSIGHTS:</b>\n" + "\n".join([f"• 💡 <i>{i}</i>" for i in ins_list[:2]])
        except Exception as _audit_err:
            logger.warning(f"Sentry audit error in hourly update: {_audit_err}")

        pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"
        pnl_sign = "+" if daily_pnl >= 0 else "-"

        pos_str = ""
        if positions:
            pos_lines = []
            for p in positions[:4]:
                sym = p.get("symbol", "")
                side = p.get("side", "")
                u_pnl = float(p.get("unrealized_pnl_usd", 0.0))
                u_pct = float(p.get("unrealized_pnl_pct", 0.0))
                be = " 🛡️" if p.get("tp1_hit") else ""
                pos_lines.append(f"  • <b>{sym}</b> ({side}): {'+' if u_pnl>=0 else ''}${u_pnl:.2f} ({'+' if u_pct>=0 else ''}{u_pct:.1f}%){be}")
            pos_str = "\n" + "\n".join(pos_lines)
            if len(positions) > 4:
                pos_str += f"\n  <i>...and {len(positions) - 4} more</i>"
        else:
            pos_str = "\n  <i>No open positions. Sniper scanning universe...</i>"

        text = (
            f"⏰ <b>HOURLY QUANT PERFORMANCE & SENTRY REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>FINANCIAL HEALTH (TODAY):</b>\n"
            f"• <b>Total Net PnL:</b> {pnl_emoji} <b>{pnl_sign}${abs(daily_pnl):,.2f}</b>\n"
            f"• <b>Total Gains (Wins):</b> 🟢 <b>+${gross_gain:,.2f}</b> ({wins} winning trades)\n"
            f"• <b>Total Losses:</b> 🔴 <b>-${abs(gross_loss):,.2f}</b> ({losses} losing trades)\n"
            f"• <b>Win Rate:</b> {wr:.1f}% ({trades_count} total closed){insights_str}\n\n"
            f"📊 <b>ACTIVE POSITIONS ({len(positions)}):</b>{pos_str}\n\n"
            f"⚙️ <i>Sentry Active • Send <code>/audit</code> for full research drill-down</i>"
        )
        return self.send_message(text)

    def _hourly_update_loop(self):
        """
        Background scheduler that sends an hourly PnL, total gain, and total loss
        performance report at a randomized time every hour (e.g. between 45 and 65 minutes).
        """
        logger.info("Telegram hourly randomized update scheduler active.")
        while self.is_polling:
            # Randomized interval between 45 to 65 minutes (2700s to 3900s)
            interval_sec = random.randint(2700, 3900)
            elapsed = 0
            while elapsed < interval_sec and self.is_polling:
                time.sleep(5)
                elapsed += 5

            if not self.is_polling:
                break

            # If alerts are enabled, dispatch the hourly performance report
            if self.alerts_enabled:
                try:
                    self.send_hourly_pnl_update()
                except Exception as e:
                    logger.error(f"Error sending hourly telegram update: {e}")

    def notify_daily_goal(self, eur_amount: float, usd_amount: float, total_trades: int) -> Dict[str, Any]:
        """Dispatches celebration alert when daily profit goal is reached."""
        text = (
            f"<b>🏆 DAILY TARGET ACHIEVED! 🏆</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Goal Reached:</b> €{eur_amount:.0f} (${usd_amount:.2f})\n"
            f"• <b>Total Trades Today:</b> {total_trades}\n"
            f"• <b>Vault Lock Mode:</b> ACTIVATED\n"
            f"• <i>Institutional capital preserved in bankroll. High-velocity profits secured for the day.</i>"
        )
        return self.send_message(text)

    def notify_circuit_breaker(self, loss_amount: float, limit: float = 2000.0) -> Dict[str, Any]:
        """Dispatches capital defense circuit breaker warning."""
        text = (
            f"<b>🛡️ DAILY CIRCUIT BREAKER ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Daily Drawdown:</b> -${abs(loss_amount):.2f} (Limit: -${limit:.2f})\n"
            f"• <b>Defense Action:</b> ALL AUTONOMOUS ENTRIES PAUSED\n"
            f"• <i>Trading halted for remainder of the 24h session to preserve capital. Open positions continue to be strictly risk-managed.</i>"
        )
        return self.send_message(text)

    # =========================================================================
    # TWO-WAY INTERACTIVE REMOTE COMMAND ENGINE
    # =========================================================================

    def start_polling(
        self,
        state_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        action_handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
    ):
        """Starts the background Telegram long-polling daemon and hourly scheduler threads."""
        with self._lock:
            if self.is_polling and self.polling_thread and self.polling_thread.is_alive():
                return
            if not self.bot_token or not self.chat_id:
                logger.warning("Telegram polling not started: token or chat_id missing.")
                return

            if state_provider:
                self.state_provider = state_provider
            if action_handler:
                self.action_handler = action_handler

            self.is_polling = True
            self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True, name="TelegramBotPoller")
            self.polling_thread.start()

            self.hourly_thread = threading.Thread(target=self._hourly_update_loop, daemon=True, name="TelegramHourlyScheduler")
            self.hourly_thread.start()
            logger.info("Telegram interactive command poller and hourly scheduler started successfully.")

    def stop_polling(self):
        """Stops the background Telegram long-polling daemon and scheduler threads."""
        with self._lock:
            self.is_polling = False

    def _polling_loop(self):
        """Background thread checking getUpdates with timeout."""
        logger.info("Telegram polling thread active. Listening for commands...")
        while self.is_polling:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.last_update_id + 1}&timeout=10"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.getcode() == 200:
                        data = json.loads(resp.read().decode())
                        if data.get("ok") and data.get("result"):
                            for update in data["result"]:
                                self.last_update_id = update["update_id"]
                                self._process_update(update)
            except Exception as e:
                # Brief sleep on transient network error to prevent spamming
                time.sleep(2)
            time.sleep(0.5)

    def _process_update(self, update: Dict[str, Any]):
        """Processes an incoming Telegram message update."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        sender_id = str(chat.get("id", ""))
        message_id = message.get("message_id")
        text = (message.get("text") or "").strip()

        # Security check: only allow commands from the verified owner chat_id
        if sender_id != self.chat_id:
            logger.warning(f"Unauthorized Telegram message from {sender_id}: {text}")
            return

        if not text:
            return

        clean_cmd = text.lower().strip()
        logger.info(f"Telegram command received: '{text}' from {sender_id}")

        # Dispatch command
        self._handle_command(text, clean_cmd, message_id)

    def _handle_command(self, raw_text: str, cmd: str, reply_to_id: int):
        """Dispatches recognized commands to response handlers."""
        # 0. Alert Notifications Toggle (Mute / Stop Alerts)
        if any(cmd.startswith(x) for x in ["/mute", "/stopalerts", "/silence", "/quiet", "mute", "stop alerts", "stop alert"]):
            self._save_alerts_enabled(False)
            self.send_message(
                "🔕 <b>Alerts Muted</b>\n\n"
                "Automated trade notifications (entries, exits) and hourly performance reports are now paused.\n\n"
                "Send <code>/unmute</code> or <code>/startalerts</code> at any time to resume.\n"
                "<i>(Direct commands like /today, /status, and /positions will still respond)</i>",
                reply_to_id=reply_to_id,
                force=True
            )
            return

        # 0b. Alert Notifications Toggle (Unmute / Resume Alerts)
        if any(cmd.startswith(x) for x in ["/unmute", "/startalerts", "unmute", "start alerts", "start alert"]):
            self._save_alerts_enabled(True)
            self.send_message(
                "🔔 <b>Alerts Resumed</b>\n\n"
                "Real-time notifications are now active!\n"
                "• Instant alerts when a new trade opens\n"
                "• Detailed summaries when a trade closes\n"
                "• Hourly performance updates (Net PnL, Total Gain, Total Loss)",
                reply_to_id=reply_to_id,
                force=True
            )
            return

        # 1. Today's Profit / Performance Summary
        if any(cmd.startswith(x) for x in ["/today", "/profit", "/pnl", "update?", "update", "profit", "today"]):
            reply = self._build_today_report()
            self.send_message(reply, reply_to_id=reply_to_id, force=True)
            return

        # 1b. Sentry Quantitative Audit & Scientist Research Report
        if any(cmd.startswith(x) for x in ["/audit", "/scientist", "/analysis", "audit", "scientist", "analysis"]):
            try:
                from scripts.quant_sentry_auditor import QuantSentryAuditor
                reply = QuantSentryAuditor().format_telegram_sentry_summary()
                self.send_message(reply, reply_to_id=reply_to_id, force=True)
            except Exception as e:
                self.send_message(f"⚠️ Failed to generate sentry audit: {e}", reply_to_id=reply_to_id, force=True)
            return

        # 2. Operational Health & Engine Status
        if cmd.startswith("/status") or cmd == "status":
            reply = self._build_status_report()
            self.send_message(reply, reply_to_id=reply_to_id, force=True)
            return

        # 3. Active Positions Drill-down
        if cmd.startswith("/positions") or cmd == "positions" or cmd == "/pos":
            reply = self._build_positions_report()
            self.send_message(reply, reply_to_id=reply_to_id, force=True)
            return

        # 4. Gold Market & Position Telemetry
        if cmd.startswith("/gold") or cmd == "gold" or cmd == "/xau" or cmd == "xau":
            reply = self._build_gold_report()
            self.send_message(reply, reply_to_id=reply_to_id, force=True)
            return

        # 5. Remote Autopilot Pause
        if cmd.startswith("/pause") or cmd == "pause":
            if self.action_handler:
                res = self.action_handler("pause", {})
                self.send_message("⏸️ <b>Autopilot Paused</b>\n\nBackground hunting halted. Open positions remain actively risk-managed.", reply_to_id=reply_to_id, force=True)
            else:
                self.send_message("⚠️ Action handler not attached to daemon.", reply_to_id=reply_to_id, force=True)
            return

        # 6. Remote Autopilot Resume
        if cmd.startswith("/resume") or cmd.startswith("/start") or cmd == "resume":
            if self.action_handler:
                res = self.action_handler("resume", {})
                self.send_message("▶️ <b>Autopilot Resumed</b>\n\n🚀 Autonomous Multi-Asset Trader active! Hunting Gold (XAU/USD), Top 20 Binance & 24H Movers at $100K bankroll.", reply_to_id=reply_to_id, force=True)
            else:
                self._send_help(reply_to_id)
            return

        # 7. Remote Position Close
        if cmd == "/closeall":
            if self.action_handler:
                res = self.action_handler("closeall", {})
                # Report the ACTUAL outcome: the old code announced success
                # even when every close failed.
                if res.get("success"):
                    ok = int(res.get("closed_count", 0))
                    failed = [r for r in res.get("results", []) if not r.get("success")]
                    if failed:
                        fails = ", ".join(f"{r['symbol']}: {r.get('error', '?')}" for r in failed)
                        self.send_message(f"🚨 <b>Emergency Close Partial</b>\nClosed {ok}; FAILED: {fails}",
                                            reply_to_id=reply_to_id, force=True)
                    else:
                        self.send_message(f"🚨 <b>Emergency Close Executed</b>\nAll open positions closed ({ok}).",
                                            reply_to_id=reply_to_id, force=True)
                else:
                    self.send_message(f"⚠️ Emergency close failed: {res.get('error', 'unknown error')}",
                                        reply_to_id=reply_to_id, force=True)
            else:
                self.send_message("⚠️ Action handler not attached.", reply_to_id=reply_to_id, force=True)
            return

        if cmd.startswith("/close"):
            parts = raw_text.split()
            if len(parts) >= 2:
                target_sym = parts[1].upper()
                if self.action_handler:
                    res = self.action_handler("close", {"symbol": target_sym})
                    if res.get("success"):
                        self.send_message(f"✅ <b>Position Closed:</b> {target_sym} has been liquidated.", reply_to_id=reply_to_id, force=True)
                    else:
                        self.send_message(f"⚠️ Failed to close {target_sym}: {res.get('error', 'Symbol not found')}", reply_to_id=reply_to_id, force=True)
                else:
                    self.send_message("⚠️ Action handler not attached.", reply_to_id=reply_to_id, force=True)
            else:
                self.send_message("ℹ️ Usage: <code>/close XAUUSDT</code> or <code>/closeall</code>", reply_to_id=reply_to_id, force=True)
            return

        # 8. Greeting / Help Directory
        self._send_help(reply_to_id)

    def _send_help(self, reply_to_id: int):
        help_text = (
            "🤖 <b>TypeSafe Jev: Institutional Trading Cockpit</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Here are the available remote commands:\n\n"
            "• <code>/today</code> or <code>update?</code> — <b>Today's Profit & Scorecard</b>\n"
            "• <code>/audit</code> or <code>/scientist</code> — <b>Sentry Scientist Alpha & Optimization Audit</b>\n"
            "• <code>/gold</code> or <code>/xau</code> — <b>Gold (XAU/USD) Market & Positions</b>\n"
            "• <code>/status</code> — Operational health & $100K Bankroll status\n"
            "• <code>/positions</code> — Live active positions & unrealized PnL\n"
            "• <code>/mute</code> (or <code>/stopalerts</code>) — <b>Mute Push Notifications</b>\n"
            "• <code>/unmute</code> — <b>Resume Push Notifications</b>\n"
            "• <code>/pause</code> — Pause autonomous trading\n"
            "• <code>/resume</code> — Resume 24/7 autonomous trading\n"
            "• <code>/close XAUUSDT</code> — Close specific position\n"
            "• <code>/closeall</code> — Emergency close all positions\n"
            "• <code>/help</code> — Show this command menu\n\n"
            "<i>Autonomous trader active across Gold + Top 20 Binance Crypto 24/7.</i>"
        )
        self.send_message(help_text, reply_to_id=reply_to_id, force=True)

    # =========================================================================
    # STATE QUERY HELPERS
    # =========================================================================

    def _get_live_state(self) -> Dict[str, Any]:
        """Fetches live state from attached provider or falls back to disk files."""
        if self.state_provider:
            try:
                state = self.state_provider()
                if state:
                    return state
            except Exception as e:
                logger.error(f"Error querying state_provider: {e}")

        # Fallback to runtime files
        state_file = self.repo_root / "runtime" / "autonomous_state.json"
        pos_file = self.repo_root / "runtime" / "autonomous_positions.json"
        history_file = self.repo_root / "runtime" / "autonomous_trade_history.json"

        daily_pnl = 0.0
        daily_trades = 0
        enabled = True
        cb_active = False
        goal_reached = False
        positions = []
        trades = []

        if state_file.exists():
            try:
                s_data = json.loads(state_file.read_text(encoding="utf-8"))
                daily_pnl = float(s_data.get("daily_pnl_usd", 0.0))
                daily_trades = int(s_data.get("daily_trades_count", 0))
                enabled = bool(s_data.get("enabled", True))
                cb_active = bool(s_data.get("circuit_breaker_triggered", False))
                goal_reached = bool(s_data.get("daily_goal_reached", False))
            except Exception:
                pass

        if pos_file.exists():
            try:
                p_data = json.loads(pos_file.read_text(encoding="utf-8"))
                positions = list(p_data.values()) if isinstance(p_data, dict) else p_data
            except Exception:
                pass

        if history_file.exists():
            try:
                trades = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        wins = sum(1 for t in trades if float(t.get("pnl_usd", 0)) > 0)
        losses = sum(1 for t in trades if float(t.get("pnl_usd", 0)) < 0)
        gross_gain = sum(float(t.get("pnl_usd", 0)) for t in trades if float(t.get("pnl_usd", 0)) > 0)
        gross_loss = sum(float(t.get("pnl_usd", 0)) for t in trades if float(t.get("pnl_usd", 0)) < 0)

        return {
            "daily_pnl_usd": daily_pnl,
            "gross_gain": gross_gain,
            "gross_loss": gross_loss,
            "daily_trades_count": daily_trades,
            "wins": wins,
            "losses": losses,
            "running": enabled,
            "circuit_breaker_active": cb_active,
            "daily_goal_reached": goal_reached,
            "open_positions": positions,
            "open_positions_count": len(positions),
            "daily_profit_target_usd": 2500.0,
        }

    def _build_gold_report(self) -> str:
        """Constructs a real-time Gold (XAU/USD & PAXG) market & trade telemetry report."""
        state = self._get_live_state()
        positions = state.get("open_positions", [])
        gold_pos = [p for p in positions if p.get("symbol") in ("XAUUSDT", "PAXGUSDT")]
        
        # Fetch live gold prices
        xau_px = 0.0
        paxg_px = 0.0
        try:
            r = urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/price?symbol=XAUUSDT", timeout=3.0)
            xau_px = float(json.loads(r.read().decode("utf-8")).get("price", 0.0))
        except Exception:
            pass
        try:
            r2 = urllib.request.urlopen("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", timeout=3.0)
            paxg_px = float(json.loads(r2.read().decode("utf-8")).get("price", 0.0))
        except Exception:
            pass

        active_str = ""
        if gold_pos:
            p = gold_pos[0]
            side = p.get("side", "--")
            side_emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
            u_pnl = float(p.get("unrealized_pnl_usd", 0.0))
            u_pct = float(p.get("unrealized_pnl_pct", 0.0))
            entry_px = float(p.get("entry_price", 0.0))
            qty = float(p.get("quantity", 0.0))
            sl = float(p.get("stop_loss", 0.0))
            tp1 = float(p.get("tp1", 0.0))
            tp2 = float(p.get("tp2", 0.0))
            be_tag = " [FREE TRADE 🛡️]" if p.get("tp1_hit") else ""
            active_str = (
                f"\n🎯 <b>ACTIVE GOLD POSITION:</b>\n"
                f"• {side_emoji} <b>{p.get('symbol')} ({side} 10x)</b>{be_tag}\n"
                f"• <b>Size:</b> {qty:.3f} oz (${p.get('notional_usd', 20000):,.2f})\n"
                f"• <b>Entry:</b> ${entry_px:,.2f} ➔ <b>Mark:</b> ${(xau_px or entry_px):,.2f}\n"
                f"• <b>Unrealized PnL:</b> {'+' if u_pnl>=0 else ''}${u_pnl:.2f} ({'+' if u_pct>=0 else ''}{u_pct:.1f}%)\n"
                f"• <b>Stop Loss:</b> ${sl:,.2f} | <b>TP1:</b> ${tp1:,.2f} | <b>TP2:</b> ${tp2:,.2f}"
            )
        else:
            active_str = "\n🎯 <b>Active Gold Position:</b> None. Scanner actively evaluating breakout signals."

        msg = (
            f"🏆 <b>GOLD (XAU/USD) INSTITUTIONAL DESK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>XAU/USD Futures (Binance):</b> ${xau_px:,.2f}/oz\n"
            f"• <b>PAXG Spot (Paxos Physical):</b> ${paxg_px:,.2f}/oz\n"
            f"• <b>Spread / Arb Delta:</b> ${abs(xau_px - paxg_px):.2f}\n"
            f"• <b>Allocated Position Sizing:</b> $20,000 Notional (~4.56 oz @ 10x)\n"
            f"{active_str}\n\n"
            f"<i>Type /positions to see all asset positions or /today for total daily profit.</i>"
        )
        return msg

    def _build_today_report(self) -> str:
        """Constructs the comprehensive Today's Profit & Performance Dashboard."""
        state = self._get_live_state()
        pnl = float(state.get("daily_pnl_usd", 0.0))
        target = float(state.get("daily_profit_target_usd", 2500.0))
        eur_val = pnl / 1.10
        trades_count = int(state.get("daily_trades_count", 0))
        wins = int(state.get("wins", 0))
        losses = int(state.get("losses", 0))
        wr = (wins / trades_count * 100.0) if trades_count > 0 else 0.0

        bar = make_progress_bar(pnl, target, length=10)
        pnl_sign = "+" if pnl >= 0 else "-"
        pnl_emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")

        positions = state.get("open_positions", [])
        pos_summary = ""
        total_unrealized = 0.0

        if positions:
            pos_lines = []
            for p in positions:
                sym = p.get("symbol", "--")
                side = p.get("side", "--")
                u_pnl = float(p.get("unrealized_pnl_usd", 0.0))
                total_unrealized += u_pnl
                side_emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
                be_tag = " [FREE TRADE 🛡️]" if p.get("tp1_hit") else ""
                pos_lines.append(f"  • {side_emoji} <b>{sym}</b> {side}: {'+' if u_pnl>=0 else ''}${u_pnl:.2f}{be_tag}")
            pos_summary = "\n" + "\n".join(pos_lines)
        else:
            pos_summary = "\n  <i>No open positions. Scanner actively hunting.</i>"

        status_tag = "🚀 ACTIVE" if state.get("running") else "⏸️ PAUSED"
        cb_tag = "⚠️ TRIPPED (-$1,500 limit)" if state.get("circuit_breaker_active") else "✅ OK"

        msg = (
            f"📊 <b>TODAY'S PERFORMANCE DASHBOARD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Realized Profit Today:</b> {pnl_emoji} <b>{pnl_sign}${abs(pnl):,.2f}</b> (~€{eur_val:,.2f})\n"
            f"• <b>Daily Target ($2,500):</b> {bar}\n"
            f"• <b>Trades Executed:</b> {trades_count} ({wins}W / {losses}L • {wr:.0f}% Win Rate)\n"
            f"• <b>Unrealized Floating PnL:</b> {'+' if total_unrealized>=0 else ''}${total_unrealized:,.2f}\n\n"
            f"🎯 <b>ACTIVE POSITIONS ({len(positions)}):</b>{pos_summary}\n\n"
            f"⚙️ <b>SYSTEM STATUS:</b>\n"
            f"• <b>Bankroll:</b> $100,000 USD (30% Max Daily Allocation)\n"
            f"• <b>Autopilot:</b> {status_tag} (Gold + Top 20 Binance @ 10x)\n"
            f"• <b>Circuit Breaker:</b> {cb_tag}\n"
            f"• <b>Updated:</b> {time.strftime('%H:%M:%S UTC', time.gmtime())}"
        )
        return msg

    def _build_status_report(self) -> str:
        """Constructs the high-level system status report."""
        state = self._get_live_state()
        running = state.get("running", True)
        pnl = float(state.get("daily_pnl_usd", 0.0))
        target = float(state.get("daily_profit_target_usd", 2500.0))
        bar = make_progress_bar(pnl, target, length=8)

        msg = (
            f"⚡ <b>JEV AUTONOMOUS COCKPIT STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Autopilot State:</b> {'🟢 HUNTING (Active)' if running else '🔴 PAUSED'}\n"
            f"• <b>Bankroll & Allocation:</b> $100,000 USD ($30,000 Daily Margin Budget)\n"
            f"• <b>Tradable Universe:</b> Gold (XAU/USD), Top 20 Binance & 24H Movers\n"
            f"• <b>Sizing per Trade:</b> $20,000 Notional ($2,000 Margin @ 10x)\n"
            f"• <b>Execution Mandate:</b> Profit or Fee-Protected Break-Even (+0.80% TP1)\n"
            f"• <b>Daily Profit Target ($2,500):</b> {bar}\n"
            f"• <b>Active Positions:</b> {state.get('open_positions_count', 0)} concurrent\n"
            f"• <b>Circuit Breaker:</b> {'⚠️ TRIPPED' if state.get('circuit_breaker_active') else '🛡️ Active & Watching'}\n\n"
            f"<i>Send /gold for gold desk, /today for full profit breakdown, or /pause to halt.</i>"
        )
        return msg

    def _build_positions_report(self) -> str:
        """Constructs a detailed active positions drill-down."""
        state = self._get_live_state()
        positions = state.get("open_positions", [])

        if not positions:
            return (
                "🎯 <b>ACTIVE POSITIONS: NONE</b>\n\n"
                "The Hungry Alpha Predator daemon is scanning BTC, ETH, and SOL 5-minute order books and momentum triggers. No positions are currently open."
            )

        msg_parts = [f"🎯 <b>OPEN POSITIONS ({len(positions)})</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for p in positions:
            sym = p.get("symbol", "--")
            side = p.get("side", "--")
            side_emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
            entry_px = float(p.get("entry_price", 0.0))
            cur_px = float(p.get("current_price", entry_px))
            u_pnl = float(p.get("unrealized_pnl_usd", 0.0))
            u_pct = float(p.get("unrealized_pnl_pct", 0.0))
            sl = float(p.get("stop_loss", 0.0))
            tp1 = float(p.get("tp1", 0.0))
            tp2 = float(p.get("tp2", 0.0))
            lev = p.get("leverage", 15)
            notional = float(p.get("notional_usd", 350.0))
            be_status = "🛡️ FREE TRADE LOCKED (TP1 Hit)" if p.get("tp1_hit") else "⏳ Target TP1 (+1.2%)"

            card = (
                f"\n{side_emoji} <b>{sym} ({side} {lev}x)</b>\n"
                f"• <b>Entry:</b> ${entry_px:,.2f} ➔ <b>Mark:</b> ${cur_px:,.2f}\n"
                f"• <b>Unrealized PnL:</b> {'+' if u_pnl>=0 else ''}${u_pnl:.2f} ({'+' if u_pct>=0 else ''}{u_pct:.1f}% ROE)\n"
                f"• <b>Position Size:</b> ${notional:.2f}\n"
                f"• <b>Stop Loss:</b> ${sl:,.2f}\n"
                f"• <b>TP1:</b> ${tp1:,.2f} | <b>TP2:</b> ${tp2:,.2f}\n"
                f"• <b>Status:</b> {be_status}"
            )
            msg_parts.append(card)

        msg_parts.append("\n<i>To close a trade manually: <code>/close [SYMBOL]</code></i>")
        return "\n".join(msg_parts)

    def _load_env_file(self):
        env_file = self.repo_root / ".env"
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            except Exception:
                pass


if __name__ == "__main__":
    bot = TelegramAlertBot()
    print(f"[TEST] Initializing Bot (Chat ID: {bot.chat_id})...")
    res = bot.notify_entry(
        symbol="BTCUSDT",
        side="LONG",
        price=81420.0,
        leverage=15,
        sl=80442.96,
        tp1=82397.04,
        tp2=83700.0,
        ev_usd=12.25,
        rationale="Hungry Predator Trend Breakout (H=0.68, Z=+1.42)",
        notional_usd=350.0,
        margin_usd=23.33,
        daily_pnl=38.50,
        daily_trades=2,
        daily_target=110.0,
        open_count=1
    )
    print(f"[TEST] Entry Alert Dispatched: {res}")

    res_close = bot.notify_trade_closed(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=81420.0,
        exit_price=82400.0,
        pnl_usd=9.80,
        roe_pct=42.0,
        exit_reason="TAKE_PROFIT_1_SCALE",
        duration_sec=745,
        fees_saved=0.48,
        daily_pnl=48.30,
        daily_trades=3,
        wins=3,
        losses=0,
        daily_target=110.0
    )
    print(f"[TEST] Trade Closed Alert Dispatched: {res_close}")
