"""
Paper Fill Simulator — the single source of truth for paper execution.

Every paper fill (entry or exit) goes through here so the experiment measures
one consistent, documented, conservative execution model:

- MAKER (post-only limit, entries and TP orders) is a WORKING ORDER, not an
  instant outcome. place_maker_order() rests the order; poll_maker_order()
  checks it against REAL market activity observed AFTER placement (Binance
  aggTrades with timestamps strictly greater than the placement timestamp).
  Pre-placement price action can NEVER fill an order — that was the old
  candle-proxy flaw, and it is gone. Fills require trade-through with
  back-of-queue volume-share logic. Partial fills are banked incrementally;
  unfilled remainder expires after max_wait_sec or is cancelled.
- TAKER (stop-market SL/BE exits, manual/Telegram closes): fills immediately
  at the touch plus half-spread slippage and a size-impact term.
- Fees: 0.015% maker / 0.045% taker (single constants — no more conflicting
  fee assumptions across modules).
- Funding: flat 0.01% per 8h interval on open notional (conservative estimate;
  the live funding rate is not fetched).

Documented limitations (not hidden):
- Queue modeling is an estimate (10% volume share, back of queue); a real
  matching engine is not replicated.
- Funding uses a flat estimate, not the live Binance funding rate.
- Network fetchers are injectable (book_fetcher, trades_fetcher,
  filters_fetcher) so tests run deterministically without network.

Paper trading only. This module never touches real money.
"""

import json
import math
import time
import logging
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger(__name__)

# --- Single source of truth for paper cost assumptions ---
MAKER_FEE_RATE = 0.00015          # 0.015% post-only maker
TAKER_FEE_RATE = 0.00045          # 0.045% taker
FUNDING_RATE_PER_INTERVAL = 0.0001  # 0.01% per 8h funding interval (flat estimate)
VOLUME_SHARE = 0.10              # back-of-queue: we capture 10% of volume at our level
DEFAULT_MAKER_WAIT_SEC = 120     # resting entry orders expire after 120s
# Pullback-discount entry (Sep-2026 research, E2 variant): rest the post-only
# entry limit 0.50 x ATR(14) off the signal price (below for LONG, above for
# SHORT) and let it work up to 1h. The replay showed the edge needs BOTH the
# discount AND the patience: short windows made the discount variant worse
# than baseline. Unfilled orders expire -> no trade (no chasing).
ENTRY_DISCOUNT_ATR_MULT = 0.5
ENTRY_MAKER_WAIT_SEC = 3600
TP_WORKING_WAIT_SEC = 900        # TP limit orders work up to 15 min (abandoned earlier on failure)
DEGRADED_TAKER_SLIPPAGE_MULT = 3.0  # slippage multiplier when book is unavailable

FAPI_BASE = "https://fapi.binance.com"


def _http_get_json(url: str, timeout: float = 6.0) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"[FILL-SIM] GET failed {url}: {e}")
        return None


class PaperFillSimulator:
    # Terminal working orders (FILLED/EXPIRED/CANCELLED) are retained this
    # long for post-terminal polls, then pruned to bound memory on 24/7
    # runs. The trader polls every second and consumes terminal states
    # immediately, so an hour is ample.
    TERMINAL_ORDER_RETENTION_SEC = 3600

    def __init__(
        self,
        cache_dir: str = "runtime",
        book_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        trades_fetcher: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
        filters_fetcher: Optional[Callable[[str], Optional[Dict[str, float]]]] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.filters_cache_file = self.cache_dir / "exchange_info_cache.json"
        self._filters_mem: Dict[str, Dict[str, float]] = {}
        self._book_fetcher = book_fetcher or self._fetch_book
        self._trades_fetcher = trades_fetcher or self._fetch_trades
        self._filters_fetcher = filters_fetcher or self._fetch_filters
        # Working maker orders: order_id -> order state. Orders only ever
        # fill on market activity observed AFTER their placement timestamp.
        self._working_orders: Dict[str, Dict[str, Any]] = {}
        self._order_seq = 0

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    def _fetch_book(self, symbol: str) -> Optional[Dict[str, Any]]:
        data = _http_get_json(f"{FAPI_BASE}/fapi/v1/depth?symbol={symbol}&limit=20")
        if not data or not data.get("bids") or not data.get("asks"):
            return None
        bids = [(float(p), float(q)) for p, q in data["bids"]]
        asks = [(float(p), float(q)) for p, q in data["asks"]]
        return {
            "best_bid": bids[0][0],
            "best_ask": asks[0][0],
            "bids": bids,
            "asks": asks,
        }

    def _fetch_trades(self, symbol: str, start_time_ms: int) -> List[Dict[str, Any]]:
        """Real trades printed AFTER start_time_ms (aggTrades, time-ordered).

        Each item: {"a": agg_trade_id, "price": float, "qty": float,
        "ts_ms": int}. This is the honesty anchor: a working order can only
        be filled by trades that happened after it was placed.
        """
        data = _http_get_json(
            f"{FAPI_BASE}/fapi/v1/aggTrades?symbol={symbol}&startTime={start_time_ms}&limit=1000"
        )
        if not data:
            return []
        out = []
        for t in data:
            try:
                out.append({
                    "a": int(t["a"]),
                    "price": float(t["p"]),
                    "qty": float(t["q"]),
                    "ts_ms": int(t["T"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _fetch_filters(self, symbol: str) -> Optional[Dict[str, float]]:
        """tickSize / stepSize from exchangeInfo, cached 7 days."""
        if symbol in self._filters_mem:
            return self._filters_mem[symbol]
        cached = {}
        try:
            if self.filters_cache_file.exists():
                cached = json.loads(self.filters_cache_file.read_text(encoding="utf-8"))
                entry = cached.get(symbol)
                if entry and (time.time() - entry.get("cached_at", 0)) < 7 * 86400:
                    self._filters_mem[symbol] = entry["filters"]
                    return entry["filters"]
        except Exception:
            pass
        data = _http_get_json(f"{FAPI_BASE}/fapi/v1/exchangeInfo?symbol={symbol}")
        try:
            f = data["symbols"][0]["filters"]
            tick = next(x["tickSize"] for x in f if x["filterType"] == "PRICE_FILTER")
            step = next(x["stepSize"] for x in f if x["filterType"] == "LOT_SIZE")
            min_notional = next((x.get("notional") or x.get("minNotional") for x in f if x["filterType"] == "MIN_NOTIONAL"), 5.0)
            filters = {"tick_size": float(tick), "step_size": float(step), "min_notional": float(min_notional or 5.0)}
        except Exception as e:
            logger.warning(f"[FILL-SIM] exchangeInfo failed for {symbol}: {e}")
            return None
        self._filters_mem[symbol] = filters
        try:
            cached[symbol] = {"cached_at": time.time(), "filters": filters}
            self.filters_cache_file.write_text(json.dumps(cached), encoding="utf-8")
        except Exception:
            pass
        return filters

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return math.floor(value / step) * step

    @staticmethod
    def compute_fee(notional_usd: float, is_maker: bool) -> float:
        rate = MAKER_FEE_RATE if is_maker else TAKER_FEE_RATE
        return round(notional_usd * rate, 4)

    @staticmethod
    def compute_funding(notional_usd: float) -> float:
        """Flat conservative funding charge per 8h interval."""
        return round(notional_usd * FUNDING_RATE_PER_INTERVAL, 4)

    # ------------------------------------------------------------------
    # MAKER (post-only) working orders
    # ------------------------------------------------------------------
    def place_maker_order(
        self,
        symbol: str,
        side: str,          # "BUY" or "SELL"
        quantity: float,
        limit_price: float,
        max_wait_sec: int = DEFAULT_MAKER_WAIT_SEC,
    ) -> Dict[str, Any]:
        """Rest a post-only limit order. Returns WORKING (with order_id) or
        REJECTED. Fills are discovered later via poll_maker_order(), which
        only considers trades printed AFTER placement."""
        side = side.upper()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        now_ms = int(time.time() * 1000)

        filters = self._filters_fetcher(symbol)
        if not filters:
            return self._rejected(symbol, side, quantity, limit_price,
                                 "filters_unavailable: cannot size to exchange precision", now_utc)
        tick, step = filters["tick_size"], filters["step_size"]
        limit_price = self._round_to_step(limit_price, tick)
        quantity = self._round_to_step(quantity, step)
        if quantity <= 0:
            return self._rejected(symbol, side, quantity, limit_price,
                                 "quantity_below_step_size", now_utc)

        book = self._book_fetcher(symbol)
        if not book:
            return self._rejected(symbol, side, quantity, limit_price,
                                 "order_book_unavailable", now_utc)
        best_bid, best_ask = book["best_bid"], book["best_ask"]

        # Post-only (GTX) check: a real post-only order that would cross the
        # touch is rejected by the exchange.
        if side == "BUY" and limit_price >= best_ask:
            return self._rejected(symbol, side, quantity, limit_price,
                                 f"post_only_would_cross: limit {limit_price} >= ask {best_ask}", now_utc)
        if side == "SELL" and limit_price <= best_bid:
            return self._rejected(symbol, side, quantity, limit_price,
                                 f"post_only_would_cross: limit {limit_price} <= bid {best_bid}", now_utc)

        # Queue ahead of us at our price level (we join the back of the queue).
        levels = book["bids"] if side == "BUY" else book["asks"]
        queue_ahead_usd = 0.0
        for p, q in levels:
            if (side == "BUY" and p >= limit_price) or (side == "SELL" and p <= limit_price):
                queue_ahead_usd += p * q

        self._order_seq += 1
        order_id = f"paper_{now_ms}_{self._order_seq}"
        self._working_orders[order_id] = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "limit_price": limit_price,
            "quantity": quantity,
            "step": step,
            "tick": tick,
            "filled_qty": 0.0,
            "remaining_qty": quantity,
            "queue_ahead_usd": queue_ahead_usd,
            "placed_ts_ms": now_ms,
            "max_wait_sec": max_wait_sec,
            "last_agg_id": -1,
            "last_poll_ts_ms": 0,
            "status": "WORKING",
            "reason": None,
        }
        # Bound memory on 24/7 runs: prune terminal orders older than the
        # retention window. Pruning on placement (not on poll) guarantees a
        # terminal state stays pollable through its retention period.
        cutoff_ms = now_ms - self.TERMINAL_ORDER_RETENTION_SEC * 1000
        for oid in [oid for oid, o in self._working_orders.items()
                    if o["status"] in ("FILLED", "EXPIRED", "CANCELLED")
                    and o.get("terminal_ts_ms", 0) < cutoff_ms]:
            del self._working_orders[oid]
        return {
            "status": "WORKING", "order_id": order_id, "symbol": symbol, "side": side,
            "limit_price": limit_price, "requested_qty": quantity,
            "filled_qty": 0.0, "remaining_qty": quantity,
            "new_filled_qty": 0.0, "new_fee_usd": 0.0,
            "avg_price": limit_price, "is_maker": True,
            "reason": None, "timestamp_utc": now_utc,
        }

    def poll_maker_order(self, order_id: str) -> Dict[str, Any]:
        """Check a working order against real post-placement trades.

        Only trades with agg id greater than the last one already processed
        AND timestamp strictly after placement can fill the order. Returns
        the cumulative state plus the incremental fill since the last poll
        (new_filled_qty / new_fee_usd), so callers can bank partial fills.
        """
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        now_ms = int(time.time() * 1000)
        order = self._working_orders.get(order_id)
        if order is None:
            return {"status": "UNKNOWN", "order_id": order_id,
                    "filled_qty": 0.0, "remaining_qty": 0.0,
                    "new_filled_qty": 0.0, "new_fee_usd": 0.0,
                    "reason": "unknown_order_id", "timestamp_utc": now_utc}
        if order["status"] in ("FILLED", "EXPIRED", "CANCELLED"):
            snap = self._order_snapshot(order, 0.0, 0.0, now_utc)
            return snap

        # Throttle network polls to ~1/sec per order; callers tick every second.
        # (Tests reset last_poll_ts_ms to 0 to poll deterministically.)
        if now_ms - order["last_poll_ts_ms"] < 900:
            return self._order_snapshot(order, 0.0, 0.0, now_utc)
        order["last_poll_ts_ms"] = now_ms

        new_fill = 0.0
        try:
            trades = self._trades_fetcher(order["symbol"], order["placed_ts_ms"])
        except Exception as e:
            logger.warning(f"[FILL-SIM] trades fetch failed for {order['symbol']}: {e}")
            return self._order_snapshot(order, 0.0, 0.0, now_utc)

        limit = order["limit_price"]
        step = order["step"]
        last_a = order["last_agg_id"]
        max_a = last_a
        for t in sorted(trades, key=lambda x: (x.get("ts_ms", 0), x.get("a", 0))):
            a = int(t.get("a", -1))
            if a <= last_a:
                continue  # already processed on an earlier poll
            max_a = max(max_a, a)
            if int(t.get("ts_ms", 0)) <= order["placed_ts_ms"]:
                continue  # honesty anchor: pre-placement activity never fills
            px = float(t.get("price", 0.0))
            qty = float(t.get("qty", 0.0))
            if px <= 0 or qty <= 0:
                continue
            # Trade-through required: a buy limit fills only on prints at or
            # below our bid; a sell limit only on prints at or above our ask.
            hit = (px <= limit) if order["side"] == "BUY" else (px >= limit)
            if not hit:
                continue
            accessible_usd = px * qty * VOLUME_SHARE
            # Queue ahead depletes first; we only get what is left.
            if order["queue_ahead_usd"] > 0:
                absorbed = min(order["queue_ahead_usd"], accessible_usd)
                order["queue_ahead_usd"] -= absorbed
                accessible_usd -= absorbed
            fill_qty = min(order["remaining_qty"], accessible_usd / limit)
            if fill_qty > 0:
                new_fill += fill_qty
                order["remaining_qty"] -= fill_qty
        order["last_agg_id"] = max_a
        order["filled_qty"] = order["quantity"] - order["remaining_qty"]
        if order["remaining_qty"] < 1e-12:
            order["remaining_qty"] = 0.0
            order["filled_qty"] = order["quantity"]
            order["status"] = "FILLED"
            order["terminal_ts_ms"] = now_ms
        elif now_ms - order["placed_ts_ms"] >= order["max_wait_sec"] * 1000:
            order["status"] = "EXPIRED"
            order["terminal_ts_ms"] = now_ms
            order["reason"] = (f"no_fill_within_{order['max_wait_sec']}s: "
                               "no post-placement trade-through with volume")
        elif new_fill > 0:
            order["status"] = "PARTIAL"

        new_fill = self._round_to_step(new_fill, step)
        new_fee = self.compute_fee(round(new_fill * limit, 2), is_maker=True)
        return self._order_snapshot(order, new_fill, new_fee, now_utc)

    def cancel_maker_order(self, order_id: str) -> Dict[str, Any]:
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        now_ms = int(time.time() * 1000)
        order = self._working_orders.get(order_id)
        if order is None:
            return {"status": "UNKNOWN", "order_id": order_id,
                    "reason": "unknown_order_id", "timestamp_utc": now_utc}
        if order["status"] == "WORKING" or order["status"] == "PARTIAL":
            order["status"] = "CANCELLED"
            order["terminal_ts_ms"] = now_ms
            order["reason"] = "cancelled_by_caller"
        return self._order_snapshot(order, 0.0, 0.0, now_utc)

    def _order_snapshot(self, order, new_filled_qty, new_fee_usd, now_utc):
        return {
            "status": order["status"], "order_id": order["order_id"],
            "symbol": order["symbol"], "side": order["side"],
            "limit_price": order["limit_price"],
            "requested_qty": order["quantity"],
            "filled_qty": round(order["filled_qty"], 8),
            "remaining_qty": round(order["remaining_qty"], 8),
            "new_filled_qty": new_filled_qty, "new_fee_usd": new_fee_usd,
            "avg_price": order["limit_price"], "is_maker": True,
            "reason": order.get("reason"), "timestamp_utc": now_utc,
        }

    def _rejected(self, symbol, side, quantity, limit_price, reason, now_utc):
        return {
            "status": "REJECTED", "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": 0.0, "remaining_qty": quantity,
            "new_filled_qty": 0.0, "new_fee_usd": 0.0,
            "limit_price": limit_price, "avg_price": limit_price,
            "is_maker": True, "reason": reason,
            "timestamp_utc": now_utc,
        }

    # ------------------------------------------------------------------
    # TAKER simulation (stop-market / manual exits)
    # ------------------------------------------------------------------
    def simulate_taker_fill(
        self,
        symbol: str,
        side: str,          # "BUY" or "SELL"
        quantity: float,
        reference_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Immediate fill at the touch plus slippage. Always fills (or fails
        loudly only if there is no price at all)."""
        side = side.upper()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        filters = self._filters_fetcher(symbol)
        tick = filters["tick_size"] if filters else 0.0
        step = filters["step_size"] if filters else 0.0
        quantity = self._round_to_step(quantity, step) if step else quantity
        if quantity <= 0:
            return {"status": "REJECTED", "symbol": symbol, "side": side,
                    "filled_qty": 0.0, "avg_price": 0.0, "fee_usd": 0.0,
                    "is_maker": False, "reason": "quantity_below_step_size",
                    "degraded": True, "timestamp_utc": now_utc}

        book = self._book_fetcher(symbol)
        degraded = False
        if book and book.get("best_bid") and book.get("best_ask"):
            best_bid, best_ask = book["best_bid"], book["best_ask"]
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0
        elif reference_price:
            # Fail-open for protective exits: we must get out. Penalize with
            # 3x slippage and flag degraded.
            degraded = True
            mid = reference_price
            spread = reference_price * 0.0005  # assume 5 bps spread when blind
            best_bid, best_ask = mid - spread / 2, mid + spread / 2
            logger.error(f"[FILL-SIM] Taker fill on {symbol} with NO order book — using reference price with penalty.")
        else:
            return {"status": "REJECTED", "symbol": symbol, "side": side,
                    "filled_qty": 0.0, "avg_price": 0.0, "fee_usd": 0.0,
                    "is_maker": False, "reason": "no_price_available",
                    "degraded": True, "timestamp_utc": now_utc}

        notional_est = quantity * mid
        # Slippage: cross half the spread + size impact (conservative).
        impact = spread * 0.10 * min(1.0, notional_est / 100_000.0)
        slip_mult = DEGRADED_TAKER_SLIPPAGE_MULT if degraded else 1.0
        if side == "BUY":
            # lifting the ask
            fill_price = best_ask + (spread / 2.0 + impact) * slip_mult
        else:
            # hitting the bid
            fill_price = best_bid - (spread / 2.0 + impact) * slip_mult
        if tick:
            fill_price = self._round_to_step(fill_price, tick)
        fill_price = max(fill_price, tick or 0.0)

        notional = round(quantity * fill_price, 2)
        return {
            "status": "FILLED", "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": quantity, "avg_price": fill_price,
            "notional_usd": notional,
            "fee_usd": self.compute_fee(notional, is_maker=False), "is_maker": False,
            "slippage_usd_per_unit": round(abs(fill_price - mid), 6),
            "reason": None, "degraded": degraded, "timestamp_utc": now_utc,
        }

    # ------------------------------------------------------------------
    # Honest break-even: the runner exit must make the TOTAL trade net >= 0.
    # ------------------------------------------------------------------
    @staticmethod
    def fee_protected_break_even(
        entry_avg_price: float,
        quantity: float,
        is_long: bool,
        sunk_cost_usd: float,
        taker_slippage_bps: float = 2.5,
    ) -> float:
        """Total-trade break-even stop for the remaining quantity.

        sunk_cost_usd is the net amount the runner exit must recover for the
        WHOLE trade (all banked legs + entry fee + funding + this exit) to
        net >= 0. The trader passes -(realized_pnl_usd booked so far):
          - before any scale-out: realized = -entry_fee - funding, so
            sunk = entry_fee + funding (the classic break-even);
          - after a banked TP1: realized = banked - entry_fee - funding, so
            sunk = entry_fee + funding - banked, which can go NEGATIVE —
            the honest "risk-free" level then sits BELOW entry for a long
            (the banked profit already paid the costs).

        taker_slippage_bps is a conservative allowance for the taker exit's
        half-spread + impact (the old formula covered the taker fee but not
        slippage, so "nets >= 0" was optimistic by ~2.5bps on BTC).

        LONG exits by selling:
            P*qty*(1 - taker_fee - slip) - entry_avg*qty >= sunk
        SHORT exits by buying:
            entry_avg*qty - P*qty*(1 + taker_fee + slip) >= sunk
        """
        qty = max(quantity, 1e-12)
        slip = max(taker_slippage_bps, 0.0) / 10000.0
        if is_long:
            denom = qty * (1.0 - TAKER_FEE_RATE - slip)
            be = (entry_avg_price * qty + sunk_cost_usd) / denom if denom > 0 else entry_avg_price
            # Round UP: the stop must guarantee net >= 0, never below it.
            be = math.ceil(be * 1e8) / 1e8
        else:
            denom = qty * (1.0 + TAKER_FEE_RATE + slip)
            be = (entry_avg_price * qty - sunk_cost_usd) / denom if denom > 0 else entry_avg_price
            # Round DOWN: for a short, a lower exit price is the safe direction.
            be = math.floor(be * 1e8) / 1e8
        return be
