"""
Rate-limited REST client for TrueData API.

All requests are throttled to stay within the ~10 req/sec limit and are
retried with exponential back-off on transient errors (5xx, network
failures, rate limits).
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any, Optional

import httpx

from .auth import TrueDataAuth
from .config import TrueDataConfig
from .exceptions import (
    TrueDataAPIError,
    TrueDataConnectionError,
    TrueDataRateLimitError,
    TrueDataTimeoutError,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 20


class TrueDataRestClient:
    """
    Thin, rate-limited HTTP client for TrueData REST endpoints.

    Features:
    - Enforces a minimum inter-request interval (10 req/sec).
    - Attaches the auth token as a query parameter automatically.
    - Retries on 429, 5xx, and network errors with exponential back-off.
    - Raises typed :mod:`provider.truedata.exceptions` on failure.

    Usage::

        client = TrueDataRestClient(config, auth)
        data = client.get("/symbols/symbolmaster", params={"segment": "nse_fo"})
    """

    # 10 requests per second → minimum 100 ms between calls
    MIN_INTERVAL: float = 0.1

    def __init__(self, config: TrueDataConfig, auth: TrueDataAuth) -> None:
        self._config = config
        self._auth = auth
        self._last_request_time: float = 0.0
        self._throttle_lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict:
        """
        Perform a GET request against the TrueData REST API.

        The base URL (``config.rest_base_url``) is prepended automatically.
        The auth token is injected into ``params`` before the call.

        Parameters
        ----------
        endpoint:
            Path relative to the REST base URL, e.g. ``/symbols/symbolmaster``.
        params:
            Extra query parameters.  ``token`` will be added/overwritten.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        TrueDataRateLimitError
            On HTTP 429.
        TrueDataAPIError
            On non-2xx responses not covered by other exceptions.
        TrueDataConnectionError
            On network failures.
        TrueDataTimeoutError
            On request timeout.
        """
        url = f"{self._config.rest_base_url}{endpoint}"
        return self._request_with_retry(url, params or {})

    def get_history(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict:
        """
        Perform a GET request against the TrueData history API.

        Same as :meth:`get` but uses ``config.history_base_url`` as the base.

        Parameters
        ----------
        endpoint:
            Path relative to the history base URL, e.g. ``/getAllData``.
        params:
            Extra query parameters.
        """
        url = f"{self._config.history_base_url}{endpoint}"
        return self._request_with_retry(url, params or {})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """
        Block until enough time has elapsed since the last request.

        Uses a lock so concurrent callers are serialised.
        """
        with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request_time
            wait = self.MIN_INTERVAL - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.monotonic()

    def _inject_token(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``params`` with the current auth token injected."""
        out = dict(params)
        out["token"] = self._auth.get_token()
        return out

    def _request_with_retry(
        self,
        url: str,
        params: dict[str, Any],
        max_retries: int = 3,
    ) -> dict:
        """
        Execute a GET request with retry logic.

        Retry conditions:
        - HTTP 429 (rate limit) — wait ``retry_after`` or 2 s.
        - HTTP 5xx — exponential back-off starting at 1 s.
        - Network / timeout errors — exponential back-off starting at 1 s.

        Parameters
        ----------
        url:
            Full URL to request.
        params:
            Query parameters (token will be injected).
        max_retries:
            Number of retry attempts after the first failure.

        Returns
        -------
        dict
            Parsed JSON response body.
        """
        last_exc: Optional[Exception] = None
        backoff = 1.0

        for attempt in range(1, max_retries + 2):  # attempt 1 = first try
            self._throttle()
            request_params = self._inject_token(params)

            # Log without token
            safe_params = {k: v for k, v in params.items() if k != "token"}
            logger.debug("GET %s params=%s (attempt %d)", url, safe_params, attempt)

            try:
                with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                    response = client.get(url, params=request_params)

            except httpx.TimeoutException as exc:
                last_exc = TrueDataTimeoutError(
                    f"Request timed out after {_REQUEST_TIMEOUT_SECONDS}s: {url}",
                    timeout_seconds=float(_REQUEST_TIMEOUT_SECONDS),
                )
                logger.warning("Timeout on attempt %d for %s", attempt, url)
            except httpx.RequestError as exc:
                last_exc = TrueDataConnectionError(
                    f"Network error: {exc}", cause=exc
                )
                logger.warning("Network error on attempt %d for %s: %s", attempt, url, exc)
            else:
                # ---- HTTP response received ----
                if response.status_code == 200:
                    try:
                        return response.json()
                    except Exception as exc:
                        raise TrueDataAPIError(
                            f"Failed to parse JSON response from {url}",
                            status_code=200,
                            response_body=response.text[:500],
                        ) from exc

                if response.status_code == 429:
                    retry_after = float(
                        response.headers.get("Retry-After", "2")
                    )
                    last_exc = TrueDataRateLimitError(
                        f"Rate limit hit on {url}", retry_after=retry_after
                    )
                    logger.warning(
                        "Rate limit (429) on attempt %d; waiting %.1fs", attempt, retry_after
                    )
                    if attempt <= max_retries:
                        time.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    last_exc = TrueDataAPIError(
                        f"Server error from {url}",
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )
                    logger.warning(
                        "HTTP %d on attempt %d for %s",
                        response.status_code, attempt, url,
                    )
                else:
                    # 4xx (not 429) — not retriable
                    raise TrueDataAPIError(
                        f"Client error from {url}",
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

            # Exponential back-off before retry
            if attempt <= max_retries:
                sleep_time = min(backoff, 30.0)
                logger.debug("Backing off %.1fs before retry", sleep_time)
                time.sleep(sleep_time)
                backoff *= 2

        # All attempts exhausted
        if last_exc:
            raise last_exc
        raise TrueDataAPIError(
            f"All {max_retries + 1} attempts failed for {url}",
            status_code=0,
        )
