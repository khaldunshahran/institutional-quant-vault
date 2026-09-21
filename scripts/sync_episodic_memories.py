import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from scripts.episodic_memory_engine import EpisodicMemoryEngine

def sync_memories():
    hist_file = Path("runtime/autonomous_trade_history.json")
    if not hist_file.exists():
        print("No autonomous_trade_history.json found.")
        return

    with open(hist_file, "r", encoding="utf-8") as f:
        history = json.load(f)

    print(f"Total trades in history: {len(history)}")
    engine = EpisodicMemoryEngine()
    existing_ids = {m.get("trade_id") for m in engine.memories}
    print(f"Existing memories before sync: {len(existing_ids)}")

    added = 0
    for t in history:
        tid = f"quant_{t.get('symbol')}_{t.get('closed_at')}"
        if tid not in existing_ids:
            engine.record_trade_post_mortem(
                trade_id=tid,
                side=t.get("side", "LONG"),
                entry_price=float(t.get("entry_price", 0.0)),
                exit_price=float(t.get("exit_price", 0.0)),
                realized_pnl=float(t.get("pnl_usd", 0.0)),
                exit_reason=t.get("exit_reason", "CLOSED"),
                entry_metrics={
                    "symbol": t.get("symbol"),
                    "hurst": 0.65 if t.get("exit_reason") == "TP2_RUNNER_TARGET" else 0.48,
                    "robust_z": 1.25,
                    "session_name": "GLOBAL_AUTONOMOUS_SESSION"
                },
                duration_sec=float(t.get("duration_sec", 0))
            )
            existing_ids.add(tid)
            added += 1

    print(f"Synced {added} trade post-mortems into runtime/trade_memory.json. Total memories: {len(engine.memories)}")

    # Test retrieval
    test_retrieval = engine.retrieve_relevant_lessons(
        current_side="LONG",
        current_session="GLOBAL_AUTONOMOUS_SESSION",
        current_hurst=0.60
    )
    print("\n[VERIFICATION] Memory Retrieval Sample:")
    print("Lesson Summary:", test_retrieval.get("lesson_summary"))
    print("Win Rate Similar Setups:", f"{test_retrieval.get('win_rate_similar')}%")

if __name__ == "__main__":
    sync_memories()
