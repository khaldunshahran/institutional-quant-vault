"""
Paper Fill Simulator — the single source of truth for paper execution.

Replaces the old "every order instantly fills at the requested price" model.
Every paper fill (entry or exit) goes through here so the experiment measures
one consistent, documented, conservative execution model:

- MAKER (post-only limit, entries and TP orders): rejects if the limit would
  cross the touch (a real GTX/post-only order would be rejected); otherwise
  the order rests and fills only on TRADE-THROUGH with back-of-queue
  volume-share logic. Partial fills are possible. Unfilled remainder expires
  after max_wait_sec. Fills are adverse by construction: a buy limit only
  fills when price trades down through it.
- TAKER (stop-market SL/BE exits, manual/Telegram closes): fills immediately
  at the touch plus half-spread slippage and a size-impact term.
- Fees: 0.015% maker / 0.045% taker (single constants — no more conflicting
  fee assumptions across modules).
- Funding: flat 0.01% per 8h interval on open notional (conservative estimate;
  the live funding rate is not fetched).

Documented limitations (not hidden):
- The resting-order fill path is simulated by walking forward through
  recently closed 1m candles as a proxy for the next N seconds. It is
  conservative (back of queue, 10% volume share, requires trade-through)
  but it is still a proxy, not a live order book.
- Funding uses a flat estimate, not the live Binance funding rate.
- Network fetchers are injectable (book_fetcher, klines_fetcher,
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
TP_WORKING_WAIT_SEC = 60         # TP limit orders re-evaluated each touch
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
    def __init__(
        self,
        cache_dir: str = "runtime",
        book_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        klines_fetcher: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
        filters_fetcher: Optional[Callable[[str], Optional[Dict[str, float]]]] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.filters_cache_file = self.cache_dir / "exchange_info_cache.json"
        self._filters_mem: Dict[str, Dict[str, float]] = {}
        self._book_fetcher = book_fetcher or self._fetch_book
        self._klines_fetcher = klines_fetcher or self._fetch_klines
        self._filters_fetcher = filters_fetcher or self._fetch_filters

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

    def _fetch_klines(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        data = _http_get_json(f"{FAPI_BASE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit={limit}")
        if not data:
            return []
        out = []
        for k in data:
            try:
                out.append({
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "quote_volume": float(k[7]),
                })
            except (IndexError, ValueError, TypeError):
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
    # MAKER (post-only) simulation
    # ------------------------------------------------------------------
    def simulate_maker_fill(
        self,
        symbol: str,
        side: str,          # "BUY" or "SELL"
        quantity: float,
        limit_price: float,
        max_wait_sec: int = DEFAULT_MAKER_WAIT_SEC,
    ) -> Dict[str, Any]:
        side = side.upper()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

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
        # touch is rejected by the exchange. No more "always fills".
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

        # Walk forward through recent 1m candles as a proxy fill path.
        n_candles = max(2, min(10, math.ceil(max_wait_sec / 60) + 1))
        candles = self._klines_fetcher(symbol, n_candles)
        remaining = quantity
        filled = 0.0
        for c in candles:
            if remaining <= 0:
                break
            # Trade-through required: buy fills only if price traded DOWN
            # through our bid; sell fills only if price traded UP through it.
            traded_through = (c["low"] <= limit_price) if side == "BUY" else (c["high"] >= limit_price)
            if not traded_through:
                continue
            accessible_usd = c["quote_volume"] * VOLUME_SHARE
            # Queue ahead depletes first; we only get what is left.
            if queue_ahead_usd > 0:
                absorbed = min(queue_ahead_usd, accessible_usd)
                queue_ahead_usd -= absorbed
                accessible_usd -= absorbed
            fill_qty = min(remaining, self._round_to_step(accessible_usd / limit_price, step))
            filled += fill_qty
            remaining = self._round_to_step(quantity - filled, step)

        filled = self._round_to_step(filled, step)
        if filled <= 0:
            return {
                "status": "EXPIRED", "symbol": symbol, "side": side,
                "requested_qty": quantity, "filled_qty": 0.0, "avg_price": limit_price,
                "fee_usd": 0.0, "is_maker": True,
                "reason": f"no_fill_within_{max_wait_sec}s: no trade-through with volume",
                "timestamp_utc": now_utc,
            }
        notional = round(filled * limit_price, 2)
        status = "FILLED" if remaining <= 0 else "PARTIAL"
        return {
            "status": status, "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": filled, "avg_price": limit_price,
            "notional_usd": notional,
            "fee_usd": self.compute_fee(notional, is_maker=True), "is_maker": True,
            "reason": None, "timestamp_utc": now_utc,
        }

    def _rejected(self, symbol, side, quantity, limit_price, reason, now_utc):
        return {
            "status": "REJECTED", "symbol": symbol, "side": side,
            "requested_qty": quantity, "filled_qty": 0.0, "avg_price": limit_price,
            "fee_usd": 0.0, "is_maker": True, "reason": reason,
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
    # Honest break-even: exit must cover entry fee + exit fee + funding.
    # ------------------------------------------------------------------
    @staticmethod
    def fee_protected_break_even(
        entry_avg_price: float,
        quantity: float,
        is_long: bool,
        entry_fee_usd: float,
        funding_paid_usd: float = 0.0,
    ) -> float:
        """Break-even stop where a taker exit nets >= 0 after all costs.

        LONG exits by selling:  P*qty*(1 - taker_fee) - entry_avg*qty - entry_fee - funding >= 0
        SHORT exits by buying:  entry_avg*qty - P*qty*(1 + taker_fee) - entry_fee - funding >= 0
        """
        qty = max(quantity, 1e-12)
        if is_long:
            total_cost = entry_avg_price * qty + entry_fee_usd + funding_paid_usd
            be = total_cost / (qty * (1.0 - TAKER_FEE_RATE))
            # Round UP: the stop must guarantee net >= 0, never below it.
            be = math.ceil(be * 1e8) / 1e8
        else:
            net_proceeds = entry_avg_price * qty - entry_fee_usd - funding_paid_usd
            be = net_proceeds / (qty * (1.0 + TAKER_FEE_RATE))
            # Round DOWN: for a short, a lower exit price is the safe direction.
            be = math.floor(be * 1e8) / 1e8
        return be
