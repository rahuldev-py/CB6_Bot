# crypto_engine/binance_adapter.py
#
# Binance USDT-M Perpetual Futures adapter.
# Handles REST (market data + signed account/order calls) and WebSocket kline stream.
#
# Public REST endpoints need no auth.
# Signed endpoints (account, orders) need API_KEY + API_SECRET from .env.
#
# Binance weight limits: 1200/min (IP).  This adapter stays well under 100/min.
# WebSocket: push-based — no weight cost. Preferred for live klines.

import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable, Dict, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger

# ── Binance endpoints ─────────────────────────────────────────────────────────
REST_BASE   = "https://fapi.binance.com"          # USDT-M Futures REST
WS_BASE     = "wss://fstream.binance.com/ws"       # USDT-M Futures WebSocket
TIME_SYNC_INTERVAL = 30 * 60                        # re-sync server time every 30 min

# REQ-2.4: Explicit per-call timeout constants.
# Every requests.* call in this file uses one of these — no unlimited blocking.
DEFAULT_HTTP_TIMEOUT  = 5.0   # fast public queries (mark price, ticker)
MARKET_DATA_TIMEOUT   = 10.0  # klines / exchange-info (larger payloads)
ORDER_TIMEOUT         = 10.0  # order placement / cancellation — balance latency vs safety
ACCOUNT_DATA_TIMEOUT  = 10.0  # account / position / trade-history queries

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Shared across all REST calls from this process.
# Allows max MAX_CALLS_PER_MIN before sleeping.
MAX_CALLS_PER_MIN = 80   # conservative ceiling (Binance limit = 1200 weight/min)

class _RateLimiter:
    def __init__(self, max_per_min: int = MAX_CALLS_PER_MIN):
        self._lock    = threading.Lock()
        self._calls   = deque()          # timestamps of recent calls
        self._max     = max_per_min
        self._window  = 60.0             # seconds

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            # Drop calls older than window
            while self._calls and now - self._calls[0] > self._window:
                self._calls.popleft()
            if len(self._calls) >= self._max:
                sleep_for = self._window - (now - self._calls[0])
                if sleep_for > 0:
                    logger.debug(f"Crypto rate limiter: sleeping {sleep_for:.1f}s")
                    time.sleep(sleep_for)
            self._calls.append(time.monotonic())

_rate_limiter = _RateLimiter()


# ── Binance Adapter ───────────────────────────────────────────────────────────

class BinanceAdapter:
    """
    Wraps Binance USDT-M Perpetual Futures API.

    Market data (klines, mark price, server time) — no auth required.
    Account / order endpoints — require API_KEY + API_SECRET.

    Paper mode (paper=True): place_order() logs the intent but sends nothing to exchange.
    """

    SYMBOL   = "BTCUSDT"
    LOT_STEP = 0.001     # minimum qty increment for BTCUSDT perp
    MIN_QTY  = 0.001     # minimum order quantity

    def __init__(self, api_key: str = '', api_secret: str = '', paper: bool = True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.paper      = paper
        self._offset_ms = 0          # local_time + _offset_ms = server_time
        self._sync_lock = threading.Lock()

        # WebSocket state
        self._ws_thread : Optional[threading.Thread] = None
        self._ws_running = False
        self._kline_callback: Optional[Callable] = None
        self._ws_reconnect_delay = 5   # seconds, doubles on each failure

        self._sync_server_time()
        threading.Thread(target=self._time_sync_loop, daemon=True).start()

    # ── Time sync ─────────────────────────────────────────────────────────────

    def _sync_server_time(self):
        """Fetch Binance server time and compute local clock offset."""
        try:
            _rate_limiter.acquire()
            r = requests.get(f"{REST_BASE}/fapi/v1/time", timeout=5)
            r.raise_for_status()
            server_ms  = int(r.json()['serverTime'])
            local_ms   = int(time.time() * 1000)
            with self._sync_lock:
                self._offset_ms = server_ms - local_ms
            logger.debug(f"Binance time sync: offset={self._offset_ms}ms")
        except Exception as e:
            logger.warning(f"Binance time sync failed: {e}")

    def _time_sync_loop(self):
        while True:
            time.sleep(TIME_SYNC_INTERVAL)
            self._sync_server_time()

    def _timestamp(self) -> int:
        with self._sync_lock:
            return int(time.time() * 1000) + self._offset_ms

    # ── Signing ───────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        query = '&'.join(f"{k}={v}" for k, v in params.items())
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _auth_headers(self) -> dict:
        return {'X-MBX-APIKEY': self.api_key}

    # ── REST: market data (public) ────────────────────────────────────────────

    def get_klines(self, symbol: str = 'BTCUSDT', interval: str = '5m',
                   limit: int = 150) -> Optional[List[dict]]:
        """
        Fetch OHLCV candles.  Returns list of dicts:
          {'open','high','low','close','volume','timestamp'}
        """
        try:
            _rate_limiter.acquire()
            r = requests.get(f"{REST_BASE}/fapi/v1/klines", params={
                'symbol': symbol, 'interval': interval, 'limit': limit
            }, timeout=MARKET_DATA_TIMEOUT)
            r.raise_for_status()
            rows = []
            for k in r.json():
                rows.append({
                    'timestamp': int(k[0]),
                    'open'     : float(k[1]),
                    'high'     : float(k[2]),
                    'low'      : float(k[3]),
                    'close'    : float(k[4]),
                    'volume'   : float(k[5]),
                })
            return rows
        except requests.exceptions.Timeout:
            logger.error(f"get_klines timeout ({MARKET_DATA_TIMEOUT}s) — {symbol} {interval}")
            return None
        except Exception as e:
            logger.error(f"get_klines error: {e}")
            return None

    def get_klines_df(self, symbol: str = 'BTCUSDT', interval: str = '5m',
                      limit: int = 150):
        """Return klines as a pandas DataFrame (same format ICT scanner expects)."""
        try:
            import pandas as pd
            rows = self.get_klines(symbol, interval, limit)
            if not rows:
                return None
            df = pd.DataFrame(rows)
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df = df.set_index('datetime')
            df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            return df
        except Exception as e:
            logger.error(f"get_klines_df error: {e}")
            return None

    def get_mark_price(self, symbol: str = 'BTCUSDT') -> Optional[float]:
        """Current mark price — most accurate reference for futures entry."""
        try:
            _rate_limiter.acquire()
            r = requests.get(f"{REST_BASE}/fapi/v1/premiumIndex",
                             params={'symbol': symbol}, timeout=5)
            r.raise_for_status()
            return float(r.json()['markPrice'])
        except Exception as e:
            logger.error(f"get_mark_price error: {e}")
            return None

    def get_symbol_info(self, symbol: str = 'BTCUSDT') -> dict:
        """
        Fetch lot size, min qty, and price precision from Binance exchange info.
        Returns dict: {lot_step, min_qty, price_precision, max_leverage}
        Public endpoint — no auth required.
        """
        defaults = {'lot_step': 0.001, 'min_qty': 0.001, 'price_precision': 2, 'max_leverage': 125}
        try:
            _rate_limiter.acquire()
            r = requests.get(f"{REST_BASE}/fapi/v1/exchangeInfo", timeout=10)
            r.raise_for_status()
            for s in r.json().get('symbols', []):
                if s['symbol'] != symbol:
                    continue
                info = dict(defaults)
                info['price_precision'] = s.get('pricePrecision', 2)
                for f in s.get('filters', []):
                    if f['filterType'] == 'LOT_SIZE':
                        info['lot_step'] = float(f['stepSize'])
                        info['min_qty']  = float(f['minQty'])
                return info
        except Exception as e:
            logger.error(f"get_symbol_info error: {e}")
        return defaults

    def get_ticker(self, symbol: str = 'BTCUSDT') -> Optional[dict]:
        """24hr stats: lastPrice, priceChangePercent, volume."""
        try:
            _rate_limiter.acquire()
            r = requests.get(f"{REST_BASE}/fapi/v1/ticker/24hr",
                             params={'symbol': symbol}, timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"get_ticker error: {e}")
            return None

    # ── REST: account (signed) ────────────────────────────────────────────────

    def get_account(self) -> Optional[dict]:
        """Fetch futures account balances and positions."""
        if not self.api_key:
            return None
        try:
            _rate_limiter.acquire()
            params = {'timestamp': self._timestamp()}
            params['signature'] = self._sign(params)
            r = requests.get(f"{REST_BASE}/fapi/v2/account",
                             headers=self._auth_headers(),
                             params=params, timeout=10)
            if not r.ok:
                logger.error(f"get_account error: {r.status_code} — {r.text[:300]}")
                return None
            return r.json()
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return None

    def get_usdt_balance(self) -> float:
        """Return available USDT balance for trading. 0 on error."""
        acct = self.get_account()
        if not acct:
            return 0.0
        for asset in acct.get('assets', []):
            if asset.get('asset') == 'USDT':
                return float(asset.get('availableBalance', 0))
        return 0.0

    def get_user_trades(self, symbol: str, since_ms: int = None,
                        limit: int = 20) -> Optional[list]:
        """
        Fetch actual trade fills (entry + exit executions) from Binance.
        Returns list of fill dicts: time, side, price, qty, realizedPnl, commission.
        Used to get the actual exit fill price after a SL/TP fires.
        Returns None in paper mode or on error.
        """
        if self.paper or not self.api_key:
            return None
        try:
            _rate_limiter.acquire()
            params = {'symbol': symbol, 'limit': limit, 'timestamp': self._timestamp()}
            if since_ms:
                params['startTime'] = since_ms
            params['signature'] = self._sign(params)
            r = requests.get(f"{REST_BASE}/fapi/v1/userTrades",
                             headers=self._auth_headers(),
                             params=params, timeout=8)
            r.raise_for_status()
            return [
                {
                    'time'        : int(t.get('time', 0)),
                    'side'        : t.get('side', ''),
                    'price'       : round(float(t.get('price', 0)), 2),
                    'qty'         : float(t.get('qty', 0)),
                    'realizedPnl' : round(float(t.get('realizedPnl', 0)), 4),
                    'commission'  : round(float(t.get('commission', 0)), 4),
                    'orderId'     : t.get('orderId', ''),
                }
                for t in r.json()
            ]
        except Exception as e:
            logger.error(f"get_user_trades error: {e}")
            return None

    def get_open_positions(self, symbols: list = None) -> Optional[dict]:
        """
        Fetch open futures positions from Binance.
        Returns dict keyed by symbol: {qty, side, entry, mark, upnl}.
        qty=0 means position is closed. Returns None on auth error.
        """
        if not self.api_key:
            return None
        try:
            _rate_limiter.acquire()
            params = {'timestamp': self._timestamp()}
            params['signature'] = self._sign(params)
            r = requests.get(f"{REST_BASE}/fapi/v2/positionRisk",
                             headers=self._auth_headers(),
                             params=params, timeout=6)
            if not r.ok:
                logger.error(f"get_open_positions error: {r.status_code} — {r.text[:300]}")
                return None
            result = {}
            for pos in r.json():
                sym = pos['symbol']
                if symbols and sym not in symbols:
                    continue
                qty = float(pos.get('positionAmt', 0))
                result[sym] = {
                    'qty'  : abs(qty),
                    'side' : 'BULLISH' if qty > 0 else ('BEARISH' if qty < 0 else 'FLAT'),
                    'entry': float(pos.get('entryPrice', 0)),
                    'mark' : float(pos.get('markPrice', 0)),
                    'upnl' : float(pos.get('unRealizedProfit', 0)),
                }
            return result
        except Exception as e:
            logger.error(f"get_open_positions error: {e}")
            return None

    def get_realized_pnl(self, symbol: str, since_ms: int = None,
                         limit: int = 10) -> Optional[list]:
        """
        Fetch recent REALIZED_PNL income entries for a symbol.
        since_ms: epoch-ms of trade open — filters entries to only that trade's closes.
        Returns list of dicts with keys: time, income (float), info.
        Returns None on error or in paper mode.
        """
        if self.paper:
            return None
        if not self.api_key:
            return None
        try:
            _rate_limiter.acquire()
            params = {
                'symbol'     : symbol,
                'incomeType' : 'REALIZED_PNL',
                'limit'      : limit,
                'timestamp'  : self._timestamp(),
            }
            if since_ms:
                params['startTime'] = since_ms
            params['signature'] = self._sign(params)
            r = requests.get(f"{REST_BASE}/fapi/v1/income",
                             headers=self._auth_headers(),
                             params=params, timeout=10)
            r.raise_for_status()
            entries = r.json()
            return [
                {
                    'time'  : e.get('time'),
                    'income': round(float(e.get('income', 0)), 4),
                    'info'  : e.get('info', ''),
                }
                for e in entries
            ]
        except Exception as e:
            logger.error(f"get_realized_pnl error: {e}")
            return None

    # ── REST: orders (signed) ─────────────────────────────────────────────────

    def floor_qty(self, qty: float, lot_step: float = None) -> float:
        """Floor quantity to lot_step precision (Binance rejects over-precise values)."""
        step = lot_step or self.LOT_STEP
        steps = int(qty / step)
        return round(steps * step, 3)

    def place_stop_market(self, symbol: str, side: str, stop_price: float,
                          qty: float = None) -> Optional[dict]:
        """
        Place a STOP_MARKET order to protect a position.
        Uses quantity + reduceOnly=true (more reliable than closePosition=true).
        workingType=MARK_PRICE avoids contract-price trigger issues.
        Paper mode: logs and returns a simulated response.
        """
        if self.paper:
            logger.info(f"[PAPER] STOP_MARKET {side} {symbol} stopPrice={stop_price} qty={qty}")
            return {'orderId': f"PAPER_SL_{int(time.time())}", 'paper': True}
        try:
            _rate_limiter.acquire()
            params = {
                'symbol'      : symbol,
                'side'        : side,
                'type'        : 'STOP_MARKET',
                'stopPrice'   : str(round(stop_price, 2)),
                'workingType' : 'MARK_PRICE',
                'timestamp'   : self._timestamp(),
            }
            if qty:
                params['quantity']   = str(round(qty, 3))
                params['reduceOnly'] = 'true'
            else:
                params['closePosition'] = 'true'
            params['signature'] = self._sign(params)
            r = requests.post(f"{REST_BASE}/fapi/v1/order",
                              headers=self._auth_headers(),
                              params=params, timeout=ORDER_TIMEOUT)
            if not r.ok:
                logger.error(f"place_stop_market {side} {symbol} @ {stop_price} "
                             f"→ {r.status_code}: {r.text}")
                return None
            result = r.json()
            logger.info(f"STOP_MARKET placed: {side} {symbol} @ {stop_price} "
                        f"qty={qty} orderId={result.get('orderId')}")
            return result
        except requests.exceptions.Timeout:
            logger.error(
                f"place_stop_market TIMEOUT ({ORDER_TIMEOUT}s) — {side} {symbol} @ {stop_price}. "
                "Order may not have been placed — verify on Binance before re-submitting."
            )
            return None
        except Exception as e:
            logger.error(f"place_stop_market error: {e}")
            return None

    def cancel_order(self, symbol: str, order_id) -> bool:
        """Cancel an open order by orderId. Returns True on success."""
        if self.paper:
            logger.info(f"[PAPER] cancel_order {symbol} id={order_id}")
            return True
        try:
            _rate_limiter.acquire()
            params = {
                'symbol'    : symbol,
                'orderId'   : order_id,
                'timestamp' : self._timestamp(),
            }
            params['signature'] = self._sign(params)
            r = requests.delete(f"{REST_BASE}/fapi/v1/order",
                                headers=self._auth_headers(),
                                params=params, timeout=ORDER_TIMEOUT)
            r.raise_for_status()
            logger.info(f"Order cancelled: {symbol} id={order_id}")
            return True
        except requests.exceptions.Timeout:
            logger.error(
                f"cancel_order TIMEOUT ({ORDER_TIMEOUT}s) — {symbol} id={order_id}. "
                "Cancel may not have reached Binance — verify order state manually."
            )
            return False
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return False

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = 'MARKET',
                    reduce_only: bool = False,
                    lot_step: float = None) -> Optional[dict]:
        """
        Place a futures order.
        Paper mode: logs the order, returns a simulated response.
        Live mode: sends to Binance.

        side: 'BUY' or 'SELL'
        """
        qty = self.floor_qty(qty, lot_step)
        if qty < self.MIN_QTY:
            logger.warning(f"Order qty {qty} below min {self.MIN_QTY} — skipped")
            return None

        if self.paper:
            mark = self.get_mark_price(symbol) or 0
            logger.info(f"[PAPER] {side} {qty} {symbol} @ mark {mark}")
            return {
                'orderId'     : f"PAPER_{int(time.time())}",
                'symbol'      : symbol,
                'side'        : side,
                'qty'         : qty,
                'price'       : mark,
                'status'      : 'FILLED',
                'paper'       : True,
            }

        try:
            _rate_limiter.acquire()
            params = {
                'symbol'    : symbol,
                'side'      : side,
                'type'      : order_type,
                'quantity'  : str(qty),
                'timestamp' : self._timestamp(),
            }
            if reduce_only:
                params['reduceOnly'] = 'true'
            params['signature'] = self._sign(params)
            r = requests.post(f"{REST_BASE}/fapi/v1/order",
                              headers=self._auth_headers(),
                              params=params, timeout=ORDER_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logger.error(
                f"place_order TIMEOUT ({ORDER_TIMEOUT}s) — {side} {symbol} qty={qty}. "
                "Order status unknown — check Binance open orders before retrying."
            )
            return None
        except Exception as e:
            logger.error(f"place_order error: {e}")
            return None

    # ── WebSocket: live kline stream ──────────────────────────────────────────

    def start_kline_stream(self, symbol: str = 'BTCUSDT',
                           interval: str = '5m',
                           on_closed_candle: Callable = None):
        """
        Subscribe to the Binance futures kline WebSocket stream.
        Calls on_closed_candle(candle_dict) every time a 5m candle closes.

        Runs in a background daemon thread with automatic reconnection.
        If the stream is disconnected (network error, Binance maintenance),
        it reconnects after an exponential backoff (5s → 10s → 20s → max 60s).
        """
        self._kline_callback = on_closed_candle
        self._ws_running     = True
        self._ws_thread      = threading.Thread(
            target=self._ws_loop,
            args=(symbol.lower(), interval),
            daemon=True,
            name="BinanceWS"
        )
        self._ws_thread.start()
        logger.info(f"Binance WS stream started: {symbol}@kline_{interval}")

    def start_multi_stream(self, symbols: list, interval: str = '5m',
                           on_closed_candle: Callable = None):
        """
        Subscribe to multiple symbols via Binance combined stream.
        Calls on_closed_candle(symbol: str, candle: dict) on each closed candle.
        More efficient than one stream per symbol — single WebSocket connection.
        """
        self._kline_callback = on_closed_candle
        self._ws_running     = True
        self._ws_thread      = threading.Thread(
            target=self._ws_multi_loop,
            args=([s.lower() for s in symbols], interval),
            daemon=True,
            name="BinanceMultiWS"
        )
        self._ws_thread.start()
        logger.info(f"Binance multi-stream started: {symbols} @ kline_{interval}")

    def stop_kline_stream(self):
        self._ws_running = False
        logger.info("Binance WS stream stopping...")

    def _ws_loop(self, symbol: str, interval: str):
        """Blocking WebSocket loop with self-healing reconnection."""
        import asyncio
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        async def _connect():
            import websockets
            stream  = f"{symbol}@kline_{interval}"
            url     = f"{WS_BASE}/{stream}"
            delay   = self._ws_reconnect_delay

            while self._ws_running:
                try:
                    logger.info(f"WS connecting: {url}")
                    async with websockets.connect(url, ping_interval=20,
                                                  ping_timeout=10) as ws:
                        delay = 5   # reset backoff on successful connect
                        async for raw in ws:
                            if not self._ws_running:
                                return
                            try:
                                msg = json.loads(raw)
                                # Binance sends {'e':'kline','k':{...}}
                                k = msg.get('k', {})
                                if k.get('x'):   # x = candle closed
                                    candle = {
                                        'timestamp': int(k['t']),
                                        'open'     : float(k['o']),
                                        'high'     : float(k['h']),
                                        'low'      : float(k['l']),
                                        'close'    : float(k['c']),
                                        'volume'   : float(k['v']),
                                    }
                                    if self._kline_callback:
                                        try:
                                            self._kline_callback(candle)
                                        except Exception as cb_err:
                                            logger.error(f"WS callback error: {cb_err}")
                            except Exception as parse_err:
                                logger.debug(f"WS parse error: {parse_err}")

                except Exception as conn_err:
                    if not self._ws_running:
                        return
                    logger.warning(f"WS disconnected ({conn_err}). "
                                   f"Reconnecting in {delay}s...")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)   # exponential backoff, cap at 60s

        # Explicitly use SelectorEventLoop — ProactorEventLoop (Windows default)
        # silently breaks websockets: connection succeeds but no messages arrive.
        import selectors
        loop = asyncio.SelectorEventLoop(selectors.DefaultSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_connect())
        finally:
            loop.close()

    def _ws_multi_loop(self, symbols: list, interval: str):
        """
        Combined-stream WebSocket using websocket-client (sync/threaded).
        Replaces asyncio websockets which silently fails on Windows ProactorEventLoop.
        """
        import websocket as _wsc

        streams = '/'.join(f"{s.lower()}@kline_{interval}" for s in symbols)
        url     = f"wss://fstream.binance.com/stream?streams={streams}"
        delay   = self._ws_reconnect_delay
        _msg_count = [0]

        def _on_message(ws, raw):
            try:
                _msg_count[0] += 1
                if _msg_count[0] == 1:
                    logger.info(f"WS first msg: {raw[:200]}")

                msg  = json.loads(raw)
                data = msg.get('data', msg)
                k    = data.get('k', {})
                if k.get('x'):   # candle closed
                    symbol = k.get('s', '').upper()
                    candle = {
                        'timestamp': int(k['t']),
                        'open'     : float(k['o']),
                        'high'     : float(k['h']),
                        'low'      : float(k['l']),
                        'close'    : float(k['c']),
                        'volume'   : float(k['v']),
                    }
                    logger.info(f"WS candle CLOSED: {symbol} ts={k['t']} C={k['c']}")
                    if self._kline_callback:
                        try:
                            self._kline_callback(symbol, candle)
                        except Exception as cb_err:
                            logger.error(f"Multi-WS callback error: {cb_err}")
            except Exception as e:
                logger.debug(f"WS message parse error: {e}")

        def _on_open(ws):
            logger.info(f"Multi-WS connected — waiting for candle closes")

        def _on_error(ws, error):
            logger.warning(f"Multi-WS error: {error}")

        def _on_close(ws, code, msg):
            logger.warning(f"Multi-WS closed: code={code}")

        while self._ws_running:
            try:
                logger.info(f"Multi-WS connecting: {url}")
                ws = _wsc.WebSocketApp(
                    url,
                    on_open    = _on_open,
                    on_message = _on_message,
                    on_error   = _on_error,
                    on_close   = _on_close,
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.warning(f"Multi-WS exception: {e}")
            if not self._ws_running:
                break
            logger.info(f"Multi-WS reconnecting in {delay}s...")
            time.sleep(delay)
            delay = min(delay * 2, 60)
