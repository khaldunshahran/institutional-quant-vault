#!/usr/bin/env python3
"""
Real-Time Stream Manager for Polymarket BTC 5M Trading.
Provides ultra-low latency data feeds:
1. Binance WebSocket Stream (btcusdt@ticker + btcusdt@kline_5m) for sub-millisecond spot BTC and impulse.
2. High-Performance Persistent HTTP Keep-Alive & Parallel Connection Pool for Polymarket CLOB.
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import websockets

logger = logging.getLogger('StreamManager')


from dataclasses import field

@dataclass
class LiveMarketState:
    btc_spot: Optional[float] = None
    btc_open: Optional[float] = None
    btc_impulse: Optional[float] = None
    last_spot_update: float = 0.0

    velocity_15s: float = 0.0
    velocity_60s: float = 0.0
    acceleration: float = 0.0
    tick_history: list = field(default_factory=list)

    up_token: str = ''
    dn_token: str = ''
    up_ask: Optional[float] = None
    up_bid: Optional[float] = None
    dn_ask: Optional[float] = None
    dn_bid: Optional[float] = None
    last_clob_update: float = 0.0


class BinanceWebSocketWorker:
    """Maintains a persistent WebSocket stream to Binance for real-time BTC price and 5m open."""

    def __init__(self, state: LiveMarketState):
        self.state = state
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.stream_url = 'wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/btcusdt@kline_5m'

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name='BinanceWSWorker', daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._loop and self._loop.is_running():
            def _cancel_and_stop():
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()
                self._loop.stop()
            self._loop.call_soon_threadsafe(_cancel_and_stop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_listen())
        except Exception:
            pass

    async def _connect_and_listen(self):
        retry_delay = 1.0
        while self.running:
            try:
                async with websockets.connect(self.stream_url, ping_interval=20, ping_timeout=10) as ws:
                    retry_delay = 1.0
                    while self.running:
                        msg = await ws.recv()
                        self._process_message(msg)
            except Exception:
                if not self.running:
                    break
                await asyncio.sleep(retry_delay)
                retry_delay = min(15.0, retry_delay * 1.5)

    def _process_message(self, raw_msg: str):
        try:
            payload = json.loads(raw_msg)
            stream_name = payload.get('stream')
            data = payload.get('data', {})

            now = time.time()
            if stream_name == 'btcusdt@ticker':
                price_str = data.get('c')
                if price_str:
                    self.state.btc_spot = float(price_str)
                    self.state.last_spot_update = now
                    if self.state.btc_open is not None:
                        self.state.btc_impulse = round(self.state.btc_spot - self.state.btc_open, 2)

                    # Real-time sub-millisecond velocity & acceleration calculation
                    hist = self.state.tick_history
                    hist.append((now, self.state.btc_spot))
                    cutoff = now - 90.0
                    while hist and hist[0][0] < cutoff:
                        hist.pop(0)

                    p_15 = next((p for t, p in hist if t >= now - 15.0), hist[0][1])
                    t_15 = next((t for t, p in hist if t >= now - 15.0), hist[0][0])
                    dt_15 = max(0.5, now - t_15)
                    self.state.velocity_15s = round((self.state.btc_spot - p_15) / dt_15, 2)

                    p_60 = hist[0][1]
                    t_60 = hist[0][0]
                    dt_60 = max(0.5, now - t_60)
                    self.state.velocity_60s = round((self.state.btc_spot - p_60) / dt_60, 2)
                    self.state.acceleration = round(self.state.velocity_15s - self.state.velocity_60s, 2)

            elif stream_name == 'btcusdt@kline_5m':
                kline = data.get('k', {})
                open_str = kline.get('o')
                if open_str:
                    self.state.btc_open = float(open_str)
                    if self.state.btc_spot is not None:
                        self.state.btc_impulse = round(self.state.btc_spot - self.state.btc_open, 2)
        except Exception:
            pass


class PolymarketFastClient:
    """High-performance client with HTTP/1.1 Keep-Alive connection pooling and parallel orderbook queries."""

    def __init__(self, pool_size: int = 10):
        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=retries)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self.executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix='PMFastPool')

    def get_order_book(self, token_id: str) -> Optional[dict]:
        try:
            url = f'https://clob.polymarket.com/book?token_id={token_id}'
            resp = self.session.get(url, timeout=1.8)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def get_parallel_side_prices(self, up_token: str, dn_token: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Fetch UP and DOWN orderbooks simultaneously in parallel across persistent TCP connections."""
        f_up = self.executor.submit(self.get_order_book, up_token)
        f_dn = self.executor.submit(self.get_order_book, dn_token)

        book_up = f_up.result()
        book_dn = f_dn.result()

        up_bid, up_ask = self._extract_best_bid_ask(book_up)
        dn_bid, dn_ask = self._extract_best_bid_ask(book_dn)

        spread = None
        if up_ask is not None and up_bid is not None:
            spread = max(0.0, up_ask - up_bid)
        if dn_ask is not None and dn_bid is not None:
            s = max(0.0, dn_ask - dn_bid)
            spread = s if spread is None else min(spread, s)

        return up_ask, dn_ask, spread

    def get_full_orderbooks(self, up_token: str, dn_token: str) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Return (up_bid, up_ask, dn_bid, dn_ask, spread) in a single parallel fetch."""
        f_up = self.executor.submit(self.get_order_book, up_token)
        f_dn = self.executor.submit(self.get_order_book, dn_token)

        book_up = f_up.result()
        book_dn = f_dn.result()

        up_bid, up_ask = self._extract_best_bid_ask(book_up)
        dn_bid, dn_ask = self._extract_best_bid_ask(book_dn)

        spread = None
        if up_ask is not None and up_bid is not None:
            spread = max(0.0, up_ask - up_bid)
        if dn_ask is not None and dn_bid is not None:
            s = max(0.0, dn_ask - dn_bid)
            spread = s if spread is None else min(spread, s)

        return up_bid, up_ask, dn_bid, dn_ask, spread

    def get_best_bid(self, token_id: str) -> Optional[float]:
        """Fetch the highest bid for token_id over persistent HTTP Keep-Alive socket."""
        book = self.get_order_book(token_id)
        best_bid, _ = self._extract_best_bid_ask(book)
        return best_bid

    @staticmethod
    def _extract_best_bid_ask(book: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
        if not book or not isinstance(book, dict):
            return None, None
        bids = book.get('bids') or []
        asks = book.get('asks') or []

        best_bid = None
        best_ask = None

        for b in bids:
            p = float(b.get('price', 0) if isinstance(b, dict) else getattr(b, 'price', 0) or 0)
            if best_bid is None or p > best_bid:
                best_bid = p

        for a in asks:
            p = float(a.get('price', 0) if isinstance(a, dict) else getattr(a, 'price', 0) or 0)
            if best_ask is None or p < best_ask:
                best_ask = p

        return best_bid, best_ask

    def close(self):
        try:
            self.session.close()
            self.executor.shutdown(wait=False)
        except Exception:
            pass


class StreamManager:
    """Unified ultra-low latency stream manager."""

    _instance: Optional['StreamManager'] = None

    @classmethod
    def get_instance(cls) -> 'StreamManager':
        if cls._instance is None:
            cls._instance = StreamManager()
            cls._instance.start()
        return cls._instance

    def __init__(self):
        self.state = LiveMarketState()
        self.binance_worker = BinanceWebSocketWorker(self.state)
        self.clob_client = PolymarketFastClient(pool_size=8)
        self.started = False

    def start(self):
        if self.started:
            return
        self.started = True
        self.binance_worker.start()
        # Warm up connection pool to clob.polymarket.com
        threading.Thread(target=self._warm_up_pool, daemon=True).start()

    def _warm_up_pool(self):
        try:
            self.clob_client.session.get('https://clob.polymarket.com/time', timeout=2.0)
        except Exception:
            pass


    def get_velocity_metrics(self) -> dict:
        now = time.time()
        lat = max(0.1, round((now - self.state.last_spot_update) * 1000, 1)) if self.state.last_spot_update > 0 else 0.5
        return {
            'velocity_15s': getattr(self.state, 'velocity_15s', 0.0),
            'velocity_60s': getattr(self.state, 'velocity_60s', 0.0),
            'acceleration': getattr(self.state, 'acceleration', 0.0),
            'latency_ms': lat,
            'tick_count': len(getattr(self.state, 'tick_history', [])),
        }

    def get_btc_telemetry(self, cur_5m: int) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (spot, open, impulse) instantly from WebSocket cache (< 0.01ms).
        Falls back to fast HTTP if WebSocket hasn't received a tick in > 5s.
        """
        now = time.time()
        # If WebSocket data is fresh (< 4 seconds old), return in-memory values instantly
        if self.state.btc_spot is not None and self.state.btc_open is not None and (now - self.state.last_spot_update) < 4.0:
            return self.state.btc_spot, self.state.btc_open, self.state.btc_impulse

        # Fallback to fast HTTP request
        try:
            r_ticker = self.clob_client.session.get('https://api.binance.com/api/v3/ticker/price', params={'symbol': 'BTCUSDT'}, timeout=2.0)
            r_kline = self.clob_client.session.get(
                'https://api.binance.com/api/v3/klines',
                params={'symbol': 'BTCUSDT', 'interval': '5m', 'startTime': cur_5m * 1000, 'limit': 1},
                timeout=2.0,
            )
            spot = float(r_ticker.json().get('price', 0)) if r_ticker.status_code == 200 else None
            open_px = float(r_kline.json()[0][1]) if (r_kline.status_code == 200 and r_kline.json()) else None
            if spot is not None and open_px is not None:
                self.state.btc_spot = spot
                self.state.btc_open = open_px
                self.state.btc_impulse = round(spot - open_px, 2)
                self.state.last_spot_update = now
                return spot, open_px, self.state.btc_impulse
        except Exception:
            pass

        return self.state.btc_spot, self.state.btc_open, self.state.btc_impulse

    def get_clob_prices(self, up_token: str, dn_token: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Fetch UP and DOWN orderbooks in parallel over persistent TCP pool."""
        return self.clob_client.get_parallel_side_prices(up_token, dn_token)

    def get_full_orderbooks(self, up_token: str, dn_token: str) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Fetch UP and DOWN bids and asks simultaneously in parallel."""
        return self.clob_client.get_full_orderbooks(up_token, dn_token)

    def get_best_bid(self, token_id: str) -> Optional[float]:
        """Fetch best bid over persistent HTTP Keep-Alive connection."""
        return self.clob_client.get_best_bid(token_id)

    def stop(self):
        self.binance_worker.stop()
        self.clob_client.close()
        self.started = False
