# Batch 4 — integrity notes

Paper trading only. There is no live-trading code path in this repository:
`BinanceExecutionAdapter` rejects `mode="live"` unconditionally at
construction and at the execution boundary, and `set_mode("live")` refuses.
No demonstrated edge exists (493 paper trades, -$9,162 net, PF 0.55 as of
2026-09-21). Nothing here is a profitability claim.

## 1. Ledger-first transaction ordering

The PnL ledger (`runtime/pnl_ledger.jsonl`) is the book of record. Every
economic event (ENTRY, TP1/TP2/SL/BE_STOP/MANUAL legs, FUNDING) is appended
**before** any in-memory position state advances:

- `_record_fill_event()` returns `True` only when the append is durable.
  On failure it returns `False` and leaves `daily_pnl_usd` untouched.
- `_apply_exit_leg()`, `_accrue_funding()`, and `_create_position()` raise
  `LedgerWriteError` on a failed append instead of diverging silently.
- A failed exit-leg write releases the `_closing` claim so the close can be
  retried next tick — a stuck claim would make the position uncloseable.
- A failed ENTRY write keeps the pending entry: the next poll retries the
  finalize; the fill is never lost and never double-booked.
- A failed FUNDING write does not stamp the window: the charge retries next
  tick, exactly once per window.
- `_record_closed_trade()` returns `True`/`False`. History is a secondary
  index: if it cannot be written, the close (already in the ledger) still
  completes, loudly, counted as `history_write_failures` in `get_status()`.

Failure-injection tests in `tests/test_batch4_integrity.py` pin all of this.

## 2. Price-feed degradation

- Per-tick management uses the lightweight futures ticker with a 2s shared
  cache (no 100-kline fetch per position per second in normal operation).
- During a ticker outage the heavy 100-kline fallback is rate-limited to
  one attempt per symbol per 60s (`HEAVY_FALLBACK_INTERVAL_SEC`). Between
  attempts the symbol is skipped, never managed from a request storm.
- 300 consecutive total-failure ticks trip one Telegram `DATA DEGRADED`
  alert. At 1 tick/second that is ~5 minutes of blind risk management.
- The ledger is reconciled every 300 ticks in-run; daily PnL is rebuilt
  from the ledger at UTC-midnight rollover (which runs in the loop, not
  only when the dashboard is opened).

## 3. Restart semantics

- Open positions are restored from `autonomous_positions.json`; a stale
  `_closing` claim is always cleared.
- TP1 working orders live in the memory-only simulator. After a restart,
  `poll_maker_order` returns `UNKNOWN`; the trader logs `TP1_ORDER_LOST`
  and re-places the original remainder (banked partials stay banked).
- Pending entry orders are persisted to `pending_entries.json` but **never
  restored as live orders** — their venue orders died with the process.
  Each is terminally logged as `ENTRY_ORDER_LOST_ON_RESTART` so the
  decision trail has no silent holes. A restart that drops live orders is
  experiment-invalidating for any fill-rate analysis covering that window.
- The paper fill simulator prunes terminal orders (FILLED/EXPIRED/
  CANCELLED) older than 1h so 24/7 runs cannot leak memory.

## 4. Experiment reproducibility

- The hunting universe is **frozen** by default (`freeze_universe=True`):
  `XAUUSDT, PAXGUSDT` + the top-20 list, fixed for the life of the process.
  A dynamic universe confounds attribution (signal vs. rotation). Set
  `freeze_universe=False` to let the universe scanner rotate the watchlist.
- The decision log (`runtime/decision_log.jsonl`) records every signal
  evaluation including rejections — the reproducible trail.
- Entry fills are modeled, not assumed: post-only (GTX) placement, queue
  position, and post-placement trade-through are simulated. There is no
  separate pre-trade spread/liquidity veto gate; wide spreads express
  themselves as unfilled/expired orders in the fill model. If a future
  experiment needs an explicit spread filter, add it as a signal gate (it
  changes strategy behavior, so it does not belong in an integrity batch).

## 5. Dashboard security posture

- `ui/server.py` binds to `127.0.0.1:5000` only and has **no
  authentication**. Anyone with local-machine access can hit mutating
  endpoints (`/close`, `/closeall`, mode controls). This is acceptable for
  a personal local dashboard; do not expose the port beyond loopback
  (the shipped `docker-compose.yml` publishes it on loopback only).
- Telegram commands require the sender to match the configured chat id.

## 6. Runtime evidence backup policy

The experiment's evidence lives in `runtime/` and is **not** backed up by
the application. The files that matter:

- `pnl_ledger.jsonl` — the book of record (append-only; never rewrite)
- `decision_log.jsonl` — every signal evaluation (append-only)
- `autonomous_trade_history.json` — closed-trade index
- `autonomous_positions.json`, `autonomous_state.json` — crash recovery

Recommended: copy `runtime/` off-machine daily (the ledger and decision
log are append-only, so incremental copy is cheap). Loss of `runtime/`
destroys the experiment's evidence; the code will not reconstruct it.
`runtime/*.quarantined-*` files are local-only and never committed.

## 7. Single-instance guard

`runtime/trader.pid` + a process-wide registry refuse a second trader on
the same runtime dir (cross-process via PID liveness, in-process via the
registry). `start()` unwinds the lock if worker-thread startup fails;
`stop()` releases it.
