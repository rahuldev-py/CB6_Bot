# forex_engine/mt5/mt5_connector.py
# Core MT5 connection management — init, reconnect, disconnect.

import os
import time
import threading
from typing import Optional, List, Callable, Dict

from utils.logger import logger
from forex_engine.mt5.mt5_error_handler import MT5ConnectionError

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed — live mode unavailable.")

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

import pandas as pd


_TF_MAP = {
    '1m' : 1,
    '2m' : 2,    # MT5 TIMEFRAME_M2 — granular MTF confirmation chain
    '3m' : 3,    # MT5 TIMEFRAME_M3 — used for XAUUSD Silver Bullet 3m backtest-validated TF
    '5m' : 5,
    '15m': 15,
    '30m': 30,
    '45m': 45,   # MT5 TIMEFRAME_M45 — granular MTF confirmation chain
    '1h' : 16385,
    '4h' : 16388,
    '1d' : 16408,
}


class MT5Connector:
    """
    Unified MT5 connector for live trading and yfinance paper/backtest.

    Encapsulates the full MT5 lifecycle: connect, disconnect, reconnect,
    candle fetching, price/spread queries, order placement, position
    management, and the REST-poll candle feed.

    Multi-account isolation:
        Pass terminal_path= so mt5.initialize() connects to a specific portable
        MT5 installation (e.g. C:\\CB6_MT5\\MT5_FTMO_10K\\terminal64.exe).
        Without terminal_path, the system-default open terminal is used —
        which causes "Algo Trading OFF" when two accounts share one terminal.

        Run each account's engine in a separate Python subprocess
        (forex_main.py --profile ALL already does this) so the MetaTrader5
        C-extension state is never shared between FTMO and GFT.
    """

    def __init__(self, paper: bool = True, credentials: Optional[dict] = None,
                 terminal_path: Optional[str] = None,
                 symbol_overrides: Optional[Dict[str, str]] = None):
        """
        paper            — True = yfinance data, no live orders
        credentials      — dict with keys: login, password, server.
                           None → reads MT5_LOGIN / MT5_PASSWORD / MT5_SERVER from env.
        terminal_path    — Full path to terminal64.exe for this account.
                           Passed as path= to mt5.initialize() for terminal isolation.
                           None → system-default terminal (legacy behaviour).
        symbol_overrides — Broker-specific symbol name map, e.g.
                           {'XAGUSD': 'XAGUSD.x', 'USOIL.cash': 'WTI.x'}
                           Overrides the mt5_symbol field from INSTRUMENTS for this
                           broker. Required when the broker uses non-standard names.
        """
        self._paper            = paper
        self._credentials      = credentials
        self._terminal_path    = terminal_path
        self._symbol_overrides = symbol_overrides or {}
        self._running          = False
        self._poll_thread: Optional[threading.Thread] = None
        self._alert_callback   = None  # set by owner to route alerts to the right Telegram bot

        if not paper:
            self._connect()

    def set_alert_callback(self, fn):
        """Wire a callable(message: str) so reconnect alerts go to the right Telegram bot."""
        self._alert_callback = fn

    def _broadcast_alert(self, message: str):
        if self._alert_callback:
            try:
                self._alert_callback(message)
            except Exception:
                pass
        else:
            try:
                from communications.forex_bot import send_alert as _fb
                _fb(message)
            except Exception:
                pass

    def _resolve_sym(self, mt5_sym: str) -> str:
        """Apply broker-specific symbol name override (e.g. XAGUSD→XAGUSD.x on GFT)."""
        return self._symbol_overrides.get(mt5_sym, mt5_sym)

    # ── Connection ─────────────────────────────────────────────────────────────

    def _connect(self):
        if not _MT5_AVAILABLE:
            raise MT5ConnectionError("MetaTrader5 package not installed")

        creds    = self._credentials or {}
        login    = int(creds.get('login',    os.getenv('MT5_LOGIN',    '0')))
        password = creds.get('password', os.getenv('MT5_PASSWORD', ''))
        server   = creds.get('server',   os.getenv('MT5_SERVER',   ''))

        if not login or not password or not server:
            raise MT5ConnectionError(
                "MT5 credentials missing — set MT5_LOGIN/MT5_PASSWORD/MT5_SERVER in .env"
            )

        # ── Multi-account terminal isolation ────────────────────────────────────
        # Pass path= so this connector binds to ONE specific portable terminal.
        # Without path=, both FTMO and GFT bind to the same terminal → account
        # switching disables Algo Trading on whichever session connected first.
        # ────────────────────────────────────────────────────────────────────────
        if self._terminal_path and os.path.isfile(self._terminal_path):
            logger.info(
                f"MT5 connecting via dedicated terminal: {self._terminal_path} "
                f"login={login} server={server}"
            )
            ok = mt5.initialize(
                path     = self._terminal_path,
                login    = login,
                password = password,
                server   = server,
            )
        else:
            if self._terminal_path:
                logger.warning(
                    f"MT5 terminal not found at {self._terminal_path!r} — "
                    f"falling back to system-default terminal. "
                    f"Algo Trading isolation NOT guaranteed. "
                    f"See C:\\CB6_MT5\\README_SETUP.md"
                )
            ok = mt5.initialize(login=login, password=password, server=server)

        if not ok:
            raise MT5ConnectionError(f"MT5 initialize failed: {mt5.last_error()}")

        info = mt5.account_info()
        if not info:
            raise MT5ConnectionError("MT5 connected but account_info() returned None")

        # ── Account contamination guard ──────────────────────────────────────────
        # Verify connected login matches what we requested. If there's a mismatch
        # the terminal connected to a different account — refuse to trade.
        if info.login != login:
            mt5.shutdown()
            raise MT5ConnectionError(
                f"ACCOUNT MISMATCH: requested login={login}, "
                f"connected login={info.login}. "
                f"Refusing to trade — check terminal_path and account config."
            )

        logger.info(
            f"MT5 connected — login={info.login} "
            f"balance=${info.balance:.2f} server={info.server}"
        )

    def disconnect(self):
        if not self._paper and _MT5_AVAILABLE:
            mt5.shutdown()
            logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        if not _MT5_AVAILABLE:
            return False
        try:
            info = mt5.terminal_info()
            if info is None:
                return False
            # terminal_info() only checks local API bridge — not broker connectivity.
            # connected=True means the terminal has an active broker session.
            return bool(getattr(info, 'connected', False))
        except Exception:
            return False

    def ensure_connected(self, max_retries: int = 3) -> bool:
        if self._paper or not _MT5_AVAILABLE:
            return True
        if self.is_connected():
            return True

        login = int((self._credentials or {}).get('login', os.getenv('MT5_LOGIN', '0')))
        logger.warning(f"MT5 broker session lost (login={login}) — attempting reconnect...")
        for attempt in range(1, max_retries + 1):
            try:
                _t = threading.Thread(target=mt5.shutdown, daemon=True)
                _t.start()
                _t.join(timeout=5)
                time.sleep(2)
                self._connect()
                if self.is_connected():
                    logger.info(f"MT5 reconnected on attempt {attempt} (login={login})")
                    try:
                        self._broadcast_alert(f"MT5 Reconnected — login={login} attempt={attempt}")
                    except Exception:
                        pass
                    return True
            except Exception as e:
                logger.error(f"MT5 reconnect attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    time.sleep(10 * attempt)

        logger.error("MT5 reconnect failed after all attempts")
        try:
            self._broadcast_alert(f"MT5 RECONNECT FAILED — login={login} — cannot place orders, check terminal")
        except Exception:
            pass
        return False

    # ── Account ────────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        if self._paper or not _MT5_AVAILABLE:
            return 0.0
        try:
            info = mt5.account_info()
            return float(info.balance) if info else 0.0
        except Exception as e:
            logger.error(f"MT5 get_balance: {e}")
            return 0.0

    def get_equity(self) -> float:
        if self._paper or not _MT5_AVAILABLE:
            return 0.0
        try:
            info = mt5.account_info()
            return float(info.equity) if info else 0.0
        except Exception as e:
            logger.error(f"MT5 get_equity: {e}")
            return 0.0

    # ── Market data ────────────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
        if self._paper or not _MT5_AVAILABLE:
            return self._klines_yfinance(symbol, interval, limit)
        return self._klines_mt5(symbol, interval, limit)

    def _klines_mt5(self, symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            tf      = _TF_MAP.get(interval, 15)
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))

            # Ensure symbol is in Market Watch — required on fresh terminals
            # (GFT/GoatFunded terminals start with no subscriptions after first launch)
            if not mt5.symbol_select(mt5_sym, True):
                logger.warning(f"MT5: symbol {mt5_sym!r} not available on this broker — skip")
                return None

            rates = mt5.copy_rates_from_pos(mt5_sym, tf, 0, limit)
            if rates is None or len(rates) == 0:
                logger.warning(f"MT5: no data for {symbol}")
                return None

            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            df = df.set_index('time')
            df = df.rename(columns={'tick_volume': 'volume'})
            cols = [c for c in ['open', 'high', 'low', 'close', 'volume', 'spread'] if c in df.columns]
            return df[cols].astype(float)
        except Exception as e:
            logger.error(f"MT5 klines ({symbol}): {e}")
            return None

    def _klines_yfinance(self, symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
        from forex_engine.forex_instruments import INSTRUMENTS
        if not _YF_AVAILABLE:
            logger.error("yfinance not available")
            return None
        try:
            cfg    = INSTRUMENTS.get(symbol, {})
            ticker = cfg.get('yf_ticker', symbol)

            yf_iv_map = {'1m': '1m', '2m': '2m', '3m': '5m', '5m': '5m', '15m': '15m',
                         '30m': '30m', '45m': '30m', '1h': '60m', '4h': '1d', '1d': '1d'}
            # Note: yfinance has no 3m/45m intervals — '3m'→'5m', '45m'→'30m' for paper mode
            yf_iv = yf_iv_map.get(interval, '15m')

            mins_per = {'1m': 1, '2m': 2, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
                        '45m': 45, '1h': 60, '4h': 240, '1d': 1440}
            total_m = mins_per.get(interval, 15) * limit
            if   total_m <= 7   * 1440: period = '7d'
            elif total_m <= 30  * 1440: period = '30d'
            elif total_m <= 60  * 1440: period = '60d'
            else:                        period = '1y'

            data = yf.download(ticker, period=period, interval=yf_iv,
                               progress=False, auto_adjust=True)
            if data is None or data.empty:
                return None

            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [c[0].lower() for c in data.columns]
            else:
                data.columns = [c.lower() for c in data.columns]

            df = data[['open', 'high', 'low', 'close', 'volume']].copy().dropna()
            if len(df) > limit:
                df = df.iloc[-limit:]
            return df.astype(float)
        except Exception as e:
            logger.error(f"yfinance klines ({symbol}): {e}")
            return None

    def get_price(self, symbol: str) -> Optional[float]:
        if self._paper or not _MT5_AVAILABLE:
            return self._price_yfinance(symbol)
        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            mt5.symbol_select(mt5_sym, True)   # ensure in Market Watch
            tick    = mt5.symbol_info_tick(mt5_sym)
            if tick:
                return round((tick.bid + tick.ask) / 2, 5)
            return None
        except Exception as e:
            logger.error(f"MT5 get_price ({symbol}): {e}")
            return None

    def _price_yfinance(self, symbol: str) -> Optional[float]:
        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg    = INSTRUMENTS.get(symbol, {})
            ticker = cfg.get('yf_ticker', symbol)
            data   = yf.download(ticker, period='5d', interval='5m',
                                 progress=False, auto_adjust=True)
            if data is None or data.empty:
                return None
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [c[0].lower() for c in data.columns]
            else:
                data.columns = [c.lower() for c in data.columns]
            return float(data['close'].iloc[-1])
        except Exception:
            return None

    def get_spread(self, symbol: str) -> Optional[float]:
        if self._paper or not _MT5_AVAILABLE:
            return None
        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            mt5.symbol_select(mt5_sym, True)   # ensure in Market Watch
            tick    = mt5.symbol_info_tick(mt5_sym)
            if tick:
                return round(tick.ask - tick.bid, 6)
            return None
        except Exception as e:
            logger.error(f"MT5 get_spread ({symbol}): {e}")
            return None

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_market_order(self, symbol: str, direction: str, lots: float,
                           sl: float, tp: float = 0.0, magic: int = 62002) -> Optional[dict]:
        if self._paper:
            logger.info(f"[PAPER] {symbol} {direction} {lots}L SL={sl:.5f} TP={tp:.5f}")
            return {'ticket': 0, 'paper': True, 'symbol': symbol,
                    'direction': direction, 'lots': lots, 'price': 0.0}

        if not _MT5_AVAILABLE or not self.ensure_connected():
            return None

        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            mt5.symbol_select(mt5_sym, True)
            tick    = mt5.symbol_info_tick(mt5_sym)
            if not tick:
                # No tick = broker session likely dropped even though terminal_info shows connected.
                # Force a hard reconnect and retry once.
                logger.warning(f"MT5: no tick for {mt5_sym} — forcing reconnect and retry")
                self._connected = False
                if self.ensure_connected():
                    mt5.symbol_select(mt5_sym, True)
                    tick = mt5.symbol_info_tick(mt5_sym)
                if not tick:
                    logger.error(f"MT5: no tick for {mt5_sym} after reconnect — broker session unavailable")
                    return None

            is_long    = direction in ('BUY', 'BULLISH')
            order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
            price      = tick.ask if is_long else tick.bid

            request = {
                'action'      : mt5.TRADE_ACTION_DEAL,
                'symbol'      : mt5_sym,
                'volume'      : lots,
                'type'        : order_type,
                'price'       : price,
                'sl'          : sl,
                'tp'          : tp if tp > 0 else 0.0,
                'deviation'   : 20,
                'magic'       : magic,
                'comment'     : 'CB6_Quantum',
                'type_time'   : mt5.ORDER_TIME_GTC,
                'type_filling': mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                fill_price = result.price
                if not fill_price:
                    # MT5 sometimes returns 0 in result.price for market orders —
                    # fetch the actual open price from the live position
                    pos = mt5.positions_get(ticket=result.order)
                    if pos:
                        fill_price = pos[0].price_open
                    else:
                        fill_price = price  # tick ask/bid captured just before send
                logger.info(
                    f"MT5 order filled: {symbol} {direction} {lots}L "
                    f"ticket={result.order} price={fill_price}"
                )
                return {'ticket': result.order, 'price': fill_price,
                        'symbol': symbol, 'direction': direction, 'lots': lots}
            else:
                err = result.comment if result else 'no result'
                retcode = result.retcode if result else -1
                logger.error(f"MT5 order failed: {symbol} {direction} — retcode={retcode} {err}")
                return None
        except Exception as e:
            logger.error(f"MT5 place_market_order ({symbol}): {e}")
            return None

    def place_limit_order(self, symbol: str, direction: str, lots: float,
                          entry: float, sl: float, tp: float = 0.0,
                          magic: int = 62002,
                          expiry: 'datetime' = None) -> Optional[dict]:
        """
        Place a pending limit order that fills when price retraces to `entry`.
        MT5 auto-cancels at `expiry` (UTC datetime) if not filled.
        Returns {'ticket', 'price', 'order_type': 'LIMIT'} or None.
        """
        if self._paper:
            logger.info(f"[PAPER] LIMIT {symbol} {direction} {lots}L @ {entry:.5f}")
            return {'ticket': 0, 'paper': True, 'price': entry,
                    'order_type': 'LIMIT', 'symbol': symbol,
                    'direction': direction, 'lots': lots}

        if not _MT5_AVAILABLE or not self.ensure_connected():
            return None

        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            mt5.symbol_select(mt5_sym, True)

            is_long    = direction in ('BUY', 'BULLISH')
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_long else mt5.ORDER_TYPE_SELL_LIMIT
            expiry_ts  = int(expiry.timestamp()) if expiry else 0
            time_type  = mt5.ORDER_TIME_SPECIFIED if expiry_ts else mt5.ORDER_TIME_GTC

            request = {
                'action'    : mt5.TRADE_ACTION_PENDING,
                'symbol'    : mt5_sym,
                'volume'    : lots,
                'type'      : order_type,
                'price'     : entry,
                'sl'        : sl,
                'tp'        : tp if tp > 0 else 0.0,
                'deviation' : 20,
                'magic'     : magic,
                'comment'   : 'CB6_Quantum_LMT',
                'type_time' : time_type,
                'expiration': expiry_ts,
            }
            result = mt5.order_send(request)
            # Pending orders return TRADE_RETCODE_PLACED (10008), not DONE (10009)
            if result and result.retcode in (mt5.TRADE_RETCODE_PLACED,
                                             mt5.TRADE_RETCODE_DONE):
                logger.info(
                    f"MT5 limit placed: {symbol} {direction} {lots}L "
                    f"@ {entry:.5f} ticket={result.order}"
                )
                return {'ticket': result.order, 'price': entry,
                        'order_type': 'LIMIT', 'symbol': symbol,
                        'direction': direction, 'lots': lots}
            err = result.comment if result else 'no result'
            logger.error(
                f"MT5 limit failed: {symbol} {direction} — "
                f"{err} (retcode={result.retcode if result else 'N/A'})"
            )
            return None
        except Exception as e:
            logger.error(f"MT5 place_limit_order ({symbol}): {e}")
            return None

    def cancel_order(self, ticket: int) -> bool:
        """Cancel a pending MT5 order by its order ticket."""
        if self._paper:
            return True
        if not _MT5_AVAILABLE:
            return False
        try:
            result = mt5.order_send({'action': mt5.TRADE_ACTION_REMOVE, 'order': ticket})
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"MT5 order cancelled: ticket={ticket}")
                return True
            logger.error(
                f"MT5 cancel failed: ticket={ticket} — "
                f"{result.comment if result else 'no result'}"
            )
            return False
        except Exception as e:
            logger.error(f"MT5 cancel_order ({ticket}): {e}")
            return False

    def get_live_positions(self, magic: int = None) -> list:
        """Return all open positions (optionally filtered by magic number)."""
        if self._paper or not _MT5_AVAILABLE:
            return []
        try:
            positions = mt5.positions_get() or []
            result = []
            for p in positions:
                if magic is not None and p.magic != magic:
                    continue
                result.append({
                    'ticket'    : p.ticket,
                    'symbol'    : p.symbol,
                    'lots'      : p.volume,
                    'price_open': p.price_open,
                    'price_curr': p.price_current,
                    'profit'    : p.profit,
                    'magic'     : p.magic,
                    'type'      : 'BUY' if p.type == 0 else 'SELL',
                })
            return result
        except Exception as e:
            logger.error(f"MT5 get_live_positions: {e}")
            return []

    def get_last_deal_for_ticket(self, ticket: int) -> tuple:
        """
        Fetch the closing deal for a position ticket from MT5 deal history.
        Returns (close_price, profit_usd) or (None, None) if not found.
        """
        if self._paper or not _MT5_AVAILABLE:
            return None, None
        try:
            from datetime import datetime, timedelta
            date_from = datetime(2020, 1, 1)
            date_to   = datetime.now() + timedelta(days=1)
            deals = mt5.history_deals_get(date_from, date_to, position=ticket)
            if not deals:
                return None, None
            # The closing deal is the one with entry == DEAL_ENTRY_OUT (1)
            for d in reversed(deals):
                if d.entry == 1:  # DEAL_ENTRY_OUT
                    return float(d.price), float(d.profit)
            return None, None
        except Exception as e:
            logger.error(f"MT5 get_last_deal_for_ticket ({ticket}): {e}")
            return None, None

    def get_pending_order(self, ticket: int) -> Optional[dict]:
        """Return pending order info if it still exists, or None if filled/expired."""
        if self._paper:
            return None
        if not _MT5_AVAILABLE:
            return None
        try:
            orders = mt5.orders_get(ticket=ticket)
            if orders:
                o = orders[0]
                return {'ticket': o.ticket, 'price_open': o.price_open,
                        'type': o.type, 'volume': o.volume_current}
            return None
        except Exception as e:
            logger.error(f"MT5 get_pending_order ({ticket}): {e}")
            return None

    def _get_filling_mode(self, mt5_sym: str) -> int:
        """
        Return the first supported ORDER_FILLING_* constant for this symbol.
        MT5 symbol_info().filling_mode is a bitmask:
          bit 0 (1) = FOK, bit 1 (2) = IOC, bit 2 (4) = RETURN.
        Falls back to IOC if the info is unavailable.
        """
        try:
            info = mt5.symbol_info(mt5_sym)
            if info is None:
                return mt5.ORDER_FILLING_IOC
            fm = info.filling_mode
            if fm & 1:    # FOK
                return mt5.ORDER_FILLING_FOK
            if fm & 2:    # IOC
                return mt5.ORDER_FILLING_IOC
            if fm & 4:    # RETURN (fill remainder)
                return mt5.ORDER_FILLING_RETURN
        except Exception:
            pass
        return mt5.ORDER_FILLING_IOC

    def close_position(self, symbol: str, ticket: int, lots: float,
                       direction: str, magic: int = 62002) -> bool:
        if self._paper:
            logger.info(f"[PAPER] Close {symbol} ticket={ticket} {lots}L")
            return True

        if not _MT5_AVAILABLE or not self.ensure_connected():
            return False

        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            mt5.symbol_select(mt5_sym, True)
            tick    = mt5.symbol_info_tick(mt5_sym)
            if not tick:
                return False

            is_long      = direction in ('BUY', 'BULLISH')
            close_type   = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
            price        = tick.bid if is_long else tick.ask
            filling_mode = self._get_filling_mode(mt5_sym)

            request = {
                'action'      : mt5.TRADE_ACTION_DEAL,
                'symbol'      : mt5_sym,
                'volume'      : lots,
                'type'        : close_type,
                'position'    : ticket,
                'price'       : price,
                'deviation'   : 20,
                'magic'       : magic,
                'comment'     : 'CB6_Quantum_close',
                'type_time'   : mt5.ORDER_TIME_GTC,
                'type_filling': filling_mode,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"MT5 closed: {symbol} ticket={ticket}")
                return True
            retcode = result.retcode if result else -1
            logger.error(f"MT5 close failed: {symbol} ticket={ticket} — "
                         f"retcode={retcode} {result.comment if result else 'no result'}")
            return False
        except Exception as e:
            logger.error(f"MT5 close_position ({symbol}): {e}")
            return False

    def modify_sl(self, symbol: str, ticket: int, new_sl: float,
                  tp: float = None) -> bool:
        """
        Modify SL on an open position.
        tp: explicit take-profit to keep. If None, the broker's current TP is preserved.
            Pass tp=0.0 only if you intentionally want to remove the TP.
        """
        if self._paper:
            logger.info(f"[PAPER] Modify SL: {symbol} ticket={ticket} → {new_sl:.5f}")
            return True

        if not _MT5_AVAILABLE:
            return False

        from forex_engine.forex_instruments import INSTRUMENTS
        try:
            cfg     = INSTRUMENTS.get(symbol, {})
            mt5_sym = self._resolve_sym(cfg.get('mt5_symbol', symbol))
            pos     = mt5.positions_get(ticket=ticket)
            if not pos:
                logger.warning(f"MT5 modify_sl: no position found for ticket={ticket} {symbol}")
                return False
            p = pos[0]
            # Use caller-supplied TP if given; otherwise keep whatever the broker has.
            # Never accidentally send tp=0 unless the caller explicitly chose it.
            effective_tp = tp if tp is not None else p.tp
            request = {
                'action'  : mt5.TRADE_ACTION_SLTP,
                'symbol'  : mt5_sym,
                'sl'      : new_sl,
                'tp'      : effective_tp,
                'position': ticket,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(
                    f"MT5 SL modified: {symbol} ticket={ticket} "
                    f"sl={new_sl:.5f} tp={effective_tp:.5f}"
                )
                return True
            logger.error(
                f"MT5 modify_sl failed: {symbol} ticket={ticket} — "
                f"retcode={result.retcode if result else 'N/A'} "
                f"{result.comment if result else 'no result'}"
            )
            return False
        except Exception as e:
            logger.error(f"MT5 modify_sl ({symbol}): {e}")
            return False

    # ── REST candle polling ────────────────────────────────────────────────────

    def start_polling(self, symbols: List[str], interval: str,
                      on_closed_candle: Callable, poll_secs: int = 60,
                      on_intracandle: Callable = None):
        """
        Poll MT5 every poll_secs for new candles.

        on_closed_candle(sym, df): fires when a new candle bar opens (old bar confirmed closed).
        on_intracandle(sym, df):   fires every poll_secs with the latest df including the
                                   current FORMING bar — enables 15s intra-candle scanning.
                                   df[-1] is the live bar with current high/low/close from MT5.
        """
        self._running = True

        def _poll():
            last_ts: Dict[str, object] = {s: None for s in symbols}
            while self._running:
                if not self.ensure_connected():
                    logger.error("Skipping poll — MT5 unavailable")
                    time.sleep(poll_secs)
                    continue
                for sym in symbols:
                    try:
                        df = self.get_klines(sym, interval, 300)
                        if df is None or df.empty:
                            continue
                        latest = df.index[-1]
                        if last_ts[sym] is None:
                            last_ts[sym] = latest
                            # Still call intracandle on first poll so scanner starts immediately
                            if on_intracandle:
                                on_intracandle(sym, df)
                            continue
                        if latest > last_ts[sym]:
                            last_ts[sym] = latest
                            on_closed_candle(sym, df)
                        # Always fire intracandle — df[-1] has live OHLC from MT5
                        if on_intracandle:
                            on_intracandle(sym, df)
                    except Exception as e:
                        logger.error(f"Poll error ({sym}): {e}")
                time.sleep(poll_secs)

        self._poll_thread = threading.Thread(target=_poll, daemon=True,
                                             name="MT5Poller")
        self._poll_thread.start()
        logger.info(f"MT5 poller started — {symbols} interval={interval} poll={poll_secs}s")

    def stop_polling(self):
        self._running = False
        logger.info("MT5 poller stopped")
