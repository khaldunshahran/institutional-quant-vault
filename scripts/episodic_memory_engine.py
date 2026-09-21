"""
Episodic Trade Memory & Continuous AI Post-Mortem Engine
Author: Google Antigravity (Advanced Agentic Systems)

Records every trade outcome into a structured episodic memory bank
(runtime/trade_memory.json). Conducts automated quantitative post-mortems
and injects relevant historical lessons into live trade evaluations to
prevent repeating mistakes and double down on winning patterns.
"""

import os
import time
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class EpisodicMemoryEngine:
    def __init__(self, memory_file_path: Optional[str] = None):
        if memory_file_path:
            self.memory_path = Path(memory_file_path)
        else:
            # Default to repo runtime directory
            repo_root = Path(__file__).resolve().parent.parent
            self.memory_path = repo_root / "runtime" / "trade_memory.json"
            
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memories: List[Dict[str, Any]] = self._load_memories()

    def record_trade_post_mortem(
        self,
        trade_id: str,
        side: str,
        entry_price: float,
        exit_price: float,
        realized_pnl: float,
        exit_reason: str,
        entry_metrics: Dict[str, Any],
        duration_sec: float = 0.0,
        metrics_provenance: str = "live",
    ) -> Dict[str, Any]:
        """
        Synthesizes a quantitative post-mortem and appends it to episodic memory.

        metrics_provenance: "live" for metrics measured at entry time by the
        trading loop; "backfilled" for historical records reconstructed without
        entry telemetry. Backfilled records are stored but NEVER used for
        similarity matching in retrieve_relevant_lessons, because their metrics
        were not observed (using them would let outcome labels masquerade as
        predictive features).
        """
        is_win = realized_pnl > 0.0
        hurst = entry_metrics.get("hurst", 0.50)
        # Only claim causal insight about flow when flow was actually measured.
        # Defaults of 0.0 previously generated confident-sounding but fabricated
        # diagnoses ("lacked strong CVD taker conviction (+0.0 BTC)") on every
        # stop-loss. That ends here.
        obi_provided = "obi" in entry_metrics
        cvd_provided = "perp_cvd_15m" in entry_metrics
        obi = entry_metrics.get("obi", 0.0)
        cvd = entry_metrics.get("perp_cvd_15m", 0.0)
        session = entry_metrics.get("session_name", "UNKNOWN")
        strategy_mode = entry_metrics.get("strategy_mode", "TREND_EXPANSION")

        # Synthesize quantitative takeaway (honest about missing telemetry)
        if is_win:
            if exit_reason == "TAKE_PROFIT_2":
                lesson = f"Runner win (+${realized_pnl:.2f}): {side} held through TP1 to TP2 with Hurst {hurst:.2f}. Trend rider validated."
            elif obi_provided and cvd_provided:
                lesson = f"Target hit (+${realized_pnl:.2f}): {side} executed at OBI {obi:+.2f} with CVD {cvd:+.1f} BTC. Setup high conviction."
            else:
                lesson = f"Target hit (+${realized_pnl:.2f}): {side} closed in profit. Entry flow telemetry unavailable; no causal attribution."
        else:
            if exit_reason == "STOP_LOSS":
                if session in ("ASIA_RANGE", "OFF_HOURS_DRIFT"):
                    lesson = f"Loss (-${abs(realized_pnl):.2f}): {side} stopped out in {session}. Tighten invalidation and require OBI > +0.30."
                elif cvd_provided and abs(cvd) < 10.0:
                    lesson = f"Loss (-${abs(realized_pnl):.2f}): {side} lacked strong CVD taker conviction ({cvd:+.1f} BTC). Require strong flow."
                elif not cvd_provided:
                    lesson = f"Loss (-${abs(realized_pnl):.2f}): {side} invalidation hit at ${exit_price:.1f}. Entry flow telemetry unavailable; no causal attribution."
                else:
                    lesson = f"Loss (-${abs(realized_pnl):.2f}): Invalidation hit at ${exit_price:.1f}. Controlled loss under -2% risk cap."
            elif exit_reason == "ALPHA_DECAY_TIMEOUT":
                lesson = f"Alpha Decay ({realized_pnl:+.2f}): Trade stalled for {int(duration_sec//60)}m without momentum. Breakeven exit defended capital."
            else:
                lesson = f"Manual exit ({realized_pnl:+.2f}): Exited before SL/TP."

        record = {
            "trade_id": trade_id,
            "timestamp": time.time(),
            "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "realized_pnl": round(realized_pnl, 2),
            "exit_reason": exit_reason,
            "is_win": is_win,
            "strategy_mode": strategy_mode,
            "metrics_provenance": metrics_provenance,
            "entry_metrics": {
                "hurst": hurst,
                "obi": obi,
                "cvd_15m": cvd,
                "session": session,
                "robust_z": entry_metrics.get("robust_z", 0.0),
            },
            "lesson": lesson,
        }

        self.memories.insert(0, record)
        if len(self.memories) > 100:
            self.memories = self.memories[:100]

        self._save_memories()
        return record

    def retrieve_relevant_lessons(
        self,
        current_side: str,
        current_session: str,
        current_hurst: float,
        limit: int = 3
    ) -> Dict[str, Any]:
        """
        Retrieves the most contextually relevant historical trade lessons.

        Only records with metrics_provenance == "live" (metrics actually
        measured at entry time) participate in similarity matching. Backfilled
        records are stored for audit but excluded, because matching on
        reconstructed metrics would let outcome labels masquerade as
        predictive features.
        """
        # Quarantine: never learn from backfilled / fabricated metrics.
        live_memories = [m for m in self.memories if m.get("metrics_provenance", "live") == "live"]
        if not live_memories:
            return {
                "has_memory": False,
                "lesson_summary": "Initial baseline session. Establishing episodic track record.",
                "top_lessons": [],
                "win_rate_similar": 0.0,
            }

        # Filter for similar setups (matching side or matching session/regime)
        matches = []
        for m in live_memories:
            score = 0
            if m.get("side") == current_side:
                score += 2
            if m.get("entry_metrics", {}).get("session") == current_session:
                score += 2
            m_hurst = m.get("entry_metrics", {}).get("hurst", 0.5)
            if abs(m_hurst - current_hurst) < 0.12:
                score += 1
            matches.append((score, m))

        matches.sort(key=lambda x: x[0], reverse=True)
        top_items = [item[1] for item in matches[:limit]]

        wins = sum(1 for m in top_items if m.get("is_win"))
        total = len(top_items)
        win_rate = round((wins / total) * 100.0, 1) if total > 0 else 0.0

        lessons = [m.get("lesson") for m in top_items if m.get("lesson")]
        summary = " | ".join(lessons[:2]) if lessons else "Historical patterns balanced."

        return {
            "has_memory": True,
            "lesson_summary": summary,
            "top_lessons": lessons,
            "win_rate_similar": win_rate,
            "total_memories_stored": len(live_memories),
        }

    def _load_memories(self) -> List[Dict[str, Any]]:
        if not self.memory_path.exists():
            return []
        try:
            with open(self.memory_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load episodic memories: {e}")
            return []

    def _save_memories(self):
        try:
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(self.memories, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save episodic memories: {e}")


if __name__ == "__main__":
    engine = EpisodicMemoryEngine()
    # Test recording a sample trade
    rec = engine.record_trade_post_mortem(
        trade_id="pos_test_123",
        side="LONG",
        entry_price=81250.0,
        exit_price=81650.0,
        realized_pnl=28.50,
        exit_reason="TAKE_PROFIT_1",
        entry_metrics={"hurst": 0.68, "obi": 0.42, "perp_cvd_15m": 35.0, "session_name": "NEW_YORK_CASH_OPEN"},
        duration_sec=1420.0
    )
    print("[TEST] Recorded Post-Mortem Lesson:")
    print(rec["lesson"])

    retrieved = engine.retrieve_relevant_lessons(
        current_side="LONG", current_session="NEW_YORK_CASH_OPEN", current_hurst=0.70
    )
    print("\n[TEST] Retrieved Memory Insights:")
    print("Lesson Summary:", retrieved["lesson_summary"])
    print("Win Rate Similar:", f"{retrieved['win_rate_similar']}%")
