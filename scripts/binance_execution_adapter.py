"""
Unified Binance Execution Adapter
Supports seamless dual-mode execution:
1. PAPER MODE: Zero-risk simulated execution with realistic 0.015% Maker rate and local ledger persistence.
2. LIVE MODE: Official Binance Futures/Spot execution via HMAC-SHA256 signed API requests.
"""

import os
import time
import hmac
import hashlib
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

class BinanceExecutionAdapter:
    def __init__(self, mode: str = "paper", ledger_path: str = "runtime/binance_orders.json"):
        self.mode = mode.lower() # "paper" or "live"
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load API keys from environment or .env
        self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
        self.api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        
        # Base URLs for Binance Futures
        self.futures_base_url = "https://fapi.binance.com"
        
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
                return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"mode": self.mode, "starting_balance": 1000.0, "current_balance": 1000.0, "orders": []}

    def has_live_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret and len(self.api_key) > 10 and len(self.api_secret) > 10)

    def get_status(self) -> Dict[str, Any]:
        ledger = self._read_ledger()
        return {
            "mode": self.mode.upper(),
            "is_live": self.mode == "live",
            "has_credentials": self.has_live_credentials(),
            "paper_balance_usd": ledger.get("current_balance", 1000.0),
            "total_orders": len(ledger.get("orders", []))
        }

    def set_mode(self, mode: str) -> Dict[str, Any]:
        target = mode.lower()
        if target == "live":
            if not self.has_live_credentials():
                return {
                    "success": False,
                    "error": "Cannot switch to LIVE: BINANCE_API_KEY and BINANCE_API_SECRET are missing from .env"
                }
        self.mode = target
        ledger = self._read_ledger()
        ledger["mode"] = self.mode
        self._save_ledger(ledger)
        return {"success": True, "mode": self.mode.upper()}

    def _sign_query(self, params: Dict[str, Any]) -> str:
        """Create HMAC-SHA256 signature for Binance Private REST API."""
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

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
        notional = round(quantity * price, 2)
        maker_fee = round(notional * 0.00015, 4) # 0.015% Maker rate
        now = time.time()
        order_id = client_order_id or f"jev_{int(now*1000)}"

        # 1. PAPER EXECUTION
        if self.mode != "live":
            fill_result = {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "status": "FILLED",
                "price": price,
                "quantity": quantity,
                "notional_usd": notional,
                "fee_usd": maker_fee,
                "fee_rate": "0.015% (Maker)",
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

        # 2. LIVE BINANCE EXECUTION
        if not self.has_live_credentials():
            return {
                "success": False,
                "error": "Live credentials missing in .env"
            }

        url = f"{self.futures_base_url}/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTX", # Post-Only for pure Maker fees
            "quantity": quantity,
            "price": price,
            "newClientOrderId": order_id,
            "timestamp": int(now * 1000)
        }

        signed_query = self._sign_query(params)
        req_url = f"{url}?{signed_query}"

        try:
            req = urllib.request.Request(
                req_url,
                method="POST",
                headers={
                    "X-MBX-APIKEY": self.api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "TypeSafe-Jev-Quant/2.0"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                live_result = {
                    "order_id": str(data.get("orderId", order_id)),
                    "client_order_id": data.get("clientOrderId", order_id),
                    "symbol": symbol,
                    "side": side,
                    "status": data.get("status", "NEW"),
                    "price": float(data.get("price", price)),
                    "quantity": float(data.get("origQty", quantity)),
                    "notional_usd": notional,
                    "fee_rate": "0.015% (Maker GTX)",
                    "execution_mode": "LIVE_BINANCE",
                    "timestamp": now,
                    "raw_response": data
                }
                return {"success": True, "order": live_result}
        except urllib.error.HTTPError as e:
            err_text = e.read().decode()
            return {"success": False, "error": f"Binance HTTP {e.code}: {err_text}"}
        except Exception as e:
            return {"success": False, "error": f"Network error: {str(e)}"}

if __name__ == "__main__":
    adapter = BinanceExecutionAdapter(mode="paper")
    print("Binance Adapter Status:", adapter.get_status())
    
    # Test paper order on SOLUSDT
    test_fill = adapter.execute_order("SOLUSDT", "BUY", quantity=2.5, price=112.40)
    print("Paper Order Fill Result:", json.dumps(test_fill, indent=2))
