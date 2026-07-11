"""Error diagnostics for the bank import pipeline.

Provides ``diagnose_error()`` which enriches an exception with a
human-readable suggested action and structured context, so that
failures are diagnosable without debug mode.
"""

from __future__ import annotations

from typing import Any

from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	MaxRetriesExceededError,
	NetworkError,
	NormalizationError,
	OAuthHandshakeError,
	RateLimitError,
	ServerError,
	TokenExpiredError,
	TokenRevokedError,
	is_transient_error,
)

# ------------------------------------------------------------------
# Phase labels
# ------------------------------------------------------------------

PHASE_AUTH = "auth"
PHASE_FETCH = "fetch"
PHASE_NORMALIZE = "normalize"
PHASE_INSERT = "insert"
PHASE_UNKNOWN = "unknown"

# ------------------------------------------------------------------
# Machine-readable error codes
# ------------------------------------------------------------------

_ERROR_CODES: dict[type, str] = {
	AuthenticationError: "AUTH_FAILED",
	TokenExpiredError: "TOKEN_EXPIRED",
	TokenRevokedError: "TOKEN_REVOKED",
	OAuthHandshakeError: "OAUTH_HANDSHAKE_FAILED",
	ConfigurationError: "CONFIG_ERROR",
	RateLimitError: "RATE_LIMITED",
	ServerError: "SERVER_ERROR",
	NetworkError: "NETWORK_ERROR",
	NormalizationError: "NORMALIZATION_FAILED",
	MaxRetriesExceededError: "MAX_RETRIES_EXCEEDED",
}
"""Maps exception types to machine-readable error codes.

These codes are exposed in ``diagnose_error()`` output and can be
used by operators to configure Frappe ``Notification`` rules for
automated alerting.
"""

_DEADLINE_EXCEEDED_CODES = frozenset({"TOKEN_EXPIRED", "MAX_RETRIES_EXCEEDED"})
"""Error codes that indicate an import run should not be retried
automatically until the user takes corrective action."""

# ------------------------------------------------------------------
# Suggested-action catalog
# ------------------------------------------------------------------

_PHASE_ACTIONS: dict[str, str] = {
	PHASE_AUTH: (
		"Authentication failed. Check that your API credentials are correct "
		"and the OAuth token has not been revoked. Re-authorize the connector "
		"if needed."
	),
	PHASE_FETCH: (
		"The bank API returned an error while fetching transactions. "
		"Check the bank's service status page. If the issue persists, "
		"verify the account identifier and date range."
	),
	PHASE_NORMALIZE: (
		"A transaction from the bank could not be normalised. "
		"This may indicate a change in the bank's API response format. "
		"Check the error details for the raw transaction data."
	),
	PHASE_INSERT: (
		"ERPNext could not save a bank transaction. Check that the "
		"Bank Account and Company are correctly configured and the "
		"user has write permissions."
	),
	PHASE_UNKNOWN: (
		"An unexpected error occurred. Check the error traceback for "
		"details. If the issue persists, contact support."
	),
}

_EXCEPTION_ACTIONS: dict[type, str] = {
	AuthenticationError: _PHASE_ACTIONS[PHASE_AUTH],
	TokenExpiredError: (
		"The access token has expired and could not be refreshed. "
		"Re-authorize the connector to obtain a new token."
	),
	TokenRevokedError: (
		"The refresh token has been revoked or is invalid. Re-authorize the connector to obtain a new token."
	),
	OAuthHandshakeError: (
		"The OAuth authorization code exchange failed. "
		"Verify the redirect URI and authorization code are valid, "
		"then re-authorize the connector."
	),
	ConfigurationError: (
		"The connector configuration is invalid or incomplete. "
		"Open the Bank Connector record and verify all required fields."
	),
	RateLimitError: (
		"The bank API rate limit was exceeded. The import will retry "
		"automatically. If this persists, reduce the import frequency "
		"or contact the bank to increase the rate limit."
	),
	ServerError: (
		"The bank API returned a server error (5xx). This is typically "
		"temporary. The import will retry automatically."
	),
	NetworkError: (
		"A network error occurred while contacting the bank API. "
		"Check your internet connection and firewall settings. "
		"The import will retry automatically."
	),
	NormalizationError: _PHASE_ACTIONS[PHASE_NORMALIZE],
	MaxRetriesExceededError: (
		"The import failed after multiple retry attempts. The bank API "
		"may be temporarily unavailable. Check the bank's service status "
		"page. The next scheduled import will try again."
	),
}


def diagnose_error(exc: Exception, phase: str = PHASE_UNKNOWN) -> dict[str, Any]:
	"""Enrich an exception with diagnostic context.

	Args:
	    exc: The exception that occurred.
	    phase: The pipeline phase where the error occurred
	        (``"auth"``, ``"fetch"``, ``"normalize"``, ``"insert"``,
	        or ``"unknown"``).

	Returns:
	    A dict with keys:
	        - ``phase``: The pipeline phase label.
	        - ``error_code``: Machine-readable error code.
	        - ``error_type``: The exception class name.
	        - ``is_transient``: Whether the error is retryable.
	        - ``suggested_action``: A human-readable suggested next step.
	        - ``context``: Additional structured context.
	"""
	error_type = type(exc).__name__
	error_code = _resolve_error_code(exc)
	is_transient = is_transient_error(exc)
	suggested_action = _resolve_action(exc, phase)
	context: dict[str, Any] = {"message": str(exc) if str(exc) else error_type}

	# Enrich context with exception-specific attributes
	if isinstance(exc, ApiError):
		context["http_status"] = exc.status_code
		context["response_body"] = exc.response_body
	if isinstance(exc, ServerError):
		context["http_status"] = exc.status_code
		context["response_body"] = exc.response_body
	if isinstance(exc, RateLimitError):
		context["retry_after"] = exc.retry_after
	if isinstance(exc, MaxRetriesExceededError):
		context["attempts"] = exc.attempts
		if exc.original_exception:
			context["original_exception"] = type(exc.original_exception).__name__

	return {
		"phase": phase,
		"error_code": error_code,
		"error_type": error_type,
		"is_transient": is_transient,
		"suggested_action": suggested_action,
		"context": context,
	}


def _resolve_error_code(exc: Exception) -> str:
	"""Return the machine-readable error code for *exc*."""
	exc_type = type(exc)
	if exc_type in _ERROR_CODES:
		return _ERROR_CODES[exc_type]
	for cls in exc_type.__mro__:
		if cls in _ERROR_CODES:
			return _ERROR_CODES[cls]
	return "UNKNOWN_ERROR"


def _resolve_action(exc: Exception, phase: str) -> str:
	"""Return the most specific suggested action for *exc* in *phase*.

	Checks for an exact match on the exception class first, then
	falls back to the phase-level action, then to the generic action.
	"""
	# Exact class match (not via isinstance — we want the most specific)
	exc_type = type(exc)
	if exc_type in _EXCEPTION_ACTIONS:
		return _EXCEPTION_ACTIONS[exc_type]

	# Check MRO for a registered parent
	for cls in exc_type.__mro__:
		if cls in _EXCEPTION_ACTIONS:
			return _EXCEPTION_ACTIONS[cls]

	# Fall back to phase-level action
	phase_action = _PHASE_ACTIONS.get(phase)
	if phase_action:
		return phase_action

	return _PHASE_ACTIONS[PHASE_UNKNOWN]


__all__ = [
	"PHASE_AUTH",
	"PHASE_FETCH",
	"PHASE_INSERT",
	"PHASE_NORMALIZE",
	"PHASE_UNKNOWN",
	"ERROR_CODES",
	"DEADLINE_EXCEEDED_CODES",
	"diagnose_error",
]

# Re-export error code constants for convenience
ERROR_CODES = _ERROR_CODES
DEADLINE_EXCEEDED_CODES = _DEADLINE_EXCEEDED_CODES
