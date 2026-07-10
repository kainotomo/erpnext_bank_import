"""Tests for the shared OAuth2 token lifecycle service.

These tests validate:

1. ``OAuthToken`` dataclass construction and defaults.
2. ``OAuthProviderConfig`` extraction from ``ConnectorConfig``.
3. ``OAuth2Service`` URL building, token exchange, refresh, revoke.
4. Token expiry logic (safety buffer).
5. Error paths and exception propagation.

All Frappe-dependent code paths are mocked to keep tests fast and
deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ConfigurationError,
	OAuthHandshakeError,
	TokenExpiredError,
	TokenRevokedError,
)
from erpnext_bank_import.services.oauth import OAuth2Service, OAuthProviderConfig, OAuthToken

# =========================================================================
# Helpers
# =========================================================================


def _make_config(**overrides: Any) -> ConnectorConfig:
	"""Build a ConnectorConfig with OAuth fields, defaulting to valid values."""
	defaults: dict[str, Any] = {
		"provider_name": "test",
		"api_base_url": "https://api.example.com",
		"client_id": "cid",
		"authorize_url": "/auth/authorize",
		"token_url": "/auth/token",
	}
	defaults.update(overrides)
	return ConnectorConfig(**defaults)


class _MockFrappeDoc:
	"""Minimal mock for a Frappe document returned by get_doc / new_doc."""

	def __init__(self, **kwargs: Any) -> None:
		self._data = dict(kwargs)
		for k, v in kwargs.items():
			setattr(self, k, v)
		self._saved = False
		self._deleted = False

	def __getattr__(self, name: str) -> Any:
		"""Return None for any attribute not explicitly set."""
		if name.startswith("_"):
			raise AttributeError(name)
		return None

	def save(self, **kwargs: Any) -> None:
		self._saved = True

	def delete(self, **kwargs: Any) -> None:
		self._deleted = True

	def get_password(self, fieldname: str, *args: Any, **kwargs: Any) -> str:
		return self._data.get(fieldname, "")


def _patch_frappe_db(
	exists: bool = False,
	doc_kwargs: dict[str, Any] | None = None,
) -> dict[str, MagicMock]:
	"""Patch the ``frappe`` module in ``oauth`` with a MagicMock.

	This avoids triggering ``frappe``'s thread-local proxy (which
	requires a request context).  All calls to ``frappe.db.exists``,
	``frappe.get_doc``, ``frappe.new_doc`` etc. go through the mock.

	Returns a dict of mock references so callers can stop them via
	``_unpatch_frappe_db``.
	"""
	if doc_kwargs is None:
		doc_kwargs = {}

	mock_frappe = patch("erpnext_bank_import.services.oauth.frappe", autospec=False)
	m_frappe = mock_frappe.start()

	# Set up commonly used attributes as MagicMocks.
	m_frappe.db.exists.return_value = exists

	mock_doc = _MockFrappeDoc(**doc_kwargs) if doc_kwargs else _MockFrappeDoc()
	m_frappe.get_doc.return_value = mock_doc
	m_frappe.new_doc.return_value = _MockFrappeDoc(**doc_kwargs) if doc_kwargs else _MockFrappeDoc()

	return {
		"mock_frappe": mock_frappe,
		"_m_frappe": m_frappe,
		"_mock_doc": mock_doc,
	}


def _unpatch_frappe_db(mocks: dict[str, Any]) -> None:
	"""Stop patches started by ``_patch_frappe_db``."""
	mocks["mock_frappe"].stop()


# =========================================================================
# OAuthToken dataclass
# =========================================================================


class TestOAuthToken:
	"""Verifies OAuthToken dataclass construction."""

	def test_minimal_token(self) -> None:
		"""Minimal token requires only access_token."""
		token = OAuthToken(access_token="abc123")
		assert token.access_token == "abc123"
		assert token.refresh_token is None
		assert token.token_type == "Bearer"
		assert token.expires_at is None
		assert token.scope is None
		assert token.provider_metadata == {}

	def test_full_token(self) -> None:
		"""All fields can be set explicitly."""
		expires = datetime.now(UTC) + timedelta(hours=1)
		token = OAuthToken(
			access_token="access-1",
			refresh_token="refresh-1",
			token_type="Bearer",
			expires_at=expires,
			scope="transactions:read",
			provider_metadata={"raw": "data"},
		)
		assert token.access_token == "access-1"
		assert token.refresh_token == "refresh-1"
		assert token.expires_at == expires
		assert token.scope == "transactions:read"
		assert token.provider_metadata == {"raw": "data"}

	def test_token_defaults(self) -> None:
		"""Token type should default to 'Bearer'."""
		token = OAuthToken(access_token="x")
		assert token.token_type == "Bearer"


# =========================================================================
# OAuthProviderConfig
# =========================================================================


class TestOAuthProviderConfig:
	"""Verifies OAuthProviderConfig extraction and validation."""

	def test_from_connector_config_minimal(self) -> None:
		"""Minimal OAuth config should require client_id, authorize_url, token_url."""
		cfg = _make_config()
		oauth_cfg = OAuthProviderConfig.from_connector_config(cfg)
		assert oauth_cfg.client_id == "cid"
		assert oauth_cfg.authorize_url == "https://api.example.com/auth/authorize"
		assert oauth_cfg.token_url == "https://api.example.com/auth/token"
		assert oauth_cfg.client_secret is None
		assert oauth_cfg.revoke_url is None
		assert oauth_cfg.scopes == []
		assert oauth_cfg.token_safety_buffer_seconds == 60

	def test_from_connector_config_full(self) -> None:
		"""All OAuth config values should be extracted."""
		cfg = _make_config(
			client_secret="csecret",
			authorize_url="https://auth.example.com/authorize",
			token_url="https://auth.example.com/token",
			revoke_url="https://auth.example.com/revoke",
			scopes=["read", "write"],
			redirect_uri="https://erpnext.example.com/callback",
			token_safety_buffer_seconds=120,
		)
		oauth_cfg = OAuthProviderConfig.from_connector_config(cfg)
		assert oauth_cfg.client_secret == "csecret"
		assert oauth_cfg.revoke_url == "https://auth.example.com/revoke"
		assert oauth_cfg.scopes == ["read", "write"]
		assert oauth_cfg.redirect_uri == "https://erpnext.example.com/callback"
		assert oauth_cfg.token_safety_buffer_seconds == 120

	def test_missing_client_id_raises(self) -> None:
		"""Missing client_id should raise ConfigurationError."""
		cfg = _make_config(client_id=None)
		with pytest.raises(ConfigurationError, match="client_id"):
			OAuthProviderConfig.from_connector_config(cfg)

	def test_missing_authorize_url_raises(self) -> None:
		"""Missing authorize_url should raise ConfigurationError."""
		cfg = _make_config(authorize_url=None)
		with pytest.raises(ConfigurationError, match="authorize_url"):
			OAuthProviderConfig.from_connector_config(cfg)

	def test_missing_token_url_raises(self) -> None:
		"""Missing token_url should raise ConfigurationError."""
		cfg = _make_config(token_url=None)
		with pytest.raises(ConfigurationError, match="token_url"):
			OAuthProviderConfig.from_connector_config(cfg)

	def test_absolute_urls_left_unchanged(self) -> None:
		"""Absolute URLs should not be joined with api_base_url."""
		cfg = _make_config(
			authorize_url="https://auth.other.com/oauth/authorize",
			token_url="https://auth.other.com/oauth/token",
		)
		oauth_cfg = OAuthProviderConfig.from_connector_config(cfg)
		assert oauth_cfg.authorize_url == "https://auth.other.com/oauth/authorize"
		assert oauth_cfg.token_url == "https://auth.other.com/oauth/token"


# =========================================================================
# OAuth2Service — authorize URL
# =========================================================================


class TestOAuth2ServiceAuthorizeUrl:
	"""Verifies authorization URL generation."""

	@pytest.fixture
	def service(self) -> OAuth2Service:
		return OAuth2Service(_make_config())

	def test_get_authorize_url_contains_required_params(self, service: OAuth2Service) -> None:
		"""The authorize URL should include response_type, client_id, and state."""
		url = service.get_authorize_url(state="random-state")
		assert "response_type=code" in url
		assert "client_id=cid" in url
		assert "state=random-state" in url

	def test_get_authorize_url_includes_scope(self) -> None:
		"""When scopes are configured, the URL should include them."""
		svc = OAuth2Service(_make_config(scopes=["transactions:read", "account:info"]))
		url = svc.get_authorize_url(state="s")
		assert "scope=transactions%3Aread+account%3Ainfo" in url

	def test_get_authorize_url_includes_redirect_uri(self) -> None:
		"""When redirect_uri is set, the URL should include it."""
		svc = OAuth2Service(_make_config(redirect_uri="https://erpnext.example.com/callback"))
		url = svc.get_authorize_url(state="s")
		assert "redirect_uri=https%3A%2F%2Ferpnext.example.com%2Fcallback" in url


# =========================================================================
# OAuth2Service — token exchange
# =========================================================================


class TestOAuth2ServiceTokenExchange:
	"""Verifies authorization code → token exchange."""

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_exchange_code_success(self, mock_post: MagicMock) -> None:
		"""Successful code exchange should return OAuthToken and persist it."""
		service = OAuth2Service(_make_config(client_secret="csecret"))

		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"access_token": "new-access",
			"refresh_token": "new-refresh",
			"token_type": "Bearer",
			"expires_in": 3600,
			"scope": "read",
		}
		mock_post.return_value = mock_response

		mocks = _patch_frappe_db(exists=False)
		try:
			token = service.exchange_code_for_tokens(code="auth-code-123", bank_account="BA-001")

			assert token.access_token == "new-access"
			assert token.refresh_token == "new-refresh"
			assert token.token_type == "Bearer"
			assert token.scope == "read"
			assert token.expires_at is not None

			# Verify HTTP call details
			mock_post.assert_called_once()
			call_data = mock_post.call_args[1]["data"]
			assert call_data["grant_type"] == "authorization_code"
			assert call_data["code"] == "auth-code-123"
		finally:
			_unpatch_frappe_db(mocks)

	@patch("erpnext_bank_import.services.oauth.requests.post")
	@patch("erpnext_bank_import.services.oauth.frappe.logger")
	def test_exchange_code_failure(self, mock_logger: MagicMock, mock_post: MagicMock) -> None:
		"""Non-200 response during code exchange should raise OAuthHandshakeError."""
		service = OAuth2Service(_make_config(client_secret="csecret"))

		mock_response = MagicMock()
		mock_response.status_code = 400
		mock_response.text = '{"error": "invalid_grant"}'
		mock_post.return_value = mock_response

		with pytest.raises(OAuthHandshakeError, match="400"):
			service.exchange_code_for_tokens(code="bad-code", bank_account="BA-001")


# =========================================================================
# OAuth2Service — token refresh
# =========================================================================


class TestOAuth2ServiceTokenRefresh:
	"""Verifies access token refresh."""

	def _setup_service_with_stored_token(self) -> OAuth2Service:
		"""Create a service and mock Frappe DB to simulate a stored token."""
		service = OAuth2Service(_make_config(client_secret="csecret"))

		past_str = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		_patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "old-access",
				"refresh_token": "old-refresh",
				"token_type": "Bearer",
				"expires_at": past_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		return service

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_refresh_token_success(self, mock_post: MagicMock) -> None:
		"""Successful refresh should return a new OAuthToken."""
		service = self._setup_service_with_stored_token()

		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"access_token": "refreshed-access",
			"refresh_token": "new-refresh",
			"token_type": "Bearer",
			"expires_in": 3600,
		}
		mock_post.return_value = mock_response

		token = service.refresh_access_token(bank_account="BA-001")
		assert token.access_token == "refreshed-access"
		assert token.refresh_token == "new-refresh"

		# Verify refresh grant type was used
		call_data = mock_post.call_args[1]["data"]
		assert call_data["grant_type"] == "refresh_token"
		assert call_data["refresh_token"] == "old-refresh"

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_refresh_token_revoked(self, mock_post: MagicMock) -> None:
		"""400 response during refresh should raise TokenRevokedError."""
		service = self._setup_service_with_stored_token()

		mock_response = MagicMock()
		mock_response.status_code = 400
		mock_response.text = '{"error": "invalid_grant"}'
		mock_post.return_value = mock_response

		with pytest.raises(TokenRevokedError):
			service.refresh_access_token(bank_account="BA-001")

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_refresh_without_stored_token(self, mock_post: MagicMock) -> None:
		"""Refresh without a stored token should raise TokenExpiredError."""
		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(exists=False)
		try:
			with pytest.raises(TokenExpiredError, match="No refresh token"):
				svc.refresh_access_token(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)

		mock_post.assert_not_called()


# =========================================================================
# OAuth2Service — get_valid_access_token
# =========================================================================


class TestOAuth2ServiceGetValidToken:
	"""Verifies get_valid_access_token logic (cache, refresh, expiry)."""

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_valid_token_returned_directly(self, mock_now: MagicMock) -> None:
		"""When the stored token is still valid, it should be returned without refresh."""
		future_str = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
		mock_now.return_value = datetime.now()

		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "valid-access",
				"refresh_token": "valid-refresh",
				"token_type": "Bearer",
				"expires_at": future_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			token_str = svc.get_valid_access_token(bank_account="BA-001")
			assert token_str == "valid-access"
		finally:
			_unpatch_frappe_db(mocks)

	@patch("erpnext_bank_import.services.oauth.requests.post")
	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_expired_token_triggers_refresh(self, mock_now: MagicMock, mock_post: MagicMock) -> None:
		"""When the stored token is expired, a refresh should be attempted."""
		past_str = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		mock_now.return_value = datetime.now()

		svc = OAuth2Service(_make_config(client_secret="csecret"))

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "expired-access",
				"refresh_token": "valid-refresh",
				"token_type": "Bearer",
				"expires_at": past_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_response.json.return_value = {
				"access_token": "refreshed-access",
				"refresh_token": "new-refresh",
				"token_type": "Bearer",
				"expires_in": 3600,
			}
			mock_post.return_value = mock_response

			token_str = svc.get_valid_access_token(bank_account="BA-001")
			assert token_str == "refreshed-access"
		finally:
			_unpatch_frappe_db(mocks)

	def test_no_stored_token_raises(self) -> None:
		"""When no token exists, get_valid_access_token should raise TokenExpiredError."""
		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(exists=False)
		try:
			with pytest.raises(TokenExpiredError, match="No stored tokens"):
				svc.get_valid_access_token(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_no_refresh_token_raises(self, mock_now: MagicMock) -> None:
		"""When token is expired and no refresh token exists, raises TokenExpiredError."""
		past_str = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		mock_now.return_value = datetime.now()

		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "expired-access",
				"refresh_token": "",  # No refresh token
				"token_type": "Bearer",
				"expires_at": past_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			with pytest.raises(TokenExpiredError, match="no refresh token"):
				svc.get_valid_access_token(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)


# =========================================================================
# OAuth2Service — token revocation
# =========================================================================


class TestOAuth2ServiceRevoke:
	"""Verifies token revocation."""

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_revoke_with_revoke_url(self, mock_post: MagicMock) -> None:
		"""When revoke_url is configured, it should be called before clearing."""
		svc = OAuth2Service(_make_config(revoke_url="https://auth.example.com/revoke"))

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "access-token",
				"refresh_token": "refresh-token",
				"token_type": "Bearer",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_post.return_value = mock_response

			svc.revoke_tokens(bank_account="BA-001")

			# Verify revocation POST was made
			mock_post.assert_called_once()
			assert mock_post.call_args[0][0] == "https://auth.example.com/revoke"
		finally:
			_unpatch_frappe_db(mocks)

	def test_revoke_without_revoke_url(self) -> None:
		"""When revoke_url is not configured, tokens should just be cleared."""
		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "access-token",
				"refresh_token": "refresh-token",
				"token_type": "Bearer",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			# No HTTP call expected since revoke_url is not configured
			svc.revoke_tokens(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)


# =========================================================================
# OAuth2Service — expiry logic
# =========================================================================


class TestOAuth2ServiceExpiry:
	"""Verifies internal token expiry checking with the safety buffer."""

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_not_expired_when_far_in_future(self, mock_now: MagicMock) -> None:
		"""A token expiring far in the future should not be considered expired."""
		mock_now.return_value = datetime(2026, 7, 10, 12, 0, 0)
		future = datetime(2026, 7, 10, 14, 0, 0)
		assert not OAuth2Service._is_expired(future)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_expired_when_in_past(self, mock_now: MagicMock) -> None:
		"""A token with past expiry should be considered expired."""
		mock_now.return_value = datetime(2026, 7, 10, 12, 0, 0)
		past = datetime(2026, 7, 10, 10, 0, 0)
		assert OAuth2Service._is_expired(past)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_expired_at_exact_expiry(self, mock_now: MagicMock) -> None:
		"""A token at its exact expiry should be considered expired."""
		now = datetime(2026, 7, 10, 12, 0, 0)
		mock_now.return_value = now
		assert OAuth2Service._is_expired(now)


# =========================================================================
# OAuth2Service — token expiry edge cases
# =========================================================================


class TestOAuth2ServiceExpiryEdgeCases:
	"""Additional edge cases for the ``_is_expired`` static method.

	The safety buffer is defined in ``OAuthProviderConfig.token_safety_buffer_seconds``
	but ``_is_expired`` is a static method that does not have access to config.
	These tests document the current behaviour and the gap.
	"""

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_expires_in_30s_is_not_expired(self, mock_now: MagicMock) -> None:
		"""A token expiring in 30 seconds is not yet expired.

		Note: ``_is_expired`` only checks ``expires_at <= now``, so a token
		that expires in 30s is NOT considered expired.  The safety buffer
		(default 60s) that should trigger early refresh is NOT applied here;
		it is the caller's responsibility (e.g. ``get_valid_access_token``)
		to handle pre-emptive refresh.
		"""
		now = datetime(2026, 7, 10, 12, 0, 0)
		mock_now.return_value = now
		almost_expired = datetime(2026, 7, 10, 12, 0, 30)  # 30s from now
		assert not OAuth2Service._is_expired(almost_expired)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_expired_one_second_ago(self, mock_now: MagicMock) -> None:
		"""A token that expired 1 second ago is considered expired (boundary)."""
		now = datetime(2026, 7, 10, 12, 0, 0)
		mock_now.return_value = now
		just_past = datetime(2026, 7, 10, 11, 59, 59)
		assert OAuth2Service._is_expired(just_past)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_aware_datetime_stripped(self, mock_now: MagicMock) -> None:
		"""Timezone-aware datetimes are stripped to naive for comparison."""
		now = datetime(2026, 7, 10, 12, 0, 0)
		mock_now.return_value = now
		# An aware datetime with same clock time (UTC)
		future_aware = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
		assert OAuth2Service._is_expired(future_aware)  # stripped → equal → expired


# =========================================================================
# OAuth2Service — token refresh edge cases
# =========================================================================


class TestOAuth2ServiceTokenRefreshEdgeCases:
	"""Additional edge cases for ``refresh_access_token``."""

	def _setup_service_with_stored_token(
		self,
		access_token: str = "old-access",
		refresh_token: str = "old-refresh",
		expires_at: str | None = None,
	) -> OAuth2Service:
		service = OAuth2Service(_make_config(client_secret="csecret"))
		if expires_at is None:
			expires_at = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		_patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": access_token,
				"refresh_token": refresh_token,
				"token_type": "Bearer",
				"expires_at": expires_at,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		return service

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_token_rotation(self, mock_post: MagicMock) -> None:
		"""On refresh, if provider returns a new refresh token, it replaces the old."""
		service = self._setup_service_with_stored_token()

		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"access_token": "rotated-access",
			"refresh_token": "rotated-refresh",
			"token_type": "Bearer",
			"expires_in": 3600,
		}
		mock_post.return_value = mock_response

		token = service.refresh_access_token(bank_account="BA-001")
		assert token.access_token == "rotated-access"
		assert token.refresh_token == "rotated-refresh"

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_refresh_network_error(self, mock_post: MagicMock) -> None:
		"""Transient network error during refresh should propagate as OAuthHandshakeError."""
		service = self._setup_service_with_stored_token()

		import requests

		mock_post.side_effect = requests.ConnectionError("Connection refused")

		with pytest.raises(OAuthHandshakeError, match="Token request failed"):
			service.refresh_access_token(bank_account="BA-001")

	def test_refresh_without_any_stored_data(self) -> None:
		"""When no token record exists, raises TokenExpiredError immediately."""
		svc = OAuth2Service(_make_config())
		mocks = _patch_frappe_db(exists=False)
		try:
			with pytest.raises(TokenExpiredError, match="No refresh token"):
				svc.refresh_access_token(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)


# =========================================================================
# OAuth2Service — auto-refresh edge cases
# =========================================================================


class TestOAuth2ServiceAutoRefreshEdgeCases:
	"""Edge cases for ``get_valid_access_token`` auto-refresh behaviour."""

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_network_error_during_auto_refresh(self, mock_post: MagicMock, mock_now: MagicMock) -> None:
		"""When auto-refresh fails with network error, TokenExpiredError is raised."""
		past_str = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		mock_now.return_value = datetime.now()

		svc = OAuth2Service(_make_config(client_secret="csecret"))

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "expired-access",
				"refresh_token": "valid-refresh",
				"token_type": "Bearer",
				"expires_at": past_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			import requests

			mock_post.side_effect = requests.ConnectionError("Connection refused")

			with pytest.raises(TokenExpiredError, match="Token refresh failed"):
				svc.get_valid_access_token(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks)

	@patch("erpnext_bank_import.services.oauth.now_datetime")
	def test_token_expiring_soon_not_refreshed_if_still_valid(self, mock_now: MagicMock) -> None:
		"""A token expiring in 30s is still valid (safety buffer not in _is_expired).

		This documents a known gap: the safety buffer is NOT enforced by
		``_is_expired``, so a token with 30s remaining is returned directly
		without refresh.
		"""
		future_str = (datetime.now() + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
		mock_now.return_value = datetime.now()

		svc = OAuth2Service(_make_config())

		mocks = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "almost-valid",
				"refresh_token": "refresh-token",
				"token_type": "Bearer",
				"expires_at": future_str,
				"scope": "read",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			token_str = svc.get_valid_access_token(bank_account="BA-001")
			assert token_str == "almost-valid"
		finally:
			_unpatch_frappe_db(mocks)


# =========================================================================
# OAuth2Service — full lifecycle integration
# =========================================================================


class TestOAuth2Lifecycle:
	"""End-to-end token lifecycle: authorize → exchange → access → refresh → revoke."""

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_full_lifecycle(self, mock_post: MagicMock) -> None:
		"""Complete OAuth flow: authorize URL → code exchange → access → refresh → revoke."""
		svc = OAuth2Service(
			_make_config(
				client_id="lifecycle-cid",
				client_secret="lifecycle-secret",
				revoke_url="/auth/revoke",
			)
		)

		# 1. Authorization URL
		auth_url = svc.get_authorize_url(state="state-123")
		assert "response_type=code" in auth_url
		assert "client_id=lifecycle-cid" in auth_url
		assert "state=state-123" in auth_url

		# 2. Exchange code for tokens
		def _exchange_side_effect(*args, **kwargs):
			resp = MagicMock()
			resp.status_code = 200
			resp.json.return_value = {
				"access_token": "lifecycle-access",
				"refresh_token": "lifecycle-refresh",
				"token_type": "Bearer",
				"expires_in": 3600,
				"scope": "read",
			}
			return resp

		mock_post.side_effect = _exchange_side_effect

		mocks = _patch_frappe_db(exists=False)
		try:
			token = svc.exchange_code_for_tokens(code="auth-code-456", bank_account="BA-001")
			assert token.access_token == "lifecycle-access"
			assert token.refresh_token == "lifecycle-refresh"
		finally:
			_unpatch_frappe_db(mocks)

		# 3. Use valid access token (cached, no refresh call)
		@patch("erpnext_bank_import.services.oauth.now_datetime")
		def _check_valid(mock_now):
			future_str = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
			mock_now.return_value = datetime.now()
			mocks2 = _patch_frappe_db(
				exists=True,
				doc_kwargs={
					"access_token": "lifecycle-access",
					"refresh_token": "lifecycle-refresh",
					"token_type": "Bearer",
					"expires_at": future_str,
					"scope": "read",
					"provider_name": "test",
					"bank_account": "BA-001",
				},
			)
			try:
				access = svc.get_valid_access_token(bank_account="BA-001")
				assert access == "lifecycle-access"
			finally:
				_unpatch_frappe_db(mocks2)

		_check_valid()

		# 4. Revoke tokens
		mocks3 = _patch_frappe_db(
			exists=True,
			doc_kwargs={
				"access_token": "lifecycle-access",
				"refresh_token": "lifecycle-refresh",
				"provider_name": "test",
				"bank_account": "BA-001",
			},
		)
		try:
			svc.revoke_tokens(bank_account="BA-001")
		finally:
			_unpatch_frappe_db(mocks3)

	@patch("erpnext_bank_import.services.oauth.requests.post")
	def test_refresh_after_revoke_fails(self, mock_post: MagicMock) -> None:
		"""After revocation, refresh_access_token should raise TokenExpiredError."""
		svc = OAuth2Service(_make_config(client_secret="csecret"))

		mocks = _patch_frappe_db(exists=False)
		try:
			with pytest.raises(TokenExpiredError, match="No refresh token"):
				svc.refresh_access_token(bank_account="BA-001")
			mock_post.assert_not_called()
		finally:
			_unpatch_frappe_db(mocks)


# =========================================================================
# JWT client assertion tests
# =========================================================================


# A 2048-bit RSA private key for testing JWT signing (PKCS#1 format).
_TEST_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEogIBAAKCAQEAj8H/8k4Tr0CP02DqsF6Vl2PjDCJ0MZvZpO6qprJqO0395UVe
CmiPD5pP/zDaPzbnq50Uut/QPWcCCKYViklBjPWazua8dCeO/DMLa93UzHhaYdQb
NWrqRykv+p4uso7HJOkzrt1vy4jyZgagFrQGXMXcsFEQwsgCiD3NMIHRk3T2kjZc
nRJdtJneAcF6qk2aBu8CYUea+Ad6pi1Di/ofjWwiS15XsyZQVM8nktpcPynSb6bz
O43xgBxaIWCiSfNS75EsmOOxFqyQBRjVogwqWQTzgPgHo1D2zmeHXjyyAPahCO3o
iaFaQf2fJhhy+OSXXnjaVizPIYeLU0boH+fuTwIDAQABAoIBAAu1b4fIP9vbzyXB
HzcF6nrd+ghV0WSYVDCV9ynu89kTCqAUYiBIkN0NTVZfH7+F/3Y1AT277KWQ+2js
l2o1JOolYkUCJQz0NeCpCv/vnZ1DxjTGm6q3o7pFCrEya2JECnNMAoIhFgccBzDe
eZvacNQ4kgzS+sxVPGOQCQ4vh1F9ANzZF9WFIZXP177tM4jeNMRA2vkelHSw0Xpg
zYJl/KpWGiNvTwYvcep78Su8GNofG6pr2ksQ97XAAGWNplFX24sQxEGdMKp6K+TN
EgmdSd0+2Jt6MaZ0ONaPO+DaafGrALCJz8bokAbwvVSFqPtvF5zAmEvWzI/KXvgs
wWBpmFECgYEAxv7sTCLsBtyiBE/dEbVmmmlur94WDVhG6HUpdiptb1RhjAIE4up9
LDiqNliptUOzUFTz5pOo1lpMIhNaSJl+tamEPKL3fgzLiosRpP/SK+aHbmhrpc2/
hYIhqkLwmSAK40Ocfb3tw7C1PkpCBl2tUxYnKNwEVzSaDnZQHwlLCH8CgYEAuPBG
6AKGPL2JtWJ1MZWD6FtX3ZadWL5gkmv92knblCiyxDClEwjbJFpnECUND+hBzYsR
Vejwhe1a1sY0A5YV2gzP3IbZ12dkUyYBM+j5pjVCh1vD46M7lLwKBWuhppZMYvcl
3utOef/dV/tOuPgoa8jdXXaWqQYV7+hqYandsjECgYAqM+hTYVijP+mQdouQ9OLU
vqV94ODWZbFsHWT0rZzV7pRdiBQXN9niJgZbTkR3r+r4j3vGm+xDwZTB6U7NdNg9
mLz1yy4n6njEYigU0Th2nQZ98OFboZ4Lp4SSQm4aW4RTnIQ02rHxPanCkycbiIR4
yYr2jGrTP9GoXYkye9sQ6wKBgH0CjiuOaUbtqARgBW/67StHc2FpyfqO1aCkNvgz
LKY9zHkpmKwBNICiS0Byix3RlYlnE9TKnKsrAlhjqg0yiprWRjt/PAmK7hn2eqGo
PfjHz6zHruZVFJU5dlyroJ2GwyOyhHrm/Ckjd29dhJ0rwcb6BAiFfNnML0/3/tD9
jcpBAoGAdgN7MlrJ7ZQqkW7mJaFQyybfjkwseweU/fT6WQrRPtTu31haojnsIsCi
3yJMXgCaCJZC1ICZIFwNzGwmHT7TRwc18JFbabwFhqtiw9OfWbZq1sY9ljXu/iZG
LGeKisrZd3ZhhDF4BArMwvbJLPXZDyUsjjLtzBAxljruSn50bh0=
-----END RSA PRIVATE KEY-----"""


class TestJwtClientAssertion:
	"""Verifies JWT client assertion generation and usage."""

	def test_jwt_assertion_is_signed(self):
		"""JWT is correctly built and RS256-signed when jwt_private_key is set."""
		cfg = _make_config(
			jwt_private_key=_TEST_PRIVATE_KEY,
			jwt_issuer="erpnext.example.com",
		)
		svc = OAuth2Service(cfg)

		# Access internal method directly for unit testing.
		jwt_str = svc._build_client_assertion_jwt()

		assert jwt_str is not None
		assert isinstance(jwt_str, str)
		# JWT has three dot-separated segments.
		assert jwt_str.count(".") == 2

		# Decode without verification to inspect claims.
		import jwt as pyjwt

		payload = pyjwt.decode(jwt_str, options={"verify_signature": False})
		assert payload["iss"] == "erpnext.example.com"
		assert payload["sub"] == "cid"
		assert payload["aud"] == "https://revolut.com"
		assert "exp" in payload
		assert "iat" in payload
		assert "jti" in payload
		assert isinstance(payload["jti"], str)
		assert len(payload["jti"]) > 0

	def test_jwt_assertion_default_issuer(self):
		"""iss defaults to client_id when jwt_issuer is not set."""
		cfg = _make_config(jwt_private_key=_TEST_PRIVATE_KEY, jwt_issuer=None)
		svc = OAuth2Service(cfg)

		import jwt as pyjwt

		jwt_str = svc._build_client_assertion_jwt()
		payload = pyjwt.decode(jwt_str, options={"verify_signature": False})
		assert payload["iss"] == "cid"  # Falls back to client_id

	def test_each_call_generates_unique_jwt(self):
		"""Each call to _build_client_assertion_jwt produces a different jti."""
		cfg = _make_config(jwt_private_key=_TEST_PRIVATE_KEY)
		svc = OAuth2Service(cfg)

		import jwt as pyjwt

		jwt1 = svc._build_client_assertion_jwt()
		jwt2 = svc._build_client_assertion_jwt()

		assert jwt1 != jwt2  # Different timestamps + jti

		payload1 = pyjwt.decode(jwt1, options={"verify_signature": False})
		payload2 = pyjwt.decode(jwt2, options={"verify_signature": False})
		assert payload1["jti"] != payload2["jti"]

	def test_token_request_includes_jwt_assertion(self):
		"""Token request uses client_assertion instead of client_secret."""
		cfg = _make_config(
			jwt_private_key=_TEST_PRIVATE_KEY,
			client_secret=None,  # Not needed with JWT
		)
		svc = OAuth2Service(cfg)

		with patch("erpnext_bank_import.services.oauth.requests.post") as mock_post:
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_response.json.return_value = {
				"access_token": "jwt-access-token",
				"refresh_token": "jwt-refresh-token",
				"expires_in": 2400,
				"token_type": "bearer",
			}
			mock_post.return_value = mock_response

			# Call exchange code (which calls _token_request internally).
			mocks = _patch_frappe_db(exists=False)
			try:
				token = svc.exchange_code_for_tokens(
					code="test-auth-code",
					bank_account="BA-001",
				)
				assert token.access_token == "jwt-access-token"
			finally:
				_unpatch_frappe_db(mocks)

			# Verify the POST request included JWT assertion fields.
			call_kwargs = mock_post.call_args[1]
			data = call_kwargs.get("data", {})

			assert "client_assertion" in data
			assert "client_assertion_type" in data
			assert data["client_assertion_type"] == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
			assert "client_secret" not in data  # Not sent when JWT is used

	def test_missing_jwt_private_key_raises(self):
		"""Building a JWT assertion without jwt_private_key raises."""
		cfg = _make_config(jwt_private_key=None)
		svc = OAuth2Service(cfg)

		with pytest.raises(ConfigurationError, match="jwt_private_key"):
			svc._build_client_assertion_jwt()

	def test_provider_config_passes_through_jwt_fields(self):
		"""OAuthProviderConfig.from_connector_config preserves JWT fields."""
		cfg = _make_config(
			jwt_private_key=_TEST_PRIVATE_KEY,
			jwt_issuer="my.erp.com",
		)
		provider_cfg = OAuthProviderConfig.from_connector_config(cfg)

		assert provider_cfg.jwt_private_key == _TEST_PRIVATE_KEY
		assert provider_cfg.jwt_issuer == "my.erp.com"

	def test_token_request_omits_client_secret_with_jwt(self):
		"""client_secret is not sent when JWT assertion is configured."""
		cfg = _make_config(
			jwt_private_key=_TEST_PRIVATE_KEY,
			client_secret="should-not-be-sent",
		)
		svc = OAuth2Service(cfg)

		with patch("erpnext_bank_import.services.oauth.requests.post") as mock_post:
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_response.json.return_value = {
				"access_token": "test",
				"refresh_token": "test",
				"expires_in": 2400,
			}
			mock_post.return_value = mock_response

			mocks = _patch_frappe_db(exists=False)
			try:
				svc.exchange_code_for_tokens(code="code", bank_account="BA-001")
			finally:
				_unpatch_frappe_db(mocks)

			data = mock_post.call_args[1].get("data", {})
			assert "client_secret" not in data
			assert "client_assertion" in data
