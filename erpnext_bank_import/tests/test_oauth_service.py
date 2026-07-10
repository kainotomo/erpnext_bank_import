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
