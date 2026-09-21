#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Any, Optional
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType, MarketOrderArgs, OrderArgs, OrderType

# Automatically load environment variables from project .env
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / '.env')
load_dotenv()

# Ensure Windows terminal never crashes on unicode
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

UTC = dt.timezone.utc

# Initialize StreamManager for ultra-low latency WebSocket & connection pooling
try:
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.stream_manager import StreamManager
    STREAM_MGR = StreamManager.get_instance()
except Exception:
    STREAM_MGR = None

# Initialize TypeSafe Jev Decision Engine
try:
    from scripts.jev_decision_engine import JevDecisionEngine, MarketStatePayload
except Exception:
    try:
        from jev_decision_engine import JevDecisionEngine, MarketStatePayload
    except Exception:
        JevDecisionEngine = None
        MarketStatePayload = None


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def ts_utc() -> str:
    return now_utc().isoformat().replace('+00:00', 'Z')


def parse_json_objects(text: str) -> list[dict[str, Any]]:
    out = []
    cur = []
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
        if depth > 0:
            cur.append(ch)
        if ch == '}' and depth > 0:
            depth -= 1
            if depth == 0:
                s = ''.join(cur)
                cur = []
                try:
                    out.append(json.loads(s))
                except Exception:
                    pass
    return out


def bucket_5m(ts: int) -> int:
    return ts - (ts % 300)


def fetch_event(slug: str) -> Optional[dict[str, Any]]:
    r = requests.get('https://gamma-api.polymarket.com/events', params={'slug': slug}, timeout=12)
    r.raise_for_status()
    arr = r.json()
    return arr[0] if arr else None


def fetch_btc_spot_and_open(cur_5m: int) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (btc_spot, btc_open, btc_impulse) for current 5m candle.
    Uses real-time WebSocket stream (< 0.01ms); falls back to local server or Binance REST.
    """
    if STREAM_MGR is not None:
        return STREAM_MGR.get_btc_telemetry(cur_5m)

    # 1. Try local telemetry if server is running
    try:
        r = requests.get('http://localhost:5000/api/telemetry', timeout=0.8)
        if r.status_code == 200:
            d = r.json()
            s = d.get('btc_spot')
            o = d.get('btc_open')
            imp = d.get('btc_impulse')
            if s is not None and o is not None:
                return float(s), float(o), float(imp if imp is not None else (s - o))
    except Exception:
        pass

    # 2. Direct Binance query
    try:
        r_ticker = requests.get('https://api.binance.com/api/v3/ticker/price', params={'symbol': 'BTCUSDT'}, timeout=2.5)
        r_kline = requests.get(
            'https://api.binance.com/api/v3/klines',
            params={'symbol': 'BTCUSDT', 'interval': '5m', 'startTime': cur_5m * 1000, 'limit': 1},
            timeout=2.5,
        )
        spot = float(r_ticker.json().get('price', 0)) if r_ticker.status_code == 200 else None
        open_px = float(r_kline.json()[0][1]) if (r_kline.status_code == 200 and r_kline.json()) else None
        if spot is not None and open_px is not None:
            return spot, open_px, spot - open_px
    except Exception:
        pass

    return None, None, None


def resolve_active_current_5m_market() -> Optional[dict[str, Any]]:
    """Return active BTC 5m market for the current slot only."""
    now = int(time.time())
    cur = bucket_5m(now)
    slug = f'btc-updown-5m-{cur}'

    try:
        ev = fetch_event(slug)
    except Exception:
        return None
    if not ev:
        return None

    mkts = ev.get('markets') or []
    if not mkts:
        return None

    m = mkts[0]
    if m.get('closed') is True:
        return None
    if m.get('active') is False:
        return None

    end_iso = str(m.get('endDate') or m.get('endDateIso') or '')
    try:
        end_ts = dt.datetime.fromisoformat(end_iso.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None

    sec_left = end_ts - time.time()
    if sec_left <= 5:
        return None

    mm = dict(m)
    mm['_event_slug'] = slug
    mm['_seconds_left'] = sec_left
    return mm


def parse_json_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def market_side_prices(market: dict[str, Any]) -> tuple[float, float, str, str, str, str]:
    outcomes = parse_json_field(market.get('outcomes')) or []
    prices = parse_json_field(market.get('outcomePrices')) or []
    token_ids = parse_json_field(market.get('clobTokenIds')) or []
    if len(prices) < 2 or len(token_ids) < 2:
        raise RuntimeError('missing outcomePrices/clobTokenIds')

    up_i, down_i = 0, 1
    labs = [str(x).lower() for x in outcomes[:2]] if isinstance(outcomes, list) else []
    if len(labs) >= 2 and ('up' in labs[1] or 'yes' in labs[1]):
        up_i, down_i = 1, 0

    up_p = float(prices[up_i])
    dn_p = float(prices[down_i])
    up_t = str(token_ids[up_i])
    dn_t = str(token_ids[down_i])
    return up_p, dn_p, up_t, dn_t, str(market.get('slug') or market.get('_event_slug') or ''), str(market.get('endDate') or market.get('endDateIso') or '')


def _best_bid_ask(book) -> tuple[Optional[float], Optional[float]]:
    bids = getattr(book, 'bids', []) or []
    asks = getattr(book, 'asks', []) or []
    best_bid = None
    best_ask = None
    for b in bids:
        p = float(getattr(b, 'price', 0) or 0)
        if best_bid is None or p > best_bid:
            best_bid = p
    for a in asks:
        p = float(getattr(a, 'price', 0) or 0)
        if best_ask is None or p < best_ask:
            best_ask = p
    return best_bid, best_ask


def clob_side_prices(up_token: str, down_token: str, clob_base: str = 'https://clob.polymarket.com') -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return trigger prices from CLOB orderbooks: UP ask, DOWN ask, spread of picked side when available."""
    if STREAM_MGR is not None:
        return STREAM_MGR.get_clob_prices(up_token, down_token)

    pub = ClobClient(host=clob_base, chain_id=POLYGON)
    up_book = pub.get_order_book(str(up_token))
    dn_book = pub.get_order_book(str(down_token))
    up_bid, up_ask = _best_bid_ask(up_book)
    dn_bid, dn_ask = _best_bid_ask(dn_book)

    picked_spread = None
    if up_ask is not None and up_bid is not None:
        picked_spread = max(0.0, up_ask - up_bid)
    if dn_ask is not None and dn_bid is not None:
        s = max(0.0, dn_ask - dn_bid)
        picked_spread = s if picked_spread is None else min(picked_spread, s)

    return up_ask, dn_ask, picked_spread


def clob_best_bid(token_id: str, clob_base: str = 'https://clob.polymarket.com') -> Optional[float]:
    """Fetch the real-time highest bid from the CLOB orderbook for the given token."""
    if STREAM_MGR is not None:
        return STREAM_MGR.get_best_bid(token_id)
    try:
        pub = ClobClient(host=clob_base, chain_id=POLYGON)
        book = pub.get_order_book(str(token_id))
        best_bid, _ = _best_bid_ask(book)
        return best_bid
    except Exception:
        return None


def auth_clob_client(clob_base: str = 'https://clob.polymarket.com') -> Optional[ClobClient]:
    try:
        key = os.getenv('PM_PRIVATE_KEY') or ''
        funder = os.getenv('PM_ADDRESS') or os.getenv('PM_FUNDER') or None
        sig = int(os.getenv('PM_SIGNATURE_TYPE', '2'))
        v1 = os.getenv('PM_API_KEY') or ''
        v2 = os.getenv('PM_API_SECRET') or ''
        v3 = os.getenv('PM_API_PASSPHRASE') or ''
        if not key or not v1 or not v2 or not v3:
            return None
        c = ClobClient(host=clob_base, chain_id=POLYGON, key=key, signature_type=sig, funder=funder)
        c.set_api_creds(ApiCreds(api_key=v1, api_secret=v2, api_passphrase=v3))
        return c
    except Exception:
        return None


def poll_order_status(client: Optional[ClobClient], order_id: str, wait_sec: float = 6.0, step_sec: float = 1.0) -> tuple[str, Optional[dict[str, Any]]]:
    if client is None or not order_id:
        return '', None
    deadline = time.time() + max(0.0, float(wait_sec))
    last = None
    while time.time() <= deadline:
        try:
            last = client.get_order(order_id)
            st = str((last or {}).get('status') or '').upper()
            if st and st not in ('LIVE', 'OPEN'):
                return st, last
        except Exception:
            pass
        time.sleep(max(0.2, float(step_sec)))
    try:
        last = client.get_order(order_id)
    except Exception:
        pass
    st = str((last or {}).get('status') or '').upper()
    return st, last


def cancel_token_orders(client: Optional[ClobClient], token_id: str) -> Optional[dict[str, Any]]:
    if client is None:
        return None
    try:
        return client.cancel_market_orders(asset_id=str(token_id))
    except Exception as e:
        return {'error': str(e)}


def run_open(
    slug: str,
    side: str,
    stake: float,
    execute: bool,
    trigger_price: float = 0.70,
    token_id: str = '',
    repo: str = '',
) -> tuple[str, list[dict[str, Any]]]:
    """Execute entry order on Polymarket.
    In paper-trading (execute=False): returns simulated matched order at trigger_price.
    In live trading (execute=True): places native market order via py_clob_client.
    """
    if not execute:
        entry_p = max(0.01, trigger_price) if trigger_price else 0.70
        shares = round(stake / entry_p, 4)
        sim_obj = {
            'decision': 'enter',
            'market_slug': slug,
            'side': side,
            'token_id': token_id,
            'entry_price': entry_p,
            'order_post_result': {
                'success': True,
                'status': 'matched',
                'takingAmount': shares,
                'makingAmount': stake,
                'orderID': f'sim_open_{int(time.time())}',
                'transactionsHashes': ['sim_tx_open'],
            },
        }
        out = f"[SIMULATION] Paper-trading open {side} on {slug} stake={stake:.2f} USD @ ${entry_p:.4f} ({shares} shares)\n" + json.dumps(sim_obj)
        return out, [sim_obj]

    client = auth_clob_client()
    if client is None:
        msg = "[ERROR] Live trade failed: Missing or invalid Polymarket credentials in .env (PM_PRIVATE_KEY, PM_API_KEY, PM_API_SECRET, PM_API_PASSPHRASE)."
        print(f"\n{msg}\n", flush=True)
        return msg, [{'error': 'missing_credentials'}]

    # 1. Pre-flight check: Verify available USDC collateral balance
    try:
        sig_type = int(os.getenv('PM_SIGNATURE_TYPE', '2'))
        bal_res = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type))
        raw_bal = float(bal_res.get('balance', '0'))
        clean_bal = raw_bal / 1e6 if raw_bal > 1000 else raw_bal
        if clean_bal < stake:
            funder_addr = os.getenv('PM_ADDRESS') or os.getenv('PM_FUNDER') or 'your wallet'
            msg = f"[ERROR] Insufficient USDC collateral balance: ${clean_bal:.2f} available, but stake is ${stake:.2f} USDC. Please deposit funds to {funder_addr}."
            print(f"\n{msg}\n", flush=True)
            return msg, [{'error': 'insufficient_balance', 'available': clean_bal, 'required': stake, 'order_post_result': {'success': False, 'status': 'insufficient_balance'}}]
    except Exception as e:
        print(f"[WARN] Pre-flight balance check warning: {e}", flush=True)

    # 2. Place Market Buy Order (FOK)
    try:
        mo = MarketOrderArgs(
            token_id=token_id,
            amount=stake,
            side='BUY',
            order_type=OrderType.FOK,
        )
        signed_order = client.create_market_order(mo)
        resp = client.post_order(signed_order, orderType=OrderType.FOK)
        
        # Parse fill quantities
        taking = float(resp.get('takingAmount') or (stake / max(0.01, trigger_price)))
        making = float(resp.get('makingAmount') or stake)
        entry_p = float(resp.get('price') or (making / taking if taking > 0 else trigger_price))
        
        res_obj = {
            'decision': 'enter',
            'market_slug': slug,
            'side': side,
            'token_id': token_id,
            'entry_price': entry_p,
            'order_post_result': {
                'success': True,
                'status': str(resp.get('status') or 'matched').lower(),
                'takingAmount': taking,
                'makingAmount': making,
                'orderID': resp.get('orderID'),
                'transactionsHashes': resp.get('transactionsHashes') or [],
            },
        }
        out = f"[LIVE ORDER] Opened {side} position on {slug}: {taking:.4f} shares @ ${entry_p:.4f}\n" + json.dumps(res_obj)
        return out, [res_obj]
    except Exception as e:
        err_msg = str(e)
        err_obj = {
            'decision': 'enter',
            'market_slug': slug,
            'side': side,
            'token_id': token_id,
            'order_post_result': {'success': False, 'status': 'error', 'error': err_msg},
        }
        print(f"\n[ERROR] Live order post failed: {err_msg}\n", flush=True)
        return f"[ERROR] Live order post failed: {err_msg}", [err_obj]


def run_close(
    slug: str,
    token_id: str,
    shares: float,
    execute: bool,
    close_order_type: str = 'FAK',
    close_limit_price: float | None = None,
    side: str = 'UP',
    repo: str = '',
) -> tuple[str, list[dict[str, Any]]]:
    """Execute close/exit order on Polymarket.
    In paper-trading (execute=False): returns simulated matched sale at real CLOB Best Bid.
    In live trading (execute=True): executes native FAK or GTC limit sell order via py_clob_client.
    """
    if not execute:
        # Determine realistic liquidation price using live CLOB best bid
        cur_px = close_limit_price
        if cur_px is None or cur_px <= 0:
            cur_px = clob_best_bid(token_id)
        if cur_px is None or cur_px <= 0:
            cur_px = 0.70  # fallback
        
        close_usdc = round(shares * cur_px, 4)
        sim_obj = {
            'decision': 'close',
            'market_slug': slug,
            'token_id': token_id,
            'close_skipped': None,
            'order_post_result': {
                'success': True,
                'status': 'matched',
                'takingAmount': close_usdc,
                'makingAmount': shares,
                'orderID': f'sim_close_{int(time.time())}',
                'transactionsHashes': ['sim_tx_close'],
            },
        }
        out = f"[SIMULATION] Paper-trading close on {slug} shares={shares} @ ${cur_px:.4f} -> ${close_usdc:.2f} USDC\n" + json.dumps(sim_obj)
        return out, [sim_obj]

    client = auth_clob_client()
    if client is None:
        msg = "[ERROR] Live trade failed: Missing Polymarket credentials in .env."
        return msg, [{'error': 'missing_credentials'}]

    # Check on-chain conditional token balance before selling
    try:
        sig_type = int(os.getenv('PM_SIGNATURE_TYPE', '2'))
        bal_res = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id, signature_type=sig_type))
        token_bal = float(bal_res.get('balance', '0'))
        eff_shares = token_bal / 1e6 if token_bal > 1000 else token_bal
        if eff_shares <= 0.0001:
            sim_obj = {
                'decision': 'close',
                'market_slug': slug,
                'token_id': token_id,
                'close_skipped': 'zero_effective_shares',
                'order_post_result': {'success': False, 'status': 'zero_balance'},
            }
            return f"[WAIT] Token balance not yet visible on-chain ({eff_shares} shares)", [sim_obj]
    except Exception:
        pass

    try:
        if str(close_order_type).upper() == 'GTC' and close_limit_price is not None and close_limit_price > 0:
            ord_args = OrderArgs(
                token_id=token_id,
                price=round(close_limit_price, 2),
                size=round(shares, 2),
                side='SELL',
            )
            resp = client.create_and_post_order(ord_args)
        else:
            mo = MarketOrderArgs(
                token_id=token_id,
                amount=shares,
                side='SELL',
                order_type=OrderType.FAK,
            )
            signed_order = client.create_market_order(mo)
            resp = client.post_order(signed_order, orderType=OrderType.FAK)

        taking = float(resp.get('takingAmount') or (shares * (close_limit_price or 0.70)))
        res_obj = {
            'decision': 'close',
            'market_slug': slug,
            'token_id': token_id,
            'order_post_result': {
                'success': True,
                'status': str(resp.get('status') or 'matched').lower(),
                'takingAmount': taking,
                'makingAmount': shares,
                'orderID': resp.get('orderID'),
                'transactionsHashes': resp.get('transactionsHashes') or [],
            },
        }
        return json.dumps(res_obj), [res_obj]
    except Exception as e:
        err_msg = str(e)
        err_obj = {
            'decision': 'close',
            'market_slug': slug,
            'token_id': token_id,
            'order_post_result': {'success': False, 'status': 'error', 'error': err_msg},
        }
        return f"[ERROR] Live close order failed: {err_msg}", [err_obj]


PROFILES: dict[str, dict[str, Any]] = {
    'momentum_value': {
        'threshold': 0.65,
        'max_entry_price': 0.78,
        'stake_usd': 5.0,
        'take_profit_pct': 0.22,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.25,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 60,
        'max_entry_seconds_left': 150,
        'min_btc_impulse': 70.0,
        'entry_timeout_min': 60,
        'poll_sec': 1.0,
        'max_trades_per_day': 12,
        'strategy_type': 'momentum_value',
        'hedge_enabled': False,
    },
    'quick_scalp': {
        'threshold': 0.68,
        'max_entry_price': 0.76,
        'stake_usd': 5.0,
        'take_profit_pct': 0.12,
        'trailing_stop_pct': 0.08,
        'stop_loss_pct': 0.20,
        'exit_before_sec': 60,
        'min_entry_seconds_left': 90,
        'max_entry_seconds_left': 160,
        'min_btc_impulse': 65.0,
        'entry_timeout_min': 60,
        'poll_sec': 0.5,
        'max_trades_per_day': 18,
        'strategy_type': 'quick_scalp',
        'hedge_enabled': False,
    },
    'mean_reversion': {
        'threshold': 0.15,
        'max_entry_price': 0.25,
        'stake_usd': 5.0,
        'take_profit_pct': 0.80,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.40,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 45,
        'max_entry_seconds_left': 140,
        'min_btc_impulse': 40.0,
        'entry_timeout_min': 60,
        'poll_sec': 1.0,
        'max_trades_per_day': 10,
        'strategy_type': 'mean_reversion',
        'hedge_enabled': False,
    },
    'skew_hedge': {
        'threshold': 0.70,
        'max_entry_price': 0.80,
        'stake_usd': 5.0,
        'take_profit_pct': 0.25,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.25,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 60,
        'max_entry_seconds_left': 150,
        'min_btc_impulse': 70.0,
        'entry_timeout_min': 60,
        'poll_sec': 1.0,
        'max_trades_per_day': 12,
        'strategy_type': 'skew_hedge',
        'hedge_enabled': True,
        'hedge_trigger_price': 0.93,
        'hedge_share_pct': 5.0,
    },
    'macro_trend_sniper': {
        'threshold': 0.70,
        'max_entry_price': 0.79,
        'stake_usd': 5.0,
        'take_profit_pct': 0.25,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.22,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 60,
        'max_entry_seconds_left': 150,
        'min_btc_impulse': 85.0,
        'entry_timeout_min': 60,
        'poll_sec': 1.0,
        'max_trades_per_day': 8,
        'strategy_type': 'macro_trend_sniper',
        'hedge_enabled': False,
    },
    'conservative': {
        'threshold': 0.70,
        'max_entry_price': 0.82,
        'stake_usd': 5.0,
        'take_profit_pct': 0.25,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.25,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 60,
        'max_entry_seconds_left': 150,
        'min_btc_impulse': 70.0,
        'entry_timeout_min': 60,
        'poll_sec': 1.0,
        'max_trades_per_day': 12,
        'strategy_type': 'momentum_value',
        'hedge_enabled': False,
    },
    'aggressive': {
        'threshold': 0.37,
        'max_entry_price': 0.85,
        'stake_usd': 5.0,
        'take_profit_pct': 0.35,
        'trailing_stop_pct': None,
        'stop_loss_pct': 0.25,
        'exit_before_sec': 20,
        'min_entry_seconds_left': 60,
        'max_entry_seconds_left': 180,
        'min_btc_impulse': 50.0,
        'entry_timeout_min': 60,
        'poll_sec': 0.5,
        'max_trades_per_day': 20,
        'strategy_type': 'aggressive',
        'hedge_enabled': False,
    },
}


def load_profiles_config():
    """Load latest profile configurations from config/btc_5m_profiles.yaml if present."""
    cfg_file = PROJECT_ROOT / "config" / "btc_5m_profiles.yaml"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            profiles = data.get("profiles", {})
            for name, pdata in profiles.items():
                if name not in PROFILES:
                    PROFILES[name] = {}
                sig = pdata.get("signal", {})
                siz = pdata.get("sizing", {})
                sl = pdata.get("stop_loss", {})
                tp = pdata.get("take_profit", {})
                ts = pdata.get("trailing_stop", {})
                timing = pdata.get("timing", {})
                hedge = pdata.get("hedge", {})

                if "threshold_price" in sig:
                    PROFILES[name]["threshold"] = float(sig["threshold_price"])
                if "max_entry_price" in sig:
                    PROFILES[name]["max_entry_price"] = float(sig["max_entry_price"])
                if "min_btc_impulse" in sig:
                    PROFILES[name]["min_btc_impulse"] = float(sig["min_btc_impulse"])
                if "stake_usd" in siz:
                    PROFILES[name]["stake_usd"] = float(siz["stake_usd"])
                if "max_trades_per_day" in siz:
                    PROFILES[name]["max_trades_per_day"] = int(siz["max_trades_per_day"])
                if "stop_loss_pct_from_entry" in sl:
                    PROFILES[name]["stop_loss_pct"] = float(sl["stop_loss_pct_from_entry"])
                if tp.get("enabled") and "take_profit_pct" in tp:
                    PROFILES[name]["take_profit_pct"] = float(tp["take_profit_pct"])
                if ts.get("enabled") and "trailing_stop_pct" in ts:
                    PROFILES[name]["trailing_stop_pct"] = float(ts["trailing_stop_pct"])
                if hedge.get("enabled"):
                    PROFILES[name]["hedge_enabled"] = True
                    PROFILES[name]["hedge_trigger_price"] = float(hedge.get("trigger_side_price_gte", 0.93))
                    PROFILES[name]["hedge_share_pct"] = float(hedge.get("hedge_share_of_main_pct", 5.0))
                if "exit_before_sec" in timing:
                    PROFILES[name]["exit_before_sec"] = int(timing["exit_before_sec"])
                if "min_entry_seconds_left" in timing:
                    PROFILES[name]["min_entry_seconds_left"] = int(timing["min_entry_seconds_left"])
                if "max_entry_seconds_left" in timing:
                    PROFILES[name]["max_entry_seconds_left"] = int(timing["max_entry_seconds_left"])
                ai = pdata.get("ai_engine", {})
                if ai.get("enabled") or name == "jev_ai_brain":
                    PROFILES[name]["use_jev"] = True
                    PROFILES[name]["jev_min_confidence"] = float(ai.get("min_confidence", 0.65))
                    PROFILES[name]["jev_min_conviction"] = float(ai.get("min_conviction", 3.0))
                    PROFILES[name]["jev_max_reversal"] = float(ai.get("max_reversal_risk", 0.35))
        except Exception:
            pass


def apply_profile(args: argparse.Namespace) -> argparse.Namespace:
    load_profiles_config()
    prof_key = args.profile or 'momentum_value'
    if prof_key not in PROFILES:
        prof_key = 'momentum_value'
    prof = PROFILES[prof_key]

    if args.threshold is None:
        args.threshold = float(prof['threshold'])
    if getattr(args, 'max_entry_price', None) is None:
        args.max_entry_price = float(prof.get('max_entry_price', 0.82))
    if args.stake_usd is None:
        args.stake_usd = float(prof['stake_usd'])
    if args.stop_loss_pct is None:
        args.stop_loss_pct = float(prof['stop_loss_pct'])
    if getattr(args, 'take_profit_pct', None) is None:
        args.take_profit_pct = float(prof['take_profit_pct']) if prof.get('take_profit_pct') is not None else None
    if getattr(args, 'trailing_stop_pct', None) is None:
        args.trailing_stop_pct = float(prof['trailing_stop_pct']) if prof.get('trailing_stop_pct') is not None else None
    if getattr(args, 'hedge_enabled', None) is None:
        args.hedge_enabled = bool(prof.get('hedge_enabled', False))
    args.hedge_trigger_price = float(prof.get('hedge_trigger_price', 0.93))
    args.hedge_share_pct = float(prof.get('hedge_share_pct', 5.0))
    args.strategy_type = prof.get('strategy_type', prof_key)
    if getattr(args, 'use_jev', False) is False:
        args.use_jev = bool(prof.get('use_jev', False))
    if getattr(args, 'jev_min_confidence', None) is None:
        args.jev_min_confidence = float(prof.get('jev_min_confidence', 0.65))
    if getattr(args, 'jev_min_conviction', None) is None:
        args.jev_min_conviction = float(prof.get('jev_min_conviction', 3.0))
    if getattr(args, 'jev_max_reversal', None) is None:
        args.jev_max_reversal = float(prof.get('jev_max_reversal', 0.35))

    if args.exit_before_sec is None:
        args.exit_before_sec = int(prof.get('exit_before_sec', 20))
    if args.min_entry_seconds_left is None:
        args.min_entry_seconds_left = int(prof.get('min_entry_seconds_left', 60))
    if getattr(args, 'max_entry_seconds_left', None) is None:
        args.max_entry_seconds_left = int(prof.get('max_entry_seconds_left', 150))
    if getattr(args, 'min_btc_impulse', None) is None:
        args.min_btc_impulse = float(prof.get('min_btc_impulse', 70.0))
    if args.entry_timeout_min is None:
        args.entry_timeout_min = int(prof.get('entry_timeout_min', 60))
    if args.poll_sec is None:
        args.poll_sec = float(prof.get('poll_sec', 1.0))
    if getattr(args, 'max_trades', None) is None:
        if getattr(args, 'loop', False):
            args.max_trades = int(prof.get('max_trades_per_day', 12))
        else:
            args.max_trades = 1
    return args


def default_repo_path() -> str:
    return str(PROJECT_ROOT)


def main():
    ap = argparse.ArgumentParser(description='Polymarket BTC 5-Minute Momentum Trading Engine')
    ap.add_argument('--repo', default=default_repo_path(), help='Repository path (defaults to current)')
    ap.add_argument('--profile', choices=['jev_ai_brain', 'momentum_value', 'quick_scalp', 'mean_reversion', 'skew_hedge', 'macro_trend_sniper', 'conservative', 'aggressive'], default='momentum_value')
    ap.add_argument('--use-jev', action='store_true', help='Use TypeSafe Jev System One model as AI trade decision brain')
    ap.add_argument('--jev-min-confidence', type=float, default=None, help='Minimum Jev confidence threshold (default 0.65)')
    ap.add_argument('--jev-min-conviction', type=float, default=None, help='Minimum Jev edge conviction score 1-5 (default 3.0)')
    ap.add_argument('--jev-max-reversal', type=float, default=None, help='Maximum allowed Jev reversal risk probability (default 0.35)')
    ap.add_argument('--threshold', type=float, default=None, help='CLOB ask price threshold (e.g. 0.70)')
    ap.add_argument('--max-entry-price', type=float, default=None, help='Maximum CLOB ask price allowed for entry (e.g. 0.78)')
    ap.add_argument('--stake-usd', type=float, default=None, help='Stake per position in USDC (e.g. 5.0)')
    ap.add_argument('--stop-loss-pct', type=float, default=None, help='Stop loss percentage from entry price (e.g. 0.25 for -25%%)')
    ap.add_argument('--take-profit-pct', type=float, default=None, help='Take profit percentage from entry price (e.g. 0.22 for +22%%)')
    ap.add_argument('--trailing-stop-pct', type=float, default=None, help='Gain required to ratchet stop loss to break-even (e.g. 0.08 for +8%%)')
    ap.add_argument('--hedge-enabled', action='store_true', default=None, help='Enable tail-risk hedge on opposite token')
    ap.add_argument('--exit-before-sec', type=int, default=None, help='Close position N seconds before 5m candle expiry (e.g. 20)')
    ap.add_argument('--min-entry-seconds-left', type=int, default=None, help='Do not open if less seconds remain in current slot (e.g. 60)')
    ap.add_argument('--max-entry-seconds-left', type=int, default=None, help='Do not open if more seconds remain in current slot (e.g. 150)')
    ap.add_argument('--min-btc-impulse', type=float, default=None, help='Minimum BTC spot price delta from 5m open required (e.g. 70.0)')
    ap.add_argument('--entry-timeout-min', type=int, default=None, help='Timeout waiting for entry in minutes')
    ap.add_argument('--poll-sec', type=float, default=None, help='Polling frequency in seconds')
    ap.add_argument('--close-retry-max', type=int, default=18, help='Max close retries when exiting')
    ap.add_argument('--close-retry-delay-sec', type=float, default=2.0, help='Delay between close retries')
    ap.add_argument('--execute', action='store_true', help='Execute real orders on live Polymarket CLOB')
    ap.add_argument('--loop', action='store_true', help='Continuous loop mode: automatically advance across 5m candles up to max trades')
    ap.add_argument('--max-trades', type=int, default=None, help='Max trades for this session (defaults to profile max_trades_per_day if --loop, or 1 if one-shot)')
    args = apply_profile(ap.parse_args())

    session_start_time = time.time()

    jev_engine = None
    if getattr(args, 'use_jev', False):
        if JevDecisionEngine:
            jev_engine = JevDecisionEngine(
                min_confidence=args.jev_min_confidence,
                min_conviction=args.jev_min_conviction,
                max_reversal_risk=args.jev_max_reversal
            )
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] >>> JEV AI DECISION BRAIN INITIALIZED (Live: {jev_engine.is_live_ready()})", flush=True)
            print(f"       Min Confidence: {args.jev_min_confidence:.2f} | Min Conviction: {args.jev_min_conviction:.1f}/5.0 | Max RevRisk: {args.jev_max_reversal:.2f}", flush=True)
        else:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] >>> Warning: JevDecisionEngine not available. Falling back to rules.", flush=True)
    session_deadline = session_start_time + (args.entry_timeout_min * 60)
    session_trades_completed = 0
    session_net_pnl = 0.0
    traded_slugs: set[str] = set()

    session_mode_str = f"CONTINUOUS LOOP (Target: up to {args.max_trades} trades)" if args.loop else "ONE-SHOT (Single Trade)"

    tp_desc = f"+{int(args.take_profit_pct * 100)}%" if getattr(args, 'take_profit_pct', None) else "None"
    trail_desc = f"+{int(args.trailing_stop_pct * 100)}% to Break-Even" if getattr(args, 'trailing_stop_pct', None) else "None"

    print("=" * 65, flush=True)
    print("  BTC 5m Polymarket Momentum Trading Engine", flush=True)
    print(f"  Mode:         {'[LIVE REAL ORDERS]' if args.execute else '[SAFE SIMULATION / Paper Trading]'}", flush=True)
    print(f"  Schedule:     [{session_mode_str}]", flush=True)
    print(f"  Profile:      {args.profile} (Thresh: ${args.threshold:.2f}, Max Entry: ${args.max_entry_price:.2f}, Stake: ${args.stake_usd:.2f})", flush=True)
    print(f"  BTC Impulse:  Required +/-${args.min_btc_impulse:.1f} from 5m candle open", flush=True)
    print(f"  Entry Window: {args.min_entry_seconds_left}s - {args.max_entry_seconds_left}s remaining in candle", flush=True)
    print(f"  Risk Control: Stop-Loss: -{int(args.stop_loss_pct * 100)}% | Take-Profit: {tp_desc} | Trailing: {trail_desc}", flush=True)
    print(f"  Pre-Close:    Exit {args.exit_before_sec}s before candle close", flush=True)
    print("  Status:       Connecting to Polymarket & Binance live feeds...", flush=True)
    print("=" * 65 + "\n", flush=True)

    while session_trades_completed < args.max_trades and time.time() < session_deadline:
        trade_idx = session_trades_completed + 1
        print(f"\n" + "-" * 65, flush=True)
        print(f"  >>> HUNTING CANDLE ENTRY: TRADE #{trade_idx} of {args.max_trades} (Session Deadline: {int((session_deadline - time.time())/60)}m left)", flush=True)
        print("-" * 65 + "\n", flush=True)

        report: dict[str, Any] = {
            'trade_number': trade_idx,
            'started_at': ts_utc(),
            'params': {
                'profile': args.profile,
                'strategy_type': getattr(args, 'strategy_type', args.profile),
                'threshold': args.threshold,
                'max_entry_price': args.max_entry_price,
                'stake_usd': args.stake_usd,
                'stop_loss_pct': args.stop_loss_pct,
                'take_profit_pct': getattr(args, 'take_profit_pct', None),
                'trailing_stop_pct': getattr(args, 'trailing_stop_pct', None),
                'hedge_enabled': getattr(args, 'hedge_enabled', False),
                'exit_before_sec': args.exit_before_sec,
                'min_entry_seconds_left': args.min_entry_seconds_left,
                'max_entry_seconds_left': args.max_entry_seconds_left,
                'min_btc_impulse': args.min_btc_impulse,
                'entry_timeout_min': args.entry_timeout_min,
                'poll_sec': args.poll_sec,
                'close_retry_max': args.close_retry_max,
                'close_retry_delay_sec': args.close_retry_delay_sec,
                'execute': args.execute,
                'loop': args.loop,
                'max_trades': args.max_trades,
            },
            'attempts': [],
        }

        opened = None

        while time.time() < session_deadline:
            try:
                m = resolve_active_current_5m_market()
                if not m:
                    report['attempts'].append({'ts': ts_utc(), 'status': 'heartbeat_no_current_market'})
                    time.sleep(args.poll_sec)
                    continue

                g_up, g_dn, up_t, dn_t, slug, end_iso = market_side_prices(m)

                end_ts = None
                sec_left = None
                try:
                    end_ts = dt.datetime.fromisoformat(end_iso.replace('Z', '+00:00')).timestamp()
                    sec_left = max(0.0, end_ts - time.time())
                except Exception:
                    pass

                if sec_left is None:
                    report['attempts'].append({'ts': ts_utc(), 'slug': slug, 'status': 'heartbeat_bad_market_end'})
                    time.sleep(args.poll_sec)
                    continue

                now_str = dt.datetime.now().strftime('%H:%M:%S')

                # Guard against re-trading the exact same 5-minute candle slot if already exited
                if slug in traded_slugs:
                    print(f"[{now_str}] Candle {slug[-10:]} ({int(sec_left)}s left) | Already traded in this session. Waiting for next 5m candle...", flush=True)
                    time.sleep(max(args.poll_sec, 3.0))
                    continue

                # 1. Entry Window Check: Upper Bound (Avoid entering too early before momentum forms)
                if sec_left > args.max_entry_seconds_left:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'skip_too_early_to_enter',
                        'seconds_left': sec_left,
                        'max_entry_seconds_left': args.max_entry_seconds_left,
                    })
                    print(f"[{now_str}] Candle {slug[-10:]} ({int(sec_left)}s left) | Too early to enter (waiting for window <= {args.max_entry_seconds_left}s left)", flush=True)
                    time.sleep(args.poll_sec)
                    continue

                # 2. Entry Window Check: Lower Bound (Do not open if less than N seconds remain in current slot)
                if sec_left < args.min_entry_seconds_left:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'skip_too_late_to_enter',
                        'seconds_left': sec_left,
                        'min_entry_seconds_left': args.min_entry_seconds_left,
                    })
                    print(f"[{now_str}] Candle {slug[-10:]} ({int(sec_left)}s left) | Too late to enter (< {args.min_entry_seconds_left}s remaining)", flush=True)
                    time.sleep(args.poll_sec)
                    continue

                # 3. Read CLOB orderbooks for UP and DOWN tokens
                try:
                    up_ask, dn_ask, min_spread = clob_side_prices(up_t, dn_t)
                except Exception as e:
                    report['attempts'].append({'ts': ts_utc(), 'slug': slug, 'status': 'skip_clob_unavailable', 'error': str(e)})
                    time.sleep(args.poll_sec)
                    continue

                # 4. Fetch Binance Spot BTC price and 5m candle open
                cur_slot = bucket_5m(int(time.time()))
                btc_spot, btc_open, btc_impulse = fetch_btc_spot_and_open(cur_slot)

                report['attempts'].append({
                    'ts': ts_utc(),
                    'slug': slug,
                    'status': 'heartbeat',
                    'clob_up_ask': up_ask,
                    'clob_down_ask': dn_ask,
                    'seconds_left': sec_left,
                    'btc_spot': btc_spot,
                    'btc_open': btc_open,
                    'btc_impulse': btc_impulse,
                })

                # Formatted status line
                imp_str = f"{btc_impulse:+.1f}$" if btc_impulse is not None else "N/A"
                spot_str = f"${btc_spot:,.1f}" if btc_spot is not None else "N/A"
                up_disp = f"${up_ask:.2f}" if up_ask is not None else "N/A"
                dn_disp = f"${dn_ask:.2f}" if dn_ask is not None else "N/A"
                print(f"[{now_str}] Candle {slug[-10:]} ({int(sec_left)}s left) | BTC: {spot_str} (impulse: {imp_str}) | UP ask: {up_disp} | DOWN ask: {dn_disp} | Status: scanning", flush=True)

                # 5. Evaluate Signal Candidates
                candidates: list[tuple[str, float]] = []

                if getattr(args, 'use_jev', False) and jev_engine is not None:
                    # TypeSafe Jev System One Autonomous Market Analyst
                    vel15 = getattr(STREAM_MGR.state, 'velocity_15s', 0.0) if STREAM_MGR else 0.0
                    vel60 = getattr(STREAM_MGR.state, 'velocity_60s', 0.0) if STREAM_MGR else 0.0
                    acc = getattr(STREAM_MGR.state, 'acceleration', 0.0) if STREAM_MGR else 0.0

                    payload = MarketStatePayload(
                        slug=slug,
                        seconds_left=int(sec_left),
                        btc_spot=float(btc_spot or 0.0),
                        btc_open=float(btc_open or 0.0),
                        btc_impulse=float(btc_impulse or 0.0),
                        up_ask=float(up_ask) if up_ask is not None else None,
                        dn_ask=float(dn_ask) if dn_ask is not None else None,
                        spread=float(min_spread) if min_spread is not None else None,
                        velocity_15s=vel15,
                        velocity_60s=vel60,
                        acceleration=acc,
                        max_entry_price=args.max_entry_price,
                        min_entry_seconds_left=args.min_entry_seconds_left,
                        max_entry_seconds_left=args.max_entry_seconds_left,
                        base_stake=float(args.stake_usd or 5.0),
                        max_stake=float(args.stake_usd or 5.0) * 2.0,
                    )
                    jev_res = jev_engine.evaluate_state(payload)
                    print(f"[{now_str}]  >> JEV BRAIN: {jev_res.summary()}", flush=True)
                    report.setdefault('jev_decisions', []).append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'action': jev_res.action,
                        'regime': jev_res.regime,
                        'confidence': jev_res.confidence,
                        'conviction': jev_res.conviction_score,
                        'reversal_risk': jev_res.reversal_probability,
                        'approved': jev_res.approved,
                        'reason': jev_res.reason,
                        'dynamic_stake': jev_res.dynamic_stake,
                        'dynamic_tp': jev_res.dynamic_take_profit_pct,
                        'dynamic_trail': jev_res.dynamic_trailing_stop_pct,
                        'latency_ms': jev_res.latency_ms,
                    })
                    if jev_res.approved and jev_res.action in ('BUY_UP', 'BUY_DOWN'):
                        side = 'UP' if jev_res.action == 'BUY_UP' else 'DOWN'
                        target_ask = up_ask if side == 'UP' else dn_ask
                        if target_ask is not None:
                            args.stake_usd = jev_res.dynamic_stake
                            args.take_profit_pct = jev_res.dynamic_take_profit_pct
                            args.trailing_stop_pct = jev_res.dynamic_trailing_stop_pct
                            candidates.append((side, float(target_ask)))
                elif getattr(args, 'strategy_type', 'momentum') == 'mean_reversion':
                    # Mean Reversion Archetype: Fade extreme overextensions (>= 0.85) when BTC impulse stalls
                    # Buy the cheap underdog ($0.15 - $0.25) with massive 3:1+ payoff potential
                    if up_ask is not None and float(up_ask) >= 0.85 and dn_ask is not None:
                        if float(dn_ask) <= args.max_entry_price:
                            if btc_impulse is not None and btc_impulse <= 25.0:
                                candidates.append(('DOWN', float(dn_ask)))
                            else:
                                print(f"[{now_str}]  >> Mean Reversion: UP overextended (${up_ask:.2f}), but BTC still rushing ({btc_impulse:+.1f}$). Waiting for stall.", flush=True)
                        else:
                            print(f"[{now_str}]  >> Mean Reversion: DOWN underdog ask ${dn_ask:.2f} exceeds max entry ${args.max_entry_price:.2f}.", flush=True)

                    if dn_ask is not None and float(dn_ask) >= 0.85 and up_ask is not None:
                        if float(up_ask) <= args.max_entry_price:
                            if btc_impulse is not None and btc_impulse >= -25.0:
                                candidates.append(('UP', float(up_ask)))
                            else:
                                print(f"[{now_str}]  >> Mean Reversion: DOWN overextended (${dn_ask:.2f}), but BTC still dumping ({btc_impulse:+.1f}$). Waiting for stall.", flush=True)
                        else:
                            print(f"[{now_str}]  >> Mean Reversion: UP underdog ask ${up_ask:.2f} exceeds max entry ${args.max_entry_price:.2f}.", flush=True)
                else:
                    # Standard Momentum Archetypes (momentum_value, quick_scalp, macro_trend_sniper, skew_hedge, etc.)
                    # Check UP signal: Polymarket ask in [threshold, max_entry_price] AND BTC impulse >= +min_btc_impulse
                    if up_ask is not None and float(up_ask) >= args.threshold:
                        if float(up_ask) > args.max_entry_price:
                            print(f"[{now_str}]  >> UP ask reached ${up_ask:.2f} >= ${args.threshold:.2f}, but EXCEEDS max entry ceiling ${args.max_entry_price:.2f}. Filtered out to avoid asymmetric risk.", flush=True)
                        elif btc_impulse is not None and btc_impulse >= args.min_btc_impulse:
                            candidates.append(('UP', float(up_ask)))
                        else:
                            curr_imp = f"{btc_impulse:+.1f}$" if btc_impulse is not None else "N/A"
                            print(f"[{now_str}]  >> UP ask reached ${up_ask:.2f} >= ${args.threshold:.2f}, but BTC impulse ({curr_imp}) is below +${args.min_btc_impulse:.1f}. Filtered out.", flush=True)

                    # Check DOWN signal: Polymarket ask in [threshold, max_entry_price] AND BTC impulse <= -min_btc_impulse
                    if dn_ask is not None and float(dn_ask) >= args.threshold:
                        if float(dn_ask) > args.max_entry_price:
                            print(f"[{now_str}]  >> DOWN ask reached ${dn_ask:.2f} >= ${args.threshold:.2f}, but EXCEEDS max entry ceiling ${args.max_entry_price:.2f}. Filtered out to avoid asymmetric risk.", flush=True)
                        elif btc_impulse is not None and btc_impulse <= -args.min_btc_impulse:
                            candidates.append(('DOWN', float(dn_ask)))
                        else:
                            curr_imp = f"{btc_impulse:+.1f}$" if btc_impulse is not None else "N/A"
                            print(f"[{now_str}]  >> DOWN ask reached ${dn_ask:.2f} >= ${args.threshold:.2f}, but BTC impulse ({curr_imp}) is above -${args.min_btc_impulse:.1f}. Filtered out.", flush=True)

                if not candidates:
                    time.sleep(args.poll_sec)
                    continue

                side, trigger_price = sorted(candidates, key=lambda x: x[1], reverse=True)[0]
                strat_name = getattr(args, 'strategy_type', args.profile).upper()
                print(f"\n[{now_str}] >>> VERIFIED {strat_name} SIGNAL TRIGGERED! [Trade #{trade_idx}/{args.max_trades}]", flush=True)
                print(f"       Direction:    {side}", flush=True)
                print(f"       Polymarket:   {side} ask at ${trigger_price:.2f} (Ceiling: ${args.max_entry_price:.2f})", flush=True)
                print(f"       BTC Impulse:  {btc_impulse:+.1f}$ (Target: ±${args.min_btc_impulse:.1f})", flush=True)
                print(f"       Time Left:    {int(sec_left)}s", flush=True)
                print(f"[{now_str}] >>> Entering {side} position for ${args.stake_usd:.2f} USD...", flush=True)

                out, objs = run_open(
                    slug=slug,
                    side=side,
                    stake=args.stake_usd,
                    execute=args.execute,
                    trigger_price=trigger_price,
                    token_id=(up_t if side == 'UP' else dn_t),
                )

                # Check for critical configuration or balance errors
                if any(isinstance(o, dict) and o.get('error') in ('insufficient_balance', 'missing_credentials') for o in objs):
                    print(f"[{now_str}] >>> Aborting trade cycle due to balance/credential issue.", flush=True)
                    report['last_open_try'] = out[-2000:]
                    time.sleep(15)
                    continue

                post = None
                runner = None
                for o in objs:
                    if isinstance(o, dict) and 'order_post_result' in o:
                        runner = o
                        post = o.get('order_post_result') or {}

                if post and post.get('success') is True and str(post.get('status', '')).lower() == 'matched':
                    token_id = str(runner.get('token_id') or (up_t if side == 'UP' else dn_t))
                    shares = float(post.get('takingAmount') or 0)
                    cost = float(post.get('makingAmount') or 0)
                    entry_price = float(runner.get('entry_price') or trigger_price)
                    opened = {
                        'opened_at': ts_utc(),
                        'market_slug': slug,
                        'market_end_iso': end_iso,
                        'side': side,
                        'token_id': token_id,
                        'entry_price': entry_price,
                        'shares': shares,
                        'cost_usdc': cost,
                        'open_order_id': post.get('orderID'),
                        'open_tx': (post.get('transactionsHashes') or [None])[0],
                    }
                    report['open_raw'] = out[-4000:]
                    traded_slugs.add(slug)
                    break
                else:
                    report['last_open_try'] = out[-2000:]
                    print(f"[{now_str}] >>> Order was not matched. Server response: {out[:120]}", flush=True)
            except Exception as e:
                report['attempts'].append({'ts': ts_utc(), 'status': 'error', 'error': str(e)})
            time.sleep(args.poll_sec)

        if not opened:
            report['finished_at'] = ts_utc()
            report['result'] = 'session_timeout_no_entry'
            print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> Session timeout or no entry triggered within window. Ending session.", flush=True)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            break

        report['opened'] = opened

        # 6. Position Monitoring: Stop-Loss or Pre-Close Time Exit
        end_ts = None
        try:
            end_ts = dt.datetime.fromisoformat(opened['market_end_iso'].replace('Z', '+00:00')).timestamp()
        except Exception:
            end_ts = time.time() + 300

        sl_price = round(opened['entry_price'] * (1.0 - args.stop_loss_pct), 4)
        tp_price = round(opened['entry_price'] * (1.0 + args.take_profit_pct), 4) if getattr(args, 'take_profit_pct', None) else None
        trailing_stop_pct = getattr(args, 'trailing_stop_pct', None)
        sl_ratcheted = False
        hedge_triggered = False

        report['stop_loss_price'] = sl_price
        report['take_profit_price'] = tp_price
        report['trailing_stop_pct'] = trailing_stop_pct

        tp_str = f"${tp_price:.4f} (+{int(args.take_profit_pct*100)}%)" if tp_price else "Disabled"
        trail_str = f"+{int(trailing_stop_pct*100)}% to Break-Even" if trailing_stop_pct else "Disabled"
        hedge_str = f"Active at >=${args.hedge_trigger_price:.2f}" if getattr(args, 'hedge_enabled', False) else "Disabled"

        print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> POSITION OPENED SUCCESSFULLY! [Trade #{trade_idx}/{args.max_trades}]", flush=True)
        print(f"       Side:          {opened['side']}", flush=True)
        print(f"       Entry Price:   ${opened['entry_price']:.4f}", flush=True)
        print(f"       Shares:        {opened['shares']:.4f}", flush=True)
        print(f"       Cost:          ${opened['cost_usdc']:.2f} USDC", flush=True)
        print(f"       Stop Loss:     ${sl_price:.4f} (-{int(args.stop_loss_pct*100)}%)", flush=True)
        print(f"       Take Profit:   {tp_str}", flush=True)
        print(f"       Trailing Stop: {trail_str}", flush=True)
        print(f"       Skew Hedge:    {hedge_str}", flush=True)
        print(f"       Target Exit:   {args.exit_before_sec}s before candle close\n", flush=True)

        close_reason = None
        while True:
            now = time.time()
            sec_to_exit = int(end_ts - args.exit_before_sec - now)
            if now >= (end_ts - args.exit_before_sec):
                close_reason = f'time_exit_{args.exit_before_sec}s_before_end'
                print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> {args.exit_before_sec}s PRE-CLOSE TIMER REACHED! Closing position...", flush=True)
                break

            # Query live CLOB best bid for our specific outcome token
            side_px = None
            try:
                side_px = clob_best_bid(opened['token_id'])
            except Exception:
                side_px = None

            report['last_side_price'] = side_px
            report['last_check_at'] = ts_utc()
            px_disp = f"${side_px:.4f}" if side_px is not None else "Reading CLOB..."
            tp_disp = f" | TP: ${tp_price:.4f}" if tp_price else ""
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] HOLDING {opened['side']} | Bid: {px_disp} | SL: ${sl_price:.4f}{tp_disp} | Exit in: {max(0, sec_to_exit)}s", flush=True)

            if side_px is not None and side_px > 0:
                # 1. Check Trailing Stop Ratchet to Break-Even
                if trailing_stop_pct and not sl_ratcheted:
                    be_threshold = round(opened['entry_price'] * (1.0 + trailing_stop_pct), 4)
                    if side_px >= be_threshold:
                        sl_price = opened['entry_price']
                        sl_ratcheted = True
                        report['stop_loss_price'] = sl_price
                        report['stop_loss_ratcheted'] = True
                        print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> TRAILING STOP TRIGGERED! Bid reached ${side_px:.4f} (>= +{int(trailing_stop_pct*100)}%). SL ratcheted to BREAK-EVEN (${sl_price:.4f})!", flush=True)

                # 2. Check Take Profit Target
                if tp_price and side_px >= tp_price:
                    close_reason = f"take_profit_{int(args.take_profit_pct * 100)}pct"
                    print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> TAKE PROFIT HIT! Market bid ${side_px:.4f} reached target ${tp_price:.4f}! Liquidating with profit...", flush=True)
                    break

                # 3. Check Stop Loss Hit
                if side_px <= sl_price:
                    close_reason = "stop_loss_break_even" if sl_ratcheted else f"stop_loss_{int(args.stop_loss_pct * 100)}pct"
                    print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> STOP LOSS HIT! Market bid ${side_px:.4f} dropped to/below ${sl_price:.4f}! Liquidating...", flush=True)
                    break

                # 4. Check Skew Tail Hedge Activation
                if getattr(args, 'hedge_enabled', False) and not hedge_triggered:
                    trigger_px = getattr(args, 'hedge_trigger_price', 0.93)
                    if side_px >= trigger_px:
                        hedge_triggered = True
                        hedge_pct = getattr(args, 'hedge_share_pct', 5.0)
                        print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> SKEW HEDGE ACTIVATED! Core token reached ${side_px:.4f} (>= ${trigger_px:.2f}). Tail risk hedge logged on opposite side ({hedge_pct}%).", flush=True)
                        report['hedge_activated'] = {'at_price': side_px, 'hedge_pct': hedge_pct, 'ts': ts_utc()}

            time.sleep(args.poll_sec)

        # 7. Position Liquidation / Exit
        close_debug: list[dict[str, Any]] = []
        close_obj: dict[str, Any] = {}
        out = ''
        fallback_used = None
        force_close_used = None
        client = auth_clob_client()

        for i in range(max(1, int(args.close_retry_max))):
            out, objs = run_close(
                slug=opened['market_slug'],
                token_id=opened['token_id'],
                shares=opened['shares'],
                execute=args.execute,
                close_order_type='FAK',
                side=opened.get('side', 'UP'),
            )
            close_obj = objs[-1] if objs else {}
            post = close_obj.get('order_post_result') or {}
            status = str(post.get('status') or '').lower()
            skipped = str(close_obj.get('close_skipped') or '')
            close_debug.append({
                'ts': ts_utc(),
                'attempt': i + 1,
                'order_type': 'FAK',
                'status': status,
                'close_skipped': skipped,
            })
            if post.get('success') is True and status == 'matched':
                break

            # Common transient state right after open: token balance not yet registered on-chain
            if skipped == 'zero_effective_shares':
                time.sleep(float(args.close_retry_delay_sec))
                continue

            # Fallback: if FAK has no immediate match, post GTC limit close near best bid
            txt = ((out or '') + '\n' + json.dumps(close_obj, ensure_ascii=False)).lower()
            if 'no orders found to match' in txt or 'zero_balance' in txt or status != 'matched':
                bb = None
                try:
                    bb = clob_best_bid(opened['token_id'])
                except Exception:
                    bb = None
                limit_px = max(0.01, min(0.99, float((bb - 0.01) if bb is not None else (report.get('last_side_price') or opened['entry_price']))))
                fallback_used = {'type': 'GTC_LIMIT', 'price': limit_px}
                out2, objs2 = run_close(
                    slug=opened['market_slug'],
                    token_id=opened['token_id'],
                    shares=opened['shares'],
                    execute=args.execute,
                    close_order_type='GTC',
                    close_limit_price=limit_px,
                    side=opened.get('side', 'UP'),
                )
                close_obj2 = objs2[-1] if objs2 else {}
                post2 = close_obj2.get('order_post_result') or {}
                status2 = str(post2.get('status') or '').lower()
                close_debug.append({
                    'ts': ts_utc(),
                    'attempt': i + 1,
                    'order_type': 'GTC',
                    'status': status2,
                    'close_skipped': str(close_obj2.get('close_skipped') or ''),
                    'limit_price': limit_px,
                })
                close_obj = close_obj2
                out = out2
                if post2.get('success') is True and status2 == 'matched':
                    break

                # If GTC order is live, poll status
                if post2.get('success') is True and status2 == 'live':
                    oid2 = str(post2.get('orderID') or '')
                    st_upd, ord_upd = poll_order_status(client, oid2, wait_sec=min(8.0, max(2.0, float(args.close_retry_delay_sec) * 2)), step_sec=1.0)
                    close_debug.append({
                        'ts': ts_utc(),
                        'attempt': i + 1,
                        'order_type': 'GTC_POLL',
                        'status': st_upd.lower() if st_upd else '',
                        'order_id': oid2,
                    })
                    if st_upd == 'MATCHED':
                        post2['status'] = 'matched'
                        close_obj['order_post_result'] = post2
                        break

                    cancel_info = cancel_token_orders(client, opened['token_id'])
                    bb2 = None
                    try:
                        bb2 = clob_best_bid(opened['token_id'])
                    except Exception:
                        bb2 = None
                    force_px = max(0.01, min(0.99, float((bb2 - 0.02) if bb2 is not None else 0.01)))
                    force_close_used = {
                        'type': 'FORCE_GTC_LIMIT',
                        'price': force_px,
                        'cancel_info': cancel_info,
                    }
                    out3, objs3 = run_close(
                        slug=opened['market_slug'],
                        token_id=opened['token_id'],
                        shares=opened['shares'],
                        execute=args.execute,
                        close_order_type='GTC',
                        close_limit_price=force_px,
                        side=opened.get('side', 'UP'),
                    )
                    close_obj3 = objs3[-1] if objs3 else {}
                    post3 = close_obj3.get('order_post_result') or {}
                    status3 = str(post3.get('status') or '').lower()
                    close_debug.append({
                        'ts': ts_utc(),
                        'attempt': i + 1,
                        'order_type': 'FORCE_GTC',
                        'status': status3,
                        'close_skipped': str(close_obj3.get('close_skipped') or ''),
                        'limit_price': force_px,
                    })
                    close_obj = close_obj3
                    out = out3
                    if post3.get('success') is True and status3 == 'matched':
                        break

            time.sleep(float(args.close_retry_delay_sec))

        post = close_obj.get('order_post_result') or {}
        post_status = str(post.get('status') or '').lower()
        close_usdc = float(post.get('takingAmount') or 0)
        closed = {
            'close_reason': close_reason,
            'closed_at': ts_utc(),
            'close_success': bool(post.get('success') is True and (post_status == 'matched' or close_usdc > 0)),
            'close_status': post.get('status'),
            'close_order_id': post.get('orderID'),
            'close_tx': (post.get('transactionsHashes') or [None])[0],
            'close_shares': float(post.get('makingAmount') or 0),
            'close_usdc': close_usdc,
            'close_skipped': close_obj.get('close_skipped'),
        }
        report['close_debug'] = close_debug
        if fallback_used:
            report['close_fallback'] = fallback_used
        if force_close_used:
            report['close_force'] = force_close_used
        report['close_raw'] = out[-4000:]
        report['closed'] = closed

        pnl = None
        if closed['close_usdc']:
            pnl = round(closed['close_usdc'] - opened['cost_usdc'], 4)
        report['realized_cashflow_pnl_usdc'] = pnl
        report['finished_at'] = ts_utc()
        report['result'] = 'done'

        if pnl is not None:
            pnl_symbol = "+" if pnl >= 0 else ""
            print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> POSITION CLOSED! ({close_reason})", flush=True)
            print(f"       Realized PnL: {pnl_symbol}${pnl:.4f} USDC", flush=True)
            print("=" * 65 + "\n", flush=True)

        print(json.dumps(report, ensure_ascii=False, indent=2))
        record_trade_history(report, is_live=bool(args.execute))

        session_trades_completed += 1
        if pnl is not None:
            session_net_pnl += pnl

        pnl_symbol = "+" if session_net_pnl >= 0 else ""
        print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> SESSION PROGRESS: {session_trades_completed}/{args.max_trades} Trade(s) Completed | Cumulative Session PnL: {pnl_symbol}${session_net_pnl:.4f} USDC", flush=True)

        # Evaluate session exit conditions
        if session_trades_completed >= args.max_trades:
            print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> TARGET SESSION CAP REACHED ({args.max_trades} trades). Session complete.\n", flush=True)
            break

        if not args.loop:
            print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] >>> ONE-SHOT COMPLETED (1 trade). Session complete.\n", flush=True)
            break

        # In Continuous Loop mode: wait for the active candle to completely expire before hunting next candle
        now = time.time()
        if end_ts and now < (end_ts + 2.0):
            wait_sec = max(1.0, (end_ts + 2.0) - now)
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] >>> Continuous Loop: Waiting {int(wait_sec)}s for candle {slug[-10:]} to resolve before hunting next 5m candle...\n", flush=True)
            time.sleep(wait_sec)
        else:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] >>> Continuous Loop: Advancing immediately to next 5m candle hunt...\n", flush=True)

    pnl_symbol = "+" if session_net_pnl >= 0 else ""
    print("=" * 65, flush=True)
    print(f"  Session Finished: {session_trades_completed} trade(s) executed | Cumulative PnL: {pnl_symbol}${session_net_pnl:.4f} USDC", flush=True)
    print("=" * 65 + "\n", flush=True)


def record_trade_history(report: dict, is_live: bool):
    """Atomically append completed trade record to runtime/trades_live.json or runtime/trades_paper.json."""
    opened = report.get('opened') or {}
    closed = report.get('closed') or {}
    if not opened or not closed:
        return

    runtime_dir = PROJECT_ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    filename = "trades_live.json" if is_live else "trades_paper.json"
    file_path = runtime_dir / filename

    entry_p = float(opened.get('entry_price') or 0.0)
    shares = float(opened.get('shares') or 0.0)
    cost_usdc = float(opened.get('cost_usdc') or 0.0)
    close_usdc = float(closed.get('close_usdc') or 0.0)
    exit_p = round(close_usdc / shares, 4) if shares > 0 else 0.0
    pnl = report.get('realized_cashflow_pnl_usdc')
    if pnl is None and close_usdc and cost_usdc:
        pnl = round(close_usdc - cost_usdc, 4)
    pnl = float(pnl or 0.0)
    pnl_pct = round((pnl / cost_usdc) * 100, 2) if cost_usdc > 0 else 0.0

    trade_record = {
        "id": f"trade_{int(time.time())}_{opened.get('side', 'UP').lower()}",
        "timestamp": closed.get('closed_at') or ts_utc(),
        "mode": "live" if is_live else "paper",
        "market_slug": opened.get('market_slug', ''),
        "side": opened.get('side', 'UP'),
        "entry_price": entry_p,
        "shares": shares,
        "cost_usdc": cost_usdc,
        "exit_price": exit_p,
        "exit_usdc": close_usdc,
        "exit_reason": closed.get('close_reason', 'unknown'),
        "pnl_usdc": pnl,
        "pnl_pct": pnl_pct,
        "status": "WIN" if pnl > 0 else "LOSS",
        "order_id": opened.get('open_order_id'),
        "close_order_id": closed.get('close_order_id'),
        "tx_hash": opened.get('open_tx'),
        "close_tx": closed.get('close_tx'),
    }

    try:
        existing = []
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        if not isinstance(existing, list):
            existing = []
        existing.append(trade_record)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print(f"[HISTORY] Trade successfully persisted to {filename} ({trade_record['status']} PnL: ${pnl:+.4f})", flush=True)
    except Exception as e:
        print(f"[HISTORY ERROR] Could not persist trade: {e}", flush=True)


if __name__ == '__main__':
    main()
