"""Connector-specific exception hierarchy.

All connector-related exceptions inherit from ``ConnectorError`` so
that callers can catch a single base type or handle specific failures:

.. code-block:: python

    from erpnext_bank_import.connectors.exceptions import ConnectorError

    try:
        connector.authenticate()
    except AuthenticationError:
        ...  # credentials invalid / expired
    except RateLimitError:
        ...  # 429, wait and retry
    except ConnectorError:
        ...  # any other connector failure
"""

from __future__ import annotations


class ConnectorError(Exception):
	"""Base exception for all bank connector failures."""


class AuthenticationError(ConnectorError):
	"""Raised when authentication or token refresh fails.

	Possible causes: invalid credentials, expired token that cannot be
	refreshed, or an OAuth handshake rejection.
	"""


# ------------------------------------------------------------------
# Transient error support (defined before use by RateLimitError etc.)
# ------------------------------------------------------------------


class TransientError(ConnectorError):
	"""Marker mixin/class for errors that are transient and may be retried.

	Subclass this (or make an exception inherit from both ``ConnectorError``
	and ``TransientError``) to indicate that the operation may succeed
	if retried after a short delay.
	"""


class RateLimitError(TransientError):
	"""Raised when the bank API rate limit is exceeded (HTTP 429).

	The caller should back off and retry.  Implementations MAY attach
	a ``retry_after`` attribute (seconds) if the API provides it.
	"""

	def __init__(self, message: str = "", retry_after: float | None = None) -> None:
		self.retry_after = retry_after
		super().__init__(message)


class ServerError(TransientError):
	"""Raised when the bank API returns a 5xx status code.

	These errors indicate a temporary server-side issue and are safe
	to retry with back-off.
	"""

	def __init__(
		self, message: str = "", status_code: int | None = None, response_body: str | None = None
	) -> None:
		self.status_code = status_code
		self.response_body = response_body
		super().__init__(message)


class NetworkError(TransientError):
	"""Raised on network-level failures (timeout, DNS, connection refused).

	These errors are inherently transient and safe to retry.
	"""


class MaxRetriesExceededError(ConnectorError):
	"""Raised when the retry utility exhausts all attempts.

	Attributes:
	    original_exception: The last exception that triggered the retry
	        chain, so callers can inspect the root cause.
	    attempts: The number of attempts made before giving up.
	"""

	def __init__(
		self, message: str = "", original_exception: Exception | None = None, attempts: int = 0
	) -> None:
		self.original_exception = original_exception
		self.attempts = attempts
		super().__init__(message)


# ------------------------------------------------------------------
# Non-transient errors
# ------------------------------------------------------------------


class ApiError(ConnectorError):
	"""Raised on an unsuccessful HTTP response from the bank API.

	Attributes:
	    status_code: HTTP status code.
	    response_body: Raw response text, if available.
	"""

	def __init__(
		self,
		message: str = "",
		status_code: int | None = None,
		response_body: str | None = None,
	) -> None:
		self.status_code = status_code
		self.response_body = response_body
		super().__init__(message)


class ConfigurationError(ConnectorError):
	"""Raised when the connector configuration is invalid or incomplete."""


class NormalizationError(ConnectorError):
	"""Raised when a raw transaction dict cannot be normalised.

	This typically indicates a missing required field or a data-type
	mismatch in the bank API response.
	"""


class OAuthHandshakeError(ConnectorError):
	"""Raised when the OAuth2 authorization code exchange fails.

	Possible causes: invalid/expired authorization code, mismatched
	redirect URI, or the bank API returning an error during token
	exchange.
	"""


class TokenExpiredError(ConnectorError):
	"""Raised when the access token is expired and cannot be refreshed.

	This indicates that the refresh token itself has expired or no
	refresh token is available.  The caller should re-initiate the
	OAuth2 authorization code flow.
	"""


class TokenRevokedError(ConnectorError):
	"""Raised when the refresh token has been revoked or is invalid.

	A 400/401 response during a refresh token grant typically indicates
	the refresh token was revoked.  The caller should clear stored
	tokens and trigger re-authorization.
	"""


# ------------------------------------------------------------------
# Helper: classify an exception as transient
# ------------------------------------------------------------------


def is_transient_error(exc: Exception) -> bool:
	"""Return ``True`` if *exc* is a transient (retryable) error.

	Checks the exception and its cause chain for ``TransientError``
	subclasses or ``ApiError`` with status >= 500.
	"""
	if isinstance(exc, TransientError):
		return True
	if isinstance(exc, ApiError) and exc.status_code is not None and exc.status_code >= 500:
		return True
	# Also check the __cause__ chain
	cause = getattr(exc, "__cause__", None)
	if cause is not None:
		return is_transient_error(cause)
	return False


__all__ = [
	"ApiError",
	"AuthenticationError",
	"ConfigurationError",
	"ConnectorError",
	"MaxRetriesExceededError",
	"NetworkError",
	"NormalizationError",
	"OAuthHandshakeError",
	"RateLimitError",
	"ServerError",
	"TokenExpiredError",
	"TokenRevokedError",
	"TransientError",
	"is_transient_error",
]
