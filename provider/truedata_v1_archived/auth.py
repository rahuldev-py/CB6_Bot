"""
TrueData authentication manager.

Handles login, token caching, and automatic token refresh.
The token is stored in memory only — never written to disk.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

import httpx

from .config import TrueDataConfig
from .exceptions import TrueDataAuthError, TrueDataConnectionError, TrueDataTimeoutError

logger = logging.getLogger(__name__)

# Token is considered stale 5 minutes before its stated expiry
_TOKEN_REFRESH_BUFFER_SECONDS = 300
_LOGIN_TIMEOUT_SECONDS = 15


class TrueDataAuth:
    """
    Thread-safe token manager for TrueData API.

    Usage::

        auth = TrueDataAuth(config)
        token = auth.get_token()   # logs in on first call
        auth.logout()

    The ``get_token()`` method is safe to call from multiple threads;
    only one login request will be in flight at a time.
    """

    def __init__(self, config: TrueDataConfig) -> None:
        self._config = config
        self._token: Optional[str] = None
        self._expires_at: float = 0.0  # Unix timestamp
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self) -> str:
        """
        Authenticate with TrueData and cache the returned token.

        Returns
        -------
        str
            The access token string.

        Raises
        ------
        TrueDataAuthError
            If credentials are rejected or the response is unexpected.
        TrueDataConnectionError
            If the HTTP connection fails.
        TrueDataTimeoutError
            If the request times out.
        """
        url = f"{self._config.rest_base_url}/users/login"
        payload = {
            "user": self._config.user,
            "password": self._config.password,
        }
        logger.info(
            "Logging in to TrueData as user=%s (env=%s)",
            self._config.masked_user,
            self._config.env,
        )

        try:
            with httpx.Client(timeout=_LOGIN_TIMEOUT_SECONDS) as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise TrueDataTimeoutError(
                f"Login request timed out after {_LOGIN_TIMEOUT_SECONDS}s",
                timeout_seconds=float(_LOGIN_TIMEOUT_SECONDS),
            ) from exc
        except httpx.RequestError as exc:
            raise TrueDataConnectionError(
                f"Network error during login: {exc}", cause=exc
            ) from exc

        if response.status_code == 401:
            raise TrueDataAuthError(
                f"Invalid TrueData credentials for user '{self._config.masked_user}'",
                status_code=401,
            )
        if response.status_code != 200:
            raise TrueDataAuthError(
                f"Unexpected login response: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise TrueDataAuthError(
                f"Failed to parse login response: {exc}"
            ) from exc

        token = data.get("token") or data.get("access_token")
        if not token:
            raise TrueDataAuthError(
                f"Login response missing 'token' field. Keys present: {list(data.keys())}"
            )

        # Parse expiry — TrueData returns ISO string or epoch int
        expires_raw = data.get("expires") or data.get("expiry")
        self._expires_at = self._parse_expiry(expires_raw)

        self._token = token
        logger.info(
            "TrueData login successful. Token: %s**** expires_at=%s",
            token[:4],
            datetime.fromtimestamp(self._expires_at, tz=timezone.utc).isoformat(),
        )
        return self._token

    def get_token(self) -> str:
        """
        Return the current valid token, refreshing if needed.

        This method is thread-safe.  All callers share the same token;
        refresh is done at most once even under concurrent access.

        Returns
        -------
        str
            Valid access token.
        """
        with self._lock:
            self._refresh_if_needed()
            if not self._token:
                raise TrueDataAuthError("No token available after login attempt")
            return self._token

    def logout(self) -> None:
        """
        Invalidate the cached token.

        TrueData does not expose a logout endpoint, so this simply
        clears the in-memory state.
        """
        with self._lock:
            self._token = None
            self._expires_at = 0.0
        logger.info("TrueData token cleared (logout)")

    @property
    def is_authenticated(self) -> bool:
        """
        True if a valid (non-expired) token is currently held.
        """
        if not self._token:
            return False
        return time.time() < (self._expires_at - _TOKEN_REFRESH_BUFFER_SECONDS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_if_needed(self) -> None:
        """Login or re-login if the token is missing or near expiry."""
        if not self.is_authenticated:
            logger.debug("Token missing or expiring soon — refreshing")
            self.login()

    @staticmethod
    def _parse_expiry(expires_raw: object) -> float:
        """
        Parse the ``expires`` field from TrueData into a Unix timestamp.

        Accepts:
        - Unix int/float (seconds since epoch)
        - ISO-8601 string
        - None / missing → defaults to 8 hours from now
        """
        default_ttl = 8 * 3600  # 8 hours
        if expires_raw is None:
            logger.debug("No expiry in login response; defaulting to 8 h TTL")
            return time.time() + default_ttl

        if isinstance(expires_raw, (int, float)):
            return float(expires_raw)

        if isinstance(expires_raw, str):
            try:
                dt = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                logger.warning(
                    "Cannot parse expiry string '%s'; defaulting to 8 h", expires_raw
                )
                return time.time() + default_ttl

        return time.time() + default_ttl
