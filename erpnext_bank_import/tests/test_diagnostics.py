"""Tests for the error diagnostics module.

These tests validate:

1. Each exception type maps to a correct ``is_transient`` and ``suggested_action``.
2. Unknown exceptions get a generic diagnostic.
3. The ``phase`` parameter influences the suggested action.
4. ``MaxRetriesExceededError`` carries original exception context.
5. ``ApiError`` with status >= 500 is detected as transient.
"""

from __future__ import annotations

from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	ConnectorError,
	MaxRetriesExceededError,
	NetworkError,
	NormalizationError,
	OAuthHandshakeError,
	RateLimitError,
	ServerError,
	TokenExpiredError,
	TokenRevokedError,
)
from erpnext_bank_import.services.diagnostics import (
	PHASE_AUTH,
	PHASE_FETCH,
	PHASE_INSERT,
	PHASE_NORMALIZE,
	PHASE_UNKNOWN,
	diagnose_error,
)


class TestDiagnoseError:
	"""Tests for the diagnose_error function."""

	def _check_structure(self, diagnostic: dict) -> None:
		"""Assert that a diagnostic dict has all expected keys."""
		assert "phase" in diagnostic
		assert "error_type" in diagnostic
		assert "is_transient" in diagnostic
		assert "suggested_action" in diagnostic
		assert "context" in diagnostic
		assert isinstance(diagnostic["context"], dict)

	# ------------------------------------------------------------------
	# Per-exception type
	# ------------------------------------------------------------------

	def test_authentication_error(self):
		"""AuthenticationError is non-transient with auth action."""
		d = diagnose_error(AuthenticationError("Invalid credentials"), phase=PHASE_AUTH)
		self._check_structure(d)
		assert d["error_type"] == "AuthenticationError"
		assert d["is_transient"] is False
		assert "credentials" in d["suggested_action"].lower() or "token" in d["suggested_action"].lower()

	def test_rate_limit_error(self):
		"""RateLimitError is transient with rate-limit action."""
		d = diagnose_error(RateLimitError("Rate limit exceeded", retry_after=30.0), phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["is_transient"] is True
		assert d["context"]["retry_after"] == 30.0
		assert "rate limit" in d["suggested_action"].lower()

	def test_server_error(self):
		"""ServerError (5xx) is transient."""
		d = diagnose_error(ServerError("Internal error", status_code=500), phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["is_transient"] is True
		assert d["context"]["http_status"] == 500
		assert "server error" in d["suggested_action"].lower()

	def test_api_error_below_500(self):
		"""ApiError with status < 500 is NOT transient."""
		d = diagnose_error(ApiError("Bad request", status_code=400), phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["is_transient"] is False

	def test_api_error_500_plus(self):
		"""ApiError with status >= 500 IS transient."""
		d = diagnose_error(ApiError("Service unavailable", status_code=503), phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["is_transient"] is True
		assert d["context"]["http_status"] == 503

	def test_network_error(self):
		"""NetworkError is transient."""
		d = diagnose_error(NetworkError("Connection timed out"), phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["is_transient"] is True
		assert "network" in d["suggested_action"].lower()

	def test_token_expired_error(self):
		"""TokenExpiredError is non-transient with re-auth action."""
		d = diagnose_error(TokenExpiredError("Token expired"), phase=PHASE_AUTH)
		self._check_structure(d)
		assert d["is_transient"] is False
		assert "re-authorize" in d["suggested_action"].lower()

	def test_token_revoked_error(self):
		"""TokenRevokedError is non-transient with re-auth action."""
		d = diagnose_error(TokenRevokedError("Token revoked"), phase=PHASE_AUTH)
		self._check_structure(d)
		assert d["is_transient"] is False
		assert "re-authorize" in d["suggested_action"].lower()

	def test_oauth_handshake_error(self):
		"""OAuthHandshakeError is non-transient with handshake action."""
		d = diagnose_error(OAuthHandshakeError("Code exchange failed"), phase=PHASE_AUTH)
		self._check_structure(d)
		assert d["is_transient"] is False
		assert "redirect" in d["suggested_action"].lower()

	def test_configuration_error(self):
		"""ConfigurationError is non-transient with config action."""
		d = diagnose_error(ConfigurationError("Missing client_id"), phase=PHASE_UNKNOWN)
		self._check_structure(d)
		assert d["is_transient"] is False
		assert "configuration" in d["suggested_action"].lower()

	def test_normalization_error(self):
		"""NormalizationError is non-transient."""
		d = diagnose_error(NormalizationError("Missing field"), phase=PHASE_NORMALIZE)
		self._check_structure(d)
		assert d["is_transient"] is False

	def test_max_retries_exceeded_error(self):
		"""MaxRetriesExceededError carries attempt count and original exception."""
		original = NetworkError("Timeout on attempt 3")
		exc = MaxRetriesExceededError(
			message="All 4 attempts failed",
			original_exception=original,
			attempts=4,
		)
		d = diagnose_error(exc, phase=PHASE_FETCH)
		self._check_structure(d)
		assert d["context"]["attempts"] == 4
		assert d["context"]["original_exception"] == "NetworkError"
		assert "retry" in d["suggested_action"].lower()

	# ------------------------------------------------------------------
	# Unknown / generic exceptions
	# ------------------------------------------------------------------

	def test_unknown_exception(self):
		"""A plain Exception falls through to the generic action."""
		d = diagnose_error(ValueError("Something weird"), phase=PHASE_UNKNOWN)
		self._check_structure(d)
		assert d["is_transient"] is False
		assert "unexpected" in d["suggested_action"].lower()

	def test_phase_influences_action(self):
		"""The phase parameter influences the suggested action for generic errors."""
		d_fetch = diagnose_error(ValueError("Oops"), phase=PHASE_FETCH)
		d_insert = diagnose_error(ValueError("Oops"), phase=PHASE_INSERT)
		assert d_fetch["suggested_action"] != d_insert["suggested_action"]
		assert "bank api" in d_fetch["suggested_action"].lower()
		assert (
			"erpnext" in d_insert["suggested_action"].lower()
			or "permissions" in d_insert["suggested_action"].lower()
		)
