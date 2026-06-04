"""
data/truedata_feed.py — TrueData primary data feed for CB6 Quantum.

Uses the official truedata_ws library (TD class) for both historical REST
data and live WebSocket tick streaming.

Public API (consumed by scanner/data_fetcher.py, scanner/live_price.py,
scanner/websocket_feed.py — none of those files need to change):

    get_manager() -> TrueDataManager
    TrueDataManager.connect_hist() -> bool
    TrueDataManager.connect_live(symbols) -> bool
    TrueDataManager.get_historical_bars(td_symbol, bar_size, days) -> DataFrame | None
    TrueDataManager.get_ltp(td_symbol) -> float | None
    TrueDataManager.is_hist_ready -> bool
    TrueDataManager.is_live_ready -> bool

Convenience wrappers (Fyers-format → TrueData auto-conversion):
    get_ltp(fyers_symbol) -> float | None
    get_historical_bars(fyers_symbol, timeframe, days) -> DataFrame | None
"""

from __future__ import annotations

import enum
import logging
import os
import queue
import sys
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.logger import logger

# Load .env (os.getenv is NOT enough on Windows — .env isn't sourced automatically)
try:
    from dotenv import dotenv_values as _dotenv_values
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    _env_file = _dotenv_values(_env_path)
except Exception:
    _env_file = {}

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env_file.get(key) or default

_TRUEDATA_USER = _env("TRUEDATA_USER")
_TRUEDATA_PASS = _env("TRUEDATA_PASSWORD")
_TRUEDATA_PORT = int(_env("TRUEDATA_WS_PORT", "8086"))

# ---------------------------------------------------------------------------
# Symbol + timeframe maps
# ---------------------------------------------------------------------------

_FYERS_TO_TD: dict[str, str] = {
    "NSE:NIFTY50-INDEX":    "NIFTY 50",
    "NSE:NIFTYBANK-INDEX":  "NIFTY BANK",
    "NSE:FINNIFTY-INDEX":   "FINNIFTY",
    "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
    "NSE:NIFTY50-FUT":      "NIFTY-I",
    "NSE:NIFTYBANK-FUT":    "BANKNIFTY-I",
    "NSE:BANKNIFTY-FUT":    "BANKNIFTY-I",
    "NSE:FINNIFTY-FUT":     "FINNIFTY-I",
    "NSE:MIDCPNIFTY-FUT":   "MIDCPNIFTY-I",
}

# Reverse map: TrueData symbol → canonical Fyers symbol used by scanner/trader.
# trade_triggers.py registers TickWatcher entries under Fyers format — ticks
# dispatched under TrueData format would never fire those triggers.
_TD_TO_FYERS: dict[str, str] = {
    "NIFTY-I":     "NSE:NIFTY50-FUT",
    "BANKNIFTY-I": "NSE:NIFTYBANK-FUT",
    "FINNIFTY-I":  "NSE:FINNIFTY-FUT",
    "MIDCPNIFTY-I":"NSE:MIDCPNIFTY-FUT",
    "NIFTY 50":    "NSE:NIFTY50-INDEX",
    "NIFTY BANK":  "NSE:NIFTYBANK-INDEX",
    "FINNIFTY":    "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY":  "NSE:MIDCPNIFTY-INDEX",
}

# TrueData official bar_size strings (no trailing space/s)
_TF_TO_BARSIZE: dict[str, str] = {
    "1":   "1min",
    "3":   "3min",
    "5":   "5min",
    "10":  "10min",
    "15":  "15min",
    "30":  "30min",
    "60":  "60min",
    "D":   "eod",
    "W":   "weekly",
    "M":   "monthly",
}


# Symbols whose 1-minute bars have critically low historical coverage (< 30%).
# Any request for 1min data on these symbols is auto-upgraded to 3min and a
# structural warning is logged so the scanner never operates on sparse bars.
_FINNIFTY_1M_BLOCKED = frozenset({"FINNIFTY-I", "FINNIFTY"})


def _guard_finnifty_1m(td_symbol: str, bar_size: str) -> str:
    """
    Intercept 1-min requests for FINNIFTY symbols.

    Historical audit showed FINNIFTY-I 1min coverage at ~24% — operating on
    those bars produces erratic MSS/FVG detections.  Force a minimum of 3min.
    Returns (possibly upgraded) bar_size and logs a structural warning if changed.
    """
    if bar_size == "1min" and td_symbol in _FINNIFTY_1M_BLOCKED:
        logger.warning(
            "FINNIFTY 1m BLOCKED: '%s' 1min coverage ~24%% — auto-upgrading to 3min. "
            "Scanner will use 3min bars for FINNIFTY to prevent erratic detections.",
            td_symbol,
        )
        return "3min"
    return bar_size


def fyers_to_td_symbol(fyers_sym: str) -> str:
    """Map a Fyers symbol string to TrueData format."""
    td = _FYERS_TO_TD.get(fyers_sym)
    if td:
        return td
    if ":" in fyers_sym:
        sym = fyers_sym.split(":", 1)[1]
        for suffix in ("-EQ", "-BE", "-INDEX", "-FUT"):
            if sym.endswith(suffix):
                sym = sym[: -len(suffix)]
                break
        return sym
    return fyers_sym


def tf_to_bar_size(timeframe: str) -> str:
    """Convert Fyers timeframe string to TrueData bar_size string."""
    return _TF_TO_BARSIZE.get(str(timeframe), "15min")


def _safe_log_error(msg: str, exc: Exception) -> None:
    text = f"{msg}: {exc}"
    if _TRUEDATA_PASS and _TRUEDATA_PASS in text:
        text = text.replace(_TRUEDATA_PASS, "***")
    logger.error(text)


# ---------------------------------------------------------------------------
# Connection state machine
# ---------------------------------------------------------------------------


class _ConnState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"


# ---------------------------------------------------------------------------
# Module-level live connection guard.
# Prevents duplicate TD live objects even if the module is imported under two
# different paths (e.g. data.truedata_feed vs truedata_feed).
# Only ONE live TD object may exist in the process at any time.
# ---------------------------------------------------------------------------

_GLOBAL_LIVE_LOCK             = threading.Lock()
_GLOBAL_LIVE_TD_OBJ           = None      # the ONE authoritative live TD instance
_LIVE_LAST_CONNECT_TS         = 0.0       # epoch of last successful connect
_LIVE_MIN_RECONNECT_INTERVAL  = 30.0      # min seconds between live reconnects


# ---------------------------------------------------------------------------
# TrueDataManager singleton
# ---------------------------------------------------------------------------


class TrueDataManager:
    """
    Thread-safe singleton managing TrueData live + historical connections.

    Uses the official truedata_ws.TD class for both connections.
    Ticks are dispatched off the WS callback thread via a queue.
    """

    _instance: "TrueDataManager | None" = None
    _class_lock = threading.Lock()

    def __new__(cls) -> "TrueDataManager":
        with cls._class_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._td_hist = None       # TD instance for historical (live_port=None)
        self._td_live = None       # TD instance for live (live_port=8086)
        self._hist_state: _ConnState = _ConnState.DISCONNECTED
        self._live_state: _ConnState = _ConnState.DISCONNECTED
        self._lock = threading.Lock()
        # symbol → req_id mapping for live data lookup
        self._sym_to_req: dict[str, int] = {}
        # Off-thread tick dispatch queue
        self._tick_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._tick_worker = threading.Thread(
            target=self._tick_dispatch_loop, daemon=True, name="td-tick-worker"
        )
        self._tick_worker.start()

        # Forward-fill tracking for MIDCPNIFTY — 87 historical gaps detected.
        # If no tick arrives within _MIDCP_GAP_SECS during a Silver Bullet window,
        # we replay the last known tick so the scanner never sees a stale row.
        self._midcp_last_tick: dict = {}       # {td_sym: {ltp, volume, ts}}
        self._midcp_last_ts:   dict = {}       # {td_sym: float (epoch)}
        self._midcp_lock = threading.Lock()
        _MIDCP_SYMBOLS = frozenset({"MIDCPNIFTY-I", "MIDCPNIFTY"})
        self._midcp_symbols = _MIDCP_SYMBOLS
        # Gap threshold: if no tick for > 45s during a volatile 3m SB window, forward-fill
        self._MIDCP_GAP_SECS = 45

    # ------------------------------------------------------------------
    # Historical
    # ------------------------------------------------------------------

    def connect_hist(self) -> bool:
        """Connect for historical REST data. Returns True on success."""
        with self._lock:
            if self._hist_state == _ConnState.CONNECTED:
                return True
            if self._hist_state == _ConnState.CONNECTING:
                return False
            self._hist_state = _ConnState.CONNECTING

        if not _TRUEDATA_USER or not _TRUEDATA_PASS:
            logger.warning("TrueData: credentials missing (TRUEDATA_USER/TRUEDATA_PASSWORD)")
            with self._lock:
                self._hist_state = _ConnState.DISCONNECTED
            return False

        try:
            from truedata_ws.websocket.TD import TD
            td = TD(
                _TRUEDATA_USER,
                _TRUEDATA_PASS,
                live_port=None,
                historical_api=True,
                log_level=logging.WARNING,
            )
            with self._lock:
                self._td_hist = td
                self._hist_state = _ConnState.CONNECTED
            logger.info("TrueData: historical connection established")
            return True
        except Exception as exc:
            _safe_log_error("TrueData: historical connect failed", exc)
            with self._lock:
                self._td_hist = None
                self._hist_state = _ConnState.DISCONNECTED
            return False

    def get_historical_bars(
        self,
        td_symbol: str,
        bar_size: str,
        days: int = 15,
    ) -> "pd.DataFrame | None":
        """
        Fetch OHLCV bars from TrueData.

        Returns DataFrame(timestamp, open, high, low, close, volume, oi) or None.
        Trial limit: 15 days of bar data.
        """
        with self._lock:
            td = self._td_hist
            state = self._hist_state

        if state != _ConnState.CONNECTED or td is None:
            if not self.connect_hist():
                return None
            with self._lock:
                td = self._td_hist
            if td is None:
                return None

        try:
            bar_size = _guard_finnifty_1m(td_symbol, bar_size)
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=min(days, 15))

            raw = td.get_historic_data(
                td_symbol,
                bar_size=bar_size,
                start_time=start_dt,
                end_time=end_dt,
            )
            if not raw:
                logger.warning("TrueData: no data for %s (%s, %dd)", td_symbol, bar_size, days)
                return None

            df = pd.DataFrame(raw)
            df = _normalize_columns(df)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp").reset_index(drop=True)

            logger.info("TrueData: %d bars fetched for %s (%s)", len(df), td_symbol, bar_size)
            return df

        except Exception as exc:
            _safe_log_error(f"TrueData get_historical_bars({td_symbol}, {bar_size}, {days}d)", exc)
            self._reset_hist_on_error(exc)
            return None

    def get_last_n_bars(
        self,
        td_symbol: str,
        bar_size: str,
        n: int = 200,
    ) -> "pd.DataFrame | None":
        """Fetch last N bars."""
        with self._lock:
            td = self._td_hist
            state = self._hist_state

        if state != _ConnState.CONNECTED or td is None:
            if not self.connect_hist():
                return None
            with self._lock:
                td = self._td_hist
            if td is None:
                return None

        try:
            bar_size = _guard_finnifty_1m(td_symbol, bar_size)
            raw = td.get_n_historical_bars(td_symbol, no_of_bars=n, bar_size=bar_size)
            if not raw:
                return None
            df = pd.DataFrame(raw)
            df = _normalize_columns(df)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception as exc:
            _safe_log_error(f"TrueData get_last_n_bars({td_symbol}, {bar_size}, n={n})", exc)
            self._reset_hist_on_error(exc)
            return None

    def _reset_hist_on_error(self, exc: Exception) -> None:
        err_text = str(exc).lower()
        session_errors = ("expired", "invalid token", "unauthorized", "401", "session", "not connected")
        if any(kw in err_text for kw in session_errors):
            with self._lock:
                if self._hist_state == _ConnState.CONNECTED:
                    logger.warning("TrueData: hist session expired — will reconnect on next call")
                    self._td_hist = None
                    self._hist_state = _ConnState.DISCONNECTED

    # ------------------------------------------------------------------
    # Live WebSocket
    # ------------------------------------------------------------------

    def connect_live(self, symbols: list[str]) -> bool:
        """Connect live WebSocket and subscribe to tick streaming.
        
        Uses a module-level lock to guarantee only ONE TD live object exists
        in the process at any time — prevents the double-connection storm.
        """
        global _GLOBAL_LIVE_TD_OBJ, _LIVE_LAST_CONNECT_TS

        # Module-level guard: only one live connect attempt at a time, process-wide
        if not _GLOBAL_LIVE_LOCK.acquire(blocking=False):
            logger.warning("TrueData: connect_live already in progress (global lock held) — skipping duplicate")
            with self._lock:
                self._live_state = _ConnState.DISCONNECTED
            return False

        try:
            # Cooldown: don't reconnect faster than every 30s
            elapsed = _time.time() - _LIVE_LAST_CONNECT_TS
            if elapsed < _LIVE_MIN_RECONNECT_INTERVAL:
                wait = _LIVE_MIN_RECONNECT_INTERVAL - elapsed
                logger.info("TrueData: live reconnect cooldown %.0fs remaining", wait)
                with self._lock:
                    self._live_state = _ConnState.DISCONNECTED
                return False

            with self._lock:
                if self._live_state == _ConnState.CONNECTED:
                    return True
                self._live_state = _ConnState.CONNECTING

            # Kill any existing global TD live object before creating a new one
            if _GLOBAL_LIVE_TD_OBJ is not None:
                try:
                    _GLOBAL_LIVE_TD_OBJ.disconnect()
                    logger.info("TrueData: disconnected stale live TD object")
                except Exception:
                    pass
                _GLOBAL_LIVE_TD_OBJ = None

            if not _TRUEDATA_USER or not _TRUEDATA_PASS:
                logger.warning("TrueData: credentials missing for live connection")
                with self._lock:
                    self._live_state = _ConnState.DISCONNECTED
                return False

            td = None
            try:
                from truedata_ws.websocket.TD import TD
                td = TD(
                    _TRUEDATA_USER,
                    _TRUEDATA_PASS,
                    live_port=_TRUEDATA_PORT,
                    historical_api=False,
                    log_level=logging.WARNING,
                )

                def _on_tick(tick_data):
                    self._tick_queue.put(("tick", tick_data))

                ws = td.live_websocket
                if ws is not None:
                    ws.trade_callback = _on_tick

                req_ids = td.start_live_data(symbols)

                sym_map: dict[str, int] = {}
                if req_ids and len(req_ids) == len(symbols):
                    for sym, rid in zip(symbols, req_ids):
                        sym_map[sym] = rid
                else:
                    cmap = getattr(td.live_websocket, "contract_mapping", {})
                    sym_map = {v: k for k, v in cmap.items()} if cmap else {}

                _GLOBAL_LIVE_TD_OBJ   = td
                _LIVE_LAST_CONNECT_TS = _time.time()

                with self._lock:
                    self._td_live   = td
                    self._sym_to_req = sym_map
                    self._live_state = _ConnState.CONNECTED

                logger.info("TrueData: live connected — %d symbols subscribed", len(symbols))
                return True

            except Exception as exc:
                _safe_log_error("TrueData: live connect failed", exc)
                if td is not None:
                    try:
                        td.disconnect()
                    except Exception:
                        pass
                with self._lock:
                    self._td_live    = None
                    self._sym_to_req = {}
                    self._live_state = _ConnState.DISCONNECTED
                return False

        finally:
            _GLOBAL_LIVE_LOCK.release()

    def add_live_symbols(self, symbols: list[str]) -> None:
        """Subscribe additional symbols to the live feed."""
        with self._lock:
            td = self._td_live
            state = self._live_state
        if state != _ConnState.CONNECTED or td is None:
            return
        try:
            req_ids = td.start_live_data(symbols)
            if req_ids:
                with self._lock:
                    for sym, rid in zip(symbols, req_ids):
                        self._sym_to_req[sym] = rid
            logger.debug("TrueData: subscribed extra symbols %s", symbols)
        except Exception as exc:
            _safe_log_error("TrueData add_live_symbols error", exc)

    def get_ltp(self, td_symbol: str) -> Optional[float]:
        """Get last traded price from live data cache. Returns None if feed is down."""
        with self._lock:
            td = self._td_live
            state = self._live_state
            rid = self._sym_to_req.get(td_symbol)

        if state != _ConnState.CONNECTED or td is None:
            return None

        try:
            if rid is not None:
                obj = td.live_data.get(rid)
                if obj is not None and obj.ltp is not None:
                    return float(obj.ltp)
            # Fallback: scan all live_data for matching symbol
            for obj in td.live_data.values():
                if getattr(obj, "symbol", None) == td_symbol and obj.ltp is not None:
                    return float(obj.ltp)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Tick dispatch
    # ------------------------------------------------------------------

    def _tick_dispatch_loop(self) -> None:
        while True:
            try:
                kind, data = self._tick_queue.get()
                if kind == "tick":
                    self._dispatch_tick(data)
            except Exception as exc:
                logger.debug("TrueData tick worker error: %s", exc)

    def _dispatch_tick(self, tick_data) -> None:
        try:
            td_sym = getattr(tick_data, "symbol", None)
            ltp_raw = getattr(tick_data, "ltp", None)
            if td_sym is None or ltp_raw is None:
                return
            ltp = float(ltp_raw)
            vol = int(getattr(tick_data, "ttq", 0) or 0)
            ts  = str(getattr(tick_data, "timestamp", ""))

            # Track last known tick for MIDCPNIFTY forward-fill.
            if td_sym in self._midcp_symbols:
                with self._midcp_lock:
                    self._midcp_last_tick[td_sym] = {"ltp": ltp, "volume": vol, "ts": ts}
                    self._midcp_last_ts[td_sym]   = _time.monotonic()

            # Extract bid/ask once — used in both the tick cache and the telemetry monitor.
            # Stored as None when absent so check_bidask_filter() still passes through safely.
            _raw_bid = getattr(tick_data, "best_bid", None)
            _raw_ask = getattr(tick_data, "best_ask", None)
            bid_val  = float(_raw_bid) if _raw_bid else None
            ask_val  = float(_raw_ask) if _raw_ask else None
            oi_val   = float(getattr(tick_data, "oi", 0) or 0) or None

            # Translate TrueData symbol → Fyers format so that:
            #   (a) websocket_feed._tick_cache is keyed by Fyers format (scanner reads it that way)
            #   (b) tick_watcher.on_tick fires with Fyers key (trade_triggers registers that way)
            fyers_sym = _TD_TO_FYERS.get(td_sym, td_sym)

            try:
                from scanner import websocket_feed
                tick_entry = {
                    "ltp"     : ltp,
                    "volume"  : vol,
                    "ts"      : ts,
                    "best_bid": bid_val,
                    "best_ask": ask_val,
                }
                with websocket_feed._lock:
                    websocket_feed._tick_cache[fyers_sym] = tick_entry
                    # Also keep TD-format key so get_ltp(td_symbol) still works
                    if fyers_sym != td_sym:
                        websocket_feed._tick_cache[td_sym] = tick_entry
            except Exception:
                pass

            try:
                from core.tick_watcher import get_watcher
                get_watcher().on_tick(fyers_sym, ltp)
            except Exception:
                pass

            # Data health monitoring — record tick time for staleness detection
            try:
                from data.data_health import get_monitor as _get_health
                _get_health().record_tick(td_sym, exchange_ts=None)
            except Exception:
                pass

            # Live-session telemetry — non-blocking, fail-safe
            try:
                from data.live_session_monitor import get_monitor
                from scanner.silver_bullet import is_silver_bullet_window
                in_sb, _ = is_silver_bullet_window()
                exch_ts  = None
                raw_ts   = getattr(tick_data, "timestamp", None)
                if raw_ts:
                    try:
                        import pandas as _pd
                        exch_ts = _pd.Timestamp(raw_ts).timestamp()
                    except Exception:
                        pass
                get_monitor().record_tick(
                    td_sym,
                    exchange_ts  = exch_ts,
                    local_ts     = _time.time(),
                    oi           = oi_val,
                    bid          = bid_val,
                    ask          = ask_val,
                    in_sb_window = in_sb,
                )
            except Exception:
                pass

        except Exception as exc:
            logger.debug("TrueData tick dispatch error: %s", exc)

    # ------------------------------------------------------------------
    # MIDCPNIFTY forward-fill (gap recovery)
    # ------------------------------------------------------------------

    def forward_fill_midcpnifty(self) -> list[str]:
        """
        For each MIDCPNIFTY symbol that has gone silent beyond _MIDCP_GAP_SECS,
        re-publish the last known tick to websocket_feed and tick_watcher so the
        scanner never reads a stale or missing row.

        Called by the scanner's 3-min polling loop (or a heartbeat thread).
        Returns list of symbols that were forward-filled.
        """
        filled: list[str] = []
        now = _time.monotonic()
        with self._midcp_lock:
            stale = {
                sym: tick
                for sym, tick in self._midcp_last_tick.items()
                if now - self._midcp_last_ts.get(sym, 0) > self._MIDCP_GAP_SECS
            }

        for td_sym, tick in stale.items():
            fyers_sym = _TD_TO_FYERS.get(td_sym, td_sym)
            try:
                from scanner import websocket_feed
                with websocket_feed._lock:
                    websocket_feed._tick_cache[fyers_sym] = tick
                    if fyers_sym != td_sym:
                        websocket_feed._tick_cache[td_sym] = tick
            except Exception:
                pass

            logger.warning(
                "MIDCPNIFTY forward-fill: %s silent >%ds — replaying last LTP %.2f",
                td_sym, self._MIDCP_GAP_SECS, tick["ltp"],
            )
            filled.append(td_sym)

        return filled

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        with self._lock:
            live = self._td_live
            self._td_live = None
            self._td_hist = None
            self._live_state = _ConnState.DISCONNECTED
            self._hist_state = _ConnState.DISCONNECTED
            self._sym_to_req = {}
        if live is not None:
            try:
                live.disconnect()
            except Exception:
                pass
        logger.info("TrueData: disconnected")

    @property
    def is_hist_ready(self) -> bool:
        with self._lock:
            return self._hist_state == _ConnState.CONNECTED

    @property
    def is_live_ready(self) -> bool:
        with self._lock:
            return self._live_state == _ConnState.CONNECTED


# ---------------------------------------------------------------------------
# Column normalizer
# ---------------------------------------------------------------------------


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename TrueData columns to scanner-expected names."""
    col_map: dict[str, str] = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ("time", "datetime", "date", "timestamp"):
            col_map[col] = "timestamp"
        elif lc in ("o", "open"):
            col_map[col] = "open"
        elif lc in ("h", "high"):
            col_map[col] = "high"
        elif lc in ("l", "low"):
            col_map[col] = "low"
        elif lc in ("c", "close", "ltp"):
            col_map[col] = "close"
        elif lc in ("v", "volume", "vol", "ttq"):
            col_map[col] = "volume"
        elif lc == "oi":
            col_map[col] = "oi"
    df = df.rename(columns=col_map)
    if "timestamp" not in df.columns and df.index.name:
        df = df.reset_index().rename(columns={df.index.name: "timestamp"})
    return df


# ---------------------------------------------------------------------------
# Module-level singleton + convenience functions
# ---------------------------------------------------------------------------

_td = TrueDataManager()


def get_manager() -> TrueDataManager:
    """Return the module-level TrueDataManager singleton."""
    return _td


def get_ltp(fyers_symbol: str) -> Optional[float]:
    """Get LTP for a Fyers-format symbol via TrueData live feed."""
    return _td.get_ltp(fyers_to_td_symbol(fyers_symbol))


def get_historical_bars(
    fyers_symbol: str,
    timeframe: str,
    days: int = 15,
) -> "pd.DataFrame | None":
    """
    Fetch historical OHLCV bars for a Fyers-format symbol.
    Converts symbol + timeframe to TrueData format automatically.
    """
    td_sym = fyers_to_td_symbol(fyers_symbol)
    bar_sz = _guard_finnifty_1m(td_sym, tf_to_bar_size(timeframe))
    return _td.get_historical_bars(td_sym, bar_sz, days)
