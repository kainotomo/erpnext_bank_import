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


class RateLimitError(ConnectorError):
	"""Raised when the bank API rate limit is exceeded (HTTP 429).

	The caller should back off and retry.  Implementations MAY attach
	a ``retry_after`` attribute (seconds) if the API provides it.
	"""

	def __init__(self, message: str = "", retry_after: float | None = None) -> None:
		self.retry_after = retry_after
		super().__init__(message)


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


__all__ = [
	"ApiError",
	"AuthenticationError",
	"ConfigurationError",
	"ConnectorError",
	"NormalizationError",
	"OAuthHandshakeError",
	"RateLimitError",
	"TokenExpiredError",
	"TokenRevokedError",
]
