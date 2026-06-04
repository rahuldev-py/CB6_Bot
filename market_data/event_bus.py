"""
Thread-safe synchronous/asynchronous event bus for CB6 market data events.

Supports both regular (sync) and async handlers.  Publish is safe to call
from any thread or coroutine.

Event types
-----------
    TICK            — MarketTick received
    BAR             — MarketBar completed
    OPTION_CHAIN    — Option chain snapshot
    GREEKS          — Greeks snapshot
    CONNECT         — Provider connected
    DISCONNECT      — Provider disconnected
    ERROR           — Error occurred

Usage::

    bus = EventBus()
    bus.subscribe("TICK", lambda tick: print(tick.ltp))
    bus.publish("TICK", tick_object)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Enumeration of supported market data event types."""

    TICK = "TICK"
    BAR = "BAR"
    OPTION_CHAIN = "OPTION_CHAIN"
    GREEKS = "GREEKS"
    CONNECT = "CONNECT"
    DISCONNECT = "DISCONNECT"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


class EventBus:
    """
    Lightweight, thread-safe publish/subscribe event bus.

    Both sync and async handlers are supported.  Async handlers are
    scheduled on the provided event loop (or the running loop at call
    time).  Sync handlers are called directly in the publishing thread.

    Parameters
    ----------
    loop:
        Optional asyncio event loop for scheduling async handlers.
        If None, the running loop at publish time is used (if available).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        # handlers: event_type -> list of callables
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()
        self._loop = loop

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str | EventType, handler: Callable) -> None:
        """
        Register a handler for an event type.

        Parameters
        ----------
        event_type:
            One of the :class:`EventType` values, or a plain string.
        handler:
            Callable that accepts a single positional argument (the event
            data).  May be sync or async.
        """
        key = str(event_type).upper()
        with self._lock:
            self._handlers[key].append(handler)
        logger.debug(
            "EventBus: subscribed %s to '%s' (total=%d)",
            getattr(handler, "__name__", repr(handler)),
            key,
            len(self._handlers[key]),
        )

    def unsubscribe(self, event_type: str | EventType, handler: Callable) -> bool:
        """
        Remove a previously registered handler.

        Parameters
        ----------
        event_type:
            Event type string.
        handler:
            The exact callable that was registered.

        Returns
        -------
        bool
            True if the handler was found and removed, False otherwise.
        """
        key = str(event_type).upper()
        with self._lock:
            handlers = self._handlers.get(key, [])
            try:
                handlers.remove(handler)
                logger.debug("EventBus: unsubscribed from '%s'", key)
                return True
            except ValueError:
                return False

    def publish(self, event_type: str | EventType, data: Any) -> None:
        """
        Dispatch ``data`` to all handlers registered for ``event_type``.

        Sync handlers are called immediately in the current thread.
        Async handlers are scheduled on the event loop if one is
        available; otherwise they are skipped with a warning.

        Parameters
        ----------
        event_type:
            Event type string or :class:`EventType` enum value.
        data:
            The event payload (e.g. a :class:`MarketTick` object).
        """
        key = str(event_type).upper()
        with self._lock:
            handlers = list(self._handlers.get(key, []))

        if not handlers:
            return

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    self._schedule_async(handler, data)
                else:
                    handler(data)
            except Exception as exc:
                logger.error(
                    "EventBus handler %s raised for event '%s': %s",
                    getattr(handler, "__name__", repr(handler)),
                    key,
                    exc,
                )

    def subscriber_count(self, event_type: str | EventType) -> int:
        """Return the number of registered handlers for an event type."""
        key = str(event_type).upper()
        with self._lock:
            return len(self._handlers.get(key, []))

    def clear(self, event_type: str | EventType | None = None) -> None:
        """
        Remove all handlers for a specific event type, or for all types.

        Parameters
        ----------
        event_type:
            If provided, clear only that event type.  If None, clear all.
        """
        with self._lock:
            if event_type is None:
                self._handlers.clear()
                logger.debug("EventBus: all handlers cleared")
            else:
                key = str(event_type).upper()
                removed = len(self._handlers.pop(key, []))
                logger.debug(
                    "EventBus: cleared %d handlers for '%s'", removed, key
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _schedule_async(self, coro_fn: Callable, data: Any) -> None:
        """Schedule an async handler coroutine on an event loop."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro_fn(data), loop)
        else:
            logger.warning(
                "EventBus: async handler %s could not be scheduled "
                "(no running event loop)",
                getattr(coro_fn, "__name__", repr(coro_fn)),
            )
