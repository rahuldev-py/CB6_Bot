"""
TrueData async WebSocket client.

Maintains a persistent WebSocket connection with:
- Auth token injection on subscribe
- Auto-reconnect with exponential back-off (cap 60 s)
- 30-second heartbeat
- Sequence gap detection
- Latency tracking (exchange timestamp vs. receive time)
- Thread-safe subscription management via asyncio primitives
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from .auth import TrueDataAuth
from .config import TrueDataConfig
from .exceptions import TrueDataConnectionError
from .models import MarketBar, MarketTick, ProviderHealth

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

_HEARTBEAT_INTERVAL = 30  # seconds
_MAX_RECONNECT_BACKOFF = 60  # seconds
_INITIAL_BACKOFF = 1  # seconds


class TrueDataWebSocketClient:
    """
    Async WebSocket client for TrueData real-time market data.

    Callbacks
    ---------
    Register callbacks before calling :meth:`connect`:

    .. code-block:: python

        client = TrueDataWebSocketClient(config, auth)
        client.on_tick = lambda tick: print(tick)
        await client.connect()
        await client.subscribe(["NIFTY-I", "BANKNIFTY-I"])

    All callbacks are invoked from the internal receive loop and should
    be non-blocking (or wrapped in ``asyncio.create_task``).

    Thread safety
    -------------
    All public async methods are safe to call from any coroutine.
    Internal state uses :class:`asyncio.Lock`.
    """

    def __init__(self, config: TrueDataConfig, auth: TrueDataAuth) -> None:
        self._config = config
        self._auth = auth

        # Callbacks — set by the caller
        self.on_tick: Optional[Callable[[MarketTick], None]] = None
        self.on_bar: Optional[Callable[[MarketBar], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        # State
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: set[str] = set()
        self._sub_lock = asyncio.Lock()
        self._running = False
        self._reconnect_count = 0
        self._error_count = 0
        self._last_heartbeat: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None

        # Per-symbol sequence tracking: symbol → last seq seen
        self._last_seq: dict[str, int] = {}
        # Per-symbol latency samples (ms)
        self._latency_samples: dict[str, list[float]] = {}

        # Background tasks
        self._recv_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Connect to TrueData WebSocket and start the receive loop.

        Blocks until the first successful connection is established.
        After that, reconnection happens automatically in the background.

        Raises
        ------
        TrueDataConnectionError
            If the initial connection attempt fails.
        """
        logger.info("Connecting to TrueData WebSocket: %s", self._config.ws_url)
        self._running = True
        await self._connect_once()
        self._recv_task = asyncio.create_task(self._recv_loop(), name="td_recv")
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="td_heartbeat"
        )
        logger.info("TrueData WebSocket connected")

    async def disconnect(self) -> None:
        """
        Gracefully close the WebSocket connection and stop all tasks.
        """
        logger.info("Disconnecting TrueData WebSocket")
        self._running = False

        for task in (self._recv_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception as exc:
                logger.warning("on_disconnect callback raised: %s", exc)

        logger.info("TrueData WebSocket disconnected")

    async def subscribe(self, symbols: list[str]) -> None:
        """
        Subscribe to real-time ticks for the given symbols.

        Parameters
        ----------
        symbols:
            List of TrueData symbol strings, e.g. ``["NIFTY-I", "BANKNIFTY-I"]``.
        """
        async with self._sub_lock:
            new_symbols = [s for s in symbols if s not in self._subscriptions]
            if not new_symbols:
                return

            logger.info("Subscribing to %s", new_symbols)
            self._subscriptions.update(new_symbols)

            if self._ws:
                await self._send_subscribe(new_symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        """
        Unsubscribe from real-time ticks for the given symbols.

        Parameters
        ----------
        symbols:
            List of TrueData symbol strings to remove.
        """
        async with self._sub_lock:
            removed = [s for s in symbols if s in self._subscriptions]
            self._subscriptions -= set(removed)

            if self._ws and removed:
                msg = {
                    "method": "unsub",
                    "params": {"symbols": removed, "token": self._auth.get_token()},
                }
                await self._safe_send(json.dumps(msg))
                logger.info("Unsubscribed from %s", removed)

    def get_health(self) -> ProviderHealth:
        """
        Return a current health snapshot.

        Returns
        -------
        ProviderHealth
        """
        connected = self._ws is not None and not self._ws.closed
        latency: Optional[float] = None

        # Average of last 10 samples across all symbols
        all_samples: list[float] = []
        for samples in self._latency_samples.values():
            all_samples.extend(samples[-10:])
        if all_samples:
            latency = sum(all_samples) / len(all_samples)

        status = "connected" if connected else "disconnected"
        if connected and self._reconnect_count > 0:
            status = f"connected (reconnects={self._reconnect_count})"

        return ProviderHealth(
            provider="truedata",
            connected=connected,
            last_heartbeat=self._last_heartbeat,
            last_tick_time=self._last_tick_time,
            reconnect_count=self._reconnect_count,
            error_count=self._error_count,
            latency_ms=latency,
            status=status,
        )

    # ------------------------------------------------------------------
    # Internal connection management
    # ------------------------------------------------------------------

    async def _connect_once(self) -> None:
        """Establish a single WebSocket connection (no retry)."""
        try:
            self._ws = await websockets.connect(
                self._config.ws_url,
                ping_interval=None,  # We manage heartbeats manually
                ping_timeout=None,
                close_timeout=10,
                max_size=10 * 1024 * 1024,  # 10 MB max message
            )
        except (OSError, WebSocketException) as exc:
            raise TrueDataConnectionError(
                f"Cannot connect to {self._config.ws_url}: {exc}", cause=exc
            ) from exc

        if self.on_connect:
            try:
                self.on_connect()
            except Exception as exc:
                logger.warning("on_connect callback raised: %s", exc)

        # Re-subscribe to all symbols on reconnect
        async with self._sub_lock:
            if self._subscriptions:
                await self._send_subscribe(list(self._subscriptions))

    async def _recv_loop(self) -> None:
        """Main receive loop with auto-reconnect."""
        backoff = float(_INITIAL_BACKOFF)

        while self._running:
            try:
                if not self._ws or self._ws.closed:
                    await self._reconnect(backoff)
                    backoff = min(backoff * 2, float(_MAX_RECONNECT_BACKOFF))
                    continue

                message = await self._ws.recv()
                backoff = float(_INITIAL_BACKOFF)  # reset on success
                await self._handle_message(message)

            except ConnectionClosed as exc:
                if not self._running:
                    break
                logger.warning("WebSocket connection closed: %s", exc)
                self._error_count += 1
                if self.on_disconnect:
                    try:
                        self.on_disconnect()
                    except Exception:
                        pass
                self._ws = None
                await asyncio.sleep(backoff)

            except asyncio.CancelledError:
                break

            except Exception as exc:
                if not self._running:
                    break
                logger.error("Unexpected error in recv loop: %s", exc)
                self._error_count += 1
                if self.on_error:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
                await asyncio.sleep(min(backoff, 5.0))

    async def _reconnect(self, backoff: float) -> None:
        """Attempt to reconnect once, logging the attempt."""
        self._reconnect_count += 1
        logger.info(
            "Reconnecting to TrueData (attempt #%d, backoff=%.1fs)",
            self._reconnect_count, backoff,
        )
        try:
            await self._connect_once()
            logger.info("Reconnected to TrueData WebSocket")
        except TrueDataConnectionError as exc:
            logger.warning("Reconnect failed: %s", exc)
            await asyncio.sleep(backoff)

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat every 30 seconds."""
        while self._running:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            if not self._running:
                break
            if self._ws and not self._ws.closed:
                await self._safe_send(json.dumps({"method": "heartbeat"}))
                logger.debug("Heartbeat sent")

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, raw_message: str | bytes) -> None:
        """Parse and dispatch an incoming WebSocket message."""
        receive_time = time.time()

        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            data = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("Failed to parse message: %s", exc)
            return

        method = data.get("method", "")

        if method == "heartbeat":
            self._last_heartbeat = datetime.now(tz=_IST)
            return

        if method in ("tick", "quote", ""):
            # A tick message — dispatch
            await self._dispatch_tick(data, receive_time)
            return

        if method == "bar":
            await self._dispatch_bar(data)
            return

        if data.get("status") == "ok":
            logger.debug("Subscription confirmed: %s", data)
            return

        logger.debug("Unknown message method='%s': %s", method, str(data)[:200])

    async def _dispatch_tick(self, data: dict, receive_time: float) -> None:
        """Build and dispatch a MarketTick from a raw message dict."""
        try:
            tick = _build_tick(data)
            if tick is None:
                return

            self._last_tick_time = datetime.now(tz=_IST)

            # Latency: exchange_time vs receive_time
            exchange_ts = tick.timestamp.timestamp()
            latency_ms = (receive_time - exchange_ts) * 1000.0
            if 0 < latency_ms < 60_000:  # sanity: ignore bogus values
                sym = tick.symbol
                if sym not in self._latency_samples:
                    self._latency_samples[sym] = []
                self._latency_samples[sym].append(latency_ms)
                # Keep last 1000 samples
                if len(self._latency_samples[sym]) > 1000:
                    self._latency_samples[sym] = self._latency_samples[sym][-1000:]

            # Sequence gap detection
            if tick.seq is not None:
                last_seq = self._last_seq.get(tick.symbol)
                if last_seq is not None and tick.seq > last_seq + 1:
                    gap = tick.seq - last_seq - 1
                    logger.warning(
                        "Sequence gap for %s: expected %d got %d (missing %d ticks)",
                        tick.symbol, last_seq + 1, tick.seq, gap,
                    )
                self._last_seq[tick.symbol] = tick.seq

            if self.on_tick:
                self.on_tick(tick)

        except Exception as exc:
            logger.debug("Error dispatching tick: %s — %s", exc, data)

    async def _dispatch_bar(self, data: dict) -> None:
        """Build and dispatch a MarketBar from a raw message dict."""
        try:
            bar = _build_bar(data)
            if bar and self.on_bar:
                self.on_bar(bar)
        except Exception as exc:
            logger.debug("Error dispatching bar: %s — %s", exc, data)

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    async def _send_subscribe(self, symbols: list[str]) -> None:
        """Send a subscription message for the given symbols."""
        token = self._auth.get_token()
        msg = {
            "method": "sub",
            "params": {
                "symbols": symbols,
                "token": token,
            },
        }
        await self._safe_send(json.dumps(msg))
        logger.debug("Subscribe sent for %s", symbols)

    async def _safe_send(self, message: str) -> None:
        """Send a message, swallowing errors if not connected."""
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send(message)
            except (ConnectionClosed, WebSocketException) as exc:
                logger.debug("Send failed (connection closed): %s", exc)
            except Exception as exc:
                logger.warning("Unexpected send error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_ts(raw: object) -> Optional[datetime]:
    """Parse a raw timestamp value into an IST-aware datetime."""
    ist = ZoneInfo("Asia/Kolkata")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=ist)
    if isinstance(raw, str):
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ist)
                return dt.astimezone(ist)
            except ValueError:
                continue
    return None


def _opt_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _opt_int(val: object) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _build_tick(data: dict) -> Optional[MarketTick]:
    """Build a MarketTick from a raw WebSocket message dict."""
    symbol = data.get("symbol") or data.get("sym")
    if not symbol:
        return None

    ts_raw = (
        data.get("timestamp")
        or data.get("time")
        or data.get("exchange_time")
        or data.get("ts")
    )
    ts = _parse_ts(ts_raw)
    if ts is None:
        ts = datetime.now(tz=ZoneInfo("Asia/Kolkata"))

    ltp_raw = data.get("ltp") or data.get("price") or data.get("last_price")
    if ltp_raw is None:
        return None

    return MarketTick(
        symbol=str(symbol),
        exchange=str(data.get("exchange") or "NSE"),
        timestamp=ts,
        ltp=float(ltp_raw),
        open=_opt_float(data.get("open")),
        high=_opt_float(data.get("high")),
        low=_opt_float(data.get("low")),
        close=_opt_float(data.get("close") or data.get("prev_close")),
        volume=_opt_int(data.get("volume") or data.get("vol")),
        oi=_opt_int(data.get("oi") or data.get("open_interest")),
        bid=_opt_float(data.get("bid")),
        ask=_opt_float(data.get("ask")),
        bid_qty=_opt_int(data.get("bid_qty")),
        ask_qty=_opt_int(data.get("ask_qty")),
        seq=_opt_int(data.get("seq")),
        raw=data,
    )


def _build_bar(data: dict) -> Optional[MarketBar]:
    """Build a MarketBar from a raw WebSocket bar message dict."""
    symbol = data.get("symbol") or data.get("sym")
    if not symbol:
        return None

    ts_raw = data.get("timestamp") or data.get("bar_time") or data.get("time")
    ts = _parse_ts(ts_raw)
    if ts is None:
        return None

    interval = str(data.get("interval") or data.get("timeframe") or "1min")

    try:
        return MarketBar(
            symbol=str(symbol),
            exchange=str(data.get("exchange") or "NSE"),
            timestamp=ts,
            bar_time=_parse_ts(data.get("bar_time")) if data.get("bar_time") else ts,
            interval=interval,
            open=float(data.get("open", 0.0)),
            high=float(data.get("high", 0.0)),
            low=float(data.get("low", 0.0)),
            close=float(data.get("close", 0.0)),
            volume=int(data.get("volume") or 0),
            oi=_opt_int(data.get("oi")),
        )
    except (ValueError, TypeError):
        return None
