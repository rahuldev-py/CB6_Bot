"""
TrueData provider exceptions.

All TrueData-specific errors derive from TrueDataError so callers
can catch the entire family with a single except clause.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TrueDataError(Exception):
    """Base exception for all TrueData provider errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class TrueDataAuthError(TrueDataError):
    """
    Raised when authentication fails.

    Covers bad credentials, expired tokens that cannot be refreshed,
    or unexpected auth API responses.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TrueDataConnectionError(TrueDataError):
    """
    Raised when a network-level connection cannot be established or is lost.

    Used by both the REST client and the WebSocket client.
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause

    def __str__(self) -> str:
        if self.cause:
            return f"{self.message} (caused by: {self.cause})"
        return self.message


class TrueDataTimeoutError(TrueDataError):
    """
    Raised when a REST request or WebSocket operation times out.
    """

    def __init__(self, message: str, timeout_seconds: float | None = None) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class TrueDataRateLimitError(TrueDataError):
    """
    Raised when the REST API returns HTTP 429 or equivalent rate-limit signal.

    ``retry_after`` is the suggested wait time in seconds, if provided by
    the server.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TrueDataSymbolNotFoundError(TrueDataError):
    """
    Raised when a requested symbol does not exist in TrueData's symbol master
    or is not available for the requested segment / expiry.
    """

    def __init__(self, symbol: str) -> None:
        super().__init__(f"Symbol not found in TrueData: '{symbol}'")
        self.symbol = symbol


class TrueDataAPIError(TrueDataError):
    """
    Raised when the TrueData API returns a non-2xx HTTP response that is not
    covered by a more specific exception class.

    Attributes
    ----------
    status_code:
        HTTP status code returned by the API.
    response_body:
        Raw response body text (truncated to 500 chars in repr).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        body_preview = self.response_body[:200] if self.response_body else ""
        return (
            f"{self.message} [HTTP {self.status_code}]"
            + (f" — {body_preview}" if body_preview else "")
        )
