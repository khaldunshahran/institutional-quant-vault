"""
Paper-only Binance execution adapter.

Live trading is PERMANENTLY DISABLED in this build. There is no
confirmation flag, no environment variable, and no credential combination
that enables live orders: the adapter refuses mode="live" unconditionally
at construction, at set_mode(), and at the order-execution boundary.
The paper fill simulator is the only execution path.
"""

import time
import json
from pathlib import Path
from typing import Dict, Any, Optional

try:
    from scripts.paper_fill_simulator import PaperFillSimulator
except ImportError:  # pragma: no cover - direct script execution
    from paper_fill_simulator import PaperFillSimulator

class BinanceExecutionAdapter:
    # Live trading is permanently disabled: no confirmation value exists,
    # on purpose. Any code path that requests live execution is refused.

    def __init__(self, mode: str = "paper", ledger_path: str = "runtime/binance_orders.json"):
        requested = mode.lower()
        if requested == "live":
            raise RuntimeError(
                "LIVE mode is PERMANENTLY DISABLED in this build: this system "
                "runs paper trading only. No confirmation flag, environment "
                "variable, or API credential can enable live orders. Refusing "
                "to construct a live adapter — paper mode stays active."
            )
        if requested != "paper":
            raise RuntimeError(
                f"Unknown execution mode {mode!r}: this build supports 'paper' only."
            )
        self.mode = "paper"
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        # Paper fill simulator: the single execution model. There is no
        # live order path in this build.
        self.fill_simulator = PaperFillSimulator(cache_dir=str(self.ledger_path.parent))
        
        # Initialize ledger if not exists
        if not self.ledger_path.exists():
            self._save_ledger({
                "mode": self.mode,
                "starting_balance": 1000.0,
                "current_balance": 1000.0,
                "orders": []
            })

    def _save_ledger(self, data: Dict[str, Any]) -> None:
        try:
            self.ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _read_ledger(self) -> Dict[str, Any]:
        try:
            if self.ledger_path.exists():
                data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"mode": self.mode, "starting_balance": 1000.0, "current_balance": 1000.0, "orders": []}

    def get_status(self) -> Dict[str, Any]:
        ledger = self._read_ledger()
        return {
            "mode": self.mode.upper(),
            "is_live": False,  # live trading is permanently disabled
            "paper_balance_usd": ledger.get("current_balance", 1000.0),
            "total_orders": len(ledger.get("orders", [])),
        }

    def set_mode(self, mode: str) -> Dict[str, Any]:
        target = mode.lower()
        if target == "live":
            return {
                "success": False,
                "error": ("LIVE trading is PERMANENTLY DISABLED in this build "
                          "(paper trading only). No confirmation flag, environment "
                          "variable, or API credential can enable it. Paper mode "
                          "stays active — no live orders can be sent."),
            }
        if target != "paper":
            return {
                "success": False,
                "error": f"Unknown mode {mode!r}: this build supports 'paper' only.",
            }
        self.mode = "paper"
        ledger = self._read_ledger()
        ledger["mode"] = self.mode
        self._save_ledger(ledger)
        return {"success": True, "mode": self.mode.upper()}

    def execute_order(
        self,
        symbol: str,
        side: str, # "BUY" or "SELL"
        quantity: float,
        price: float,
        order_type: str = "LIMIT_MAKER",
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes an order on the specified symbol in Paper or Live mode.
        Uses LIMIT_MAKER / post-only (GTX) for 67% fee reduction (0.015%).
        """
        side = side.upper()
        now = time.time()
        order_id = client_order_id or f"jev_{int(now*1000)}"

        # 1. PAPER EXECUTION — routed through the realistic fill simulator.
        # Post-only maker orders are WORKING orders: they rest on the book
        # and fill only on real post-placement market activity (checked via
        # poll_maker_order by the trader loop). A single immediate poll is
        # done here so synchronous callers (dashboard manual orders) get a
        # truthful status; it is almost always WORKING at this point.
        if self.mode != "live":
            if order_type == "LIMIT_MAKER":
                placed = self.fill_simulator.place_maker_order(
                    symbol, side, quantity, price
                )
                if placed["status"] != "WORKING":
                    return {
                        "success": False,
                        "error": f"Paper fill {placed['status']}: {placed.get('reason')}",
                        "order": {
                            "order_id": order_id,
                            "symbol": symbol,
                            "side": side,
                            "status": placed["status"],
                            "reason": placed.get("reason"),
                            "execution_mode": "PAPER",
                            "timestamp": now,
                            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
                        },
                    }
                # One immediate honesty-preserving poll (only post-placement
                # trades can fill; usually none exist yet -> WORKING).
                try:
                    fill = self.fill_simulator.poll_maker_order(placed["order_id"])
                except Exception:
                    fill = placed
                fill_result = {
                    "order_id": order_id,
                    "paper_order_id": placed["order_id"],
                    "symbol": symbol,
                    "side": side,
                    "status": fill["status"],  # WORKING / PARTIAL / FILLED
                    "price": fill["limit_price"],
                    "quantity": fill["filled_qty"],
                    "requested_quantity": fill["requested_qty"],
                    "notional_usd": round(fill["filled_qty"] * fill["limit_price"], 2),
                    "fee_usd": float(fill.get("new_fee_usd", 0.0)),
                    "fee_rate": "0.015% (Maker)",
                    "execution_mode": "PAPER",
                    "note": ("Working post-only order: fills are discovered by "
                             "polling poll_maker_order against post-placement trades."),
                    "timestamp": now,
                    "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
                }
            else:
                fill = self.fill_simulator.simulate_taker_fill(
                    symbol, side, quantity
                )
                if fill["status"] in ("REJECTED",) or fill["filled_qty"] <= 0:
                    return {
                        "success": False,
                        "error": f"Paper fill {fill['status']}: {fill.get('reason')}",
                        "order": {
                            "order_id": order_id,
                            "symbol": symbol,
                            "side": side,
                            "status": fill["status"],
                            "reason": fill.get("reason"),
                            "execution_mode": "PAPER",
                            "timestamp": now,
                            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
                        },
                    }
                fill_result = {
                    "order_id": order_id,
                    "symbol": symbol,
                    "side": side,
                    "status": fill["status"],
                    "price": fill["avg_price"],
                    "quantity": fill["filled_qty"],
                    "requested_quantity": fill["requested_qty"],
                    "notional_usd": fill["notional_usd"],
                    "fee_usd": fill["fee_usd"],
                    "fee_rate": "0.045% (Taker)",
                    "execution_mode": "PAPER",
                    "timestamp": now,
                    "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
                }
            # Record in paper ledger
            ledger = self._read_ledger()
            orders = ledger.get("orders", [])
            orders.append(fill_result)
            ledger["orders"] = orders[-100:] # Keep last 100
            self._save_ledger(ledger)
            return {"success": True, "order": fill_result}

        # 2. LIVE EXECUTION — PERMANENTLY DISABLED.
        # mode can never be "live" (construction and set_mode() refuse it),
        # but if a live mode were ever smuggled onto this instance by hand,
        # the order boundary still refuses instead of signing a real
        # request. There is no live order code left in this build.
        return {
            "success": False,
            "error": ("LIVE trading is PERMANENTLY DISABLED in this build "
                      "(paper trading only). No order was sent."),
        }

if __name__ == "__main__":
    adapter = BinanceExecutionAdapter(mode="paper")
    print("Binance Adapter Status:", adapter.get_status())
    
    # Test paper order on SOLUSDT
    test_fill = adapter.execute_order("SOLUSDT", "BUY", quantity=2.5, price=112.40)
    print("Paper Order Fill Result:", json.dumps(test_fill, indent=2))
