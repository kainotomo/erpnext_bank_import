"""Tests for the Eurobank (Hellenic Bank) B2B API connector.

These tests validate:

1. Configuration validation — client_id/client_secret required for Eurobank
2. Account discovery — ``GET /v2/b2b/accounts`` parsing, inactive filter, error handling
3. NotImplemented — fetch_transactions and normalize_transaction raise NotImplementedError
4. Error handling — 401, 429, 5xx, network errors
5. Rate limiting — respects ``rate_limit_rps``
6. OAuth flow — start_oauth_flow builds correct URL, oauth_callback exchanges code

All Frappe-dependent code paths are mocked.  HTTP calls are mocked via
``unittest.mock.patch``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.eurobank import EurobankConnector
from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	NetworkError,
	NormalizationError,
	RateLimitError,
	ServerError,
)
from erpnext_bank_import.tests.fixtures.eurobank_fixtures import (
	MOCK_ACCOUNTS_RESPONSE,
	MOCK_ACCOUNTS_RESPONSE_CLOSED_ACCOUNT,
	MOCK_EMPTY_ACCOUNTS_RESPONSE,
	MOCK_ERROR_401,
	MOCK_ERROR_429,
	MOCK_ERROR_500,
	MOCK_TOKEN_ERROR_INVALID_CLIENT,
	MOCK_TOKEN_ERROR_INVALID_CODE,
	MOCK_TOKEN_RESPONSE,
	MOCK_TRANSACTIONS_EMPTY,
	MOCK_TRANSACTIONS_MULTI_PAGE,
	MOCK_TRANSACTIONS_RESPONSE,
	MOCK_TRANSACTIONS_RESPONSE_PAGE_2,
	MOCK_TRANSACTIONS_WITH_PENDING,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_eurobank_config(**overrides: Any) -> ConnectorConfig:
	"""Build a minimal ``ConnectorConfig`` for a Eurobank connector."""
	defaults: dict[str, Any] = {
		"provider_name": "eurobank",
		"api_base_url": "https://sandbox-apis.hellenicbank.com",
		"auth_method": "oauth2",
		"client_id": "test-client-id-000000000000000000000000",
		"client_secret": "test-client-secret-00000000-0000-0000-0000-000000000000",
		"authorize_url": "https://sandbox-oauth.hellenicbank.com/v2/oauth2/auth",
		"token_url": "https://sandbox-oauth.hellenicbank.com/v2/token/exchange",
		"redirect_uri": "https://127.0.0.1:8000/api/method/erpnext_bank_import.connectors.eurobank.oauth_callback",
		"scopes": [
			"v2.b2b.get.accounts",
			"v2.b2b.get.account.details",
			"v2.b2b.get.account.transactions",
		],
		"extra": {
			"sandbox": True,
			"token_request_style": "query",
			"scope_separator": ",",
		},
	}
	defaults.update(**overrides)
	return ConnectorConfig(**defaults)


def _make_connector(**config_overrides: Any) -> EurobankConnector:
	"""Build a ``EurobankConnector`` with a test config and mocked OAuth2Service."""
	config = _make_eurobank_config(**config_overrides)
	conn = EurobankConnector(config=config)
	conn._current_bank_account = "Test Bank Account"
	# Replace OAuth2Service with a mock.
	mock_oauth = MagicMock()
	mock_oauth.get_valid_access_token.return_value = "mock-access-token"
	conn._oauth = mock_oauth
	return conn


class MockResponse:
	"""Helper to simulate ``requests.Response`` objects."""

	def __init__(self, status_code: int, json_data: Any, text: str | None = None) -> None:
		self.status_code = status_code
		self._json_data = json_data
		self.text = text or str(json_data)
		self.headers: dict[str, str] = {}

	def json(self) -> Any:
		return self._json_data

	def raise_for_status(self) -> None:
		if self.status_code >= 400:
			raise requests.HTTPError(response=self)


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestEurobankConfigValidation:
	"""Configuration validation tests."""

	def test_valid_config(self) -> None:
		"""A valid Eurobank config should not raise."""
		config = _make_eurobank_config()
		conn = EurobankConnector(config=config)
		assert conn.config.provider_name == "eurobank"

	def test_missing_client_id(self) -> None:
		"""Missing client_id should raise ConfigurationError."""
		config = _make_eurobank_config(client_id=None)
		with pytest.raises(ConfigurationError, match="client_id"):
			EurobankConnector(config=config)

	def test_missing_client_secret(self) -> None:
		"""Missing client_secret should raise ConfigurationError."""
		config = _make_eurobank_config(client_secret=None)
		with pytest.raises(ConfigurationError, match="client_secret"):
			EurobankConnector(config=config)

	def test_missing_authorize_url(self) -> None:
		"""Missing authorize_url should raise ConfigurationError."""
		config = _make_eurobank_config(authorize_url=None)
		with pytest.raises(ConfigurationError, match="authorize_url"):
			EurobankConnector(config=config)

	def test_missing_token_url(self) -> None:
		"""Missing token_url should raise ConfigurationError."""
		config = _make_eurobank_config(token_url=None)
		with pytest.raises(ConfigurationError, match="token_url"):
			EurobankConnector(config=config)

	def test_missing_redirect_uri(self) -> None:
		"""Missing redirect_uri should raise ConfigurationError."""
		config = _make_eurobank_config(redirect_uri=None)
		with pytest.raises(ConfigurationError, match="redirect_uri"):
			EurobankConnector(config=config)

	def test_missing_api_base_url(self) -> None:
		"""Missing api_base_url should raise ConfigurationError."""
		with pytest.raises(ConfigurationError, match="api_base_url"):
			EurobankConnector(config=ConnectorConfig(provider_name="eurobank", api_base_url=""))

	def test_sandbox_detected(self) -> None:
		"""Sandbox mode should be reflected in extra config."""
		config = _make_eurobank_config(extra={"sandbox": True, "token_request_style": "query", "scope_separator": ","})
		conn = EurobankConnector(config=config)
		assert conn.config.extra.get("sandbox") is True

	def test_production_mode(self) -> None:
		"""Production mode when sandbox is False."""
		config = _make_eurobank_config(api_base_url="https://apisprod.hellenicbank.com", extra={"sandbox": False, "token_request_style": "query", "scope_separator": ","})
		conn = EurobankConnector(config=config)
		assert conn.config.extra.get("sandbox") is False


# ---------------------------------------------------------------------------
# Account discovery
# ---------------------------------------------------------------------------


class TestEurobankAccountDiscovery:
	"""Account discovery tests."""

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_returns_active_only(self, mock_api_get: MagicMock) -> None:
		"""Only active accounts should be returned."""
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE
		conn = _make_connector()
		accounts = conn.get_accounts()

		assert len(accounts) == 3
		for acc in accounts:
			assert acc.account_id in ("0990145684237", "1765925862102", "0916845862235")

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_eur_account(self, mock_api_get: MagicMock) -> None:
		"""EUR account should have correct fields."""
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE
		conn = _make_connector()
		accounts = conn.get_accounts()

		eur_acc = next(a for a in accounts if a.currency == "EUR")
		assert eur_acc.account_id == "0990145684237"
		assert eur_acc.account_name == "TrustEdge Solutions"
		assert eur_acc.currency == "EUR"
		assert eur_acc.iban == "CY59005000990000990145684237"
		assert eur_acc.account_number == "0990145684237"

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_usd_account(self, mock_api_get: MagicMock) -> None:
		"""USD account should have correct fields."""
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE
		conn = _make_connector()
		accounts = conn.get_accounts()

		usd_acc = next(a for a in accounts if a.currency == "USD")
		assert usd_acc.account_id == "1765925862102"
		assert usd_acc.currency == "USD"
		assert usd_acc.iban == "CY19005000990001765925862102"

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_skips_closed(self, mock_api_get: MagicMock) -> None:
		"""CLOSED accounts should be filtered out."""
		# The closed account response has only one closed account.
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE_CLOSED_ACCOUNT
		conn = _make_connector()
		accounts = conn.get_accounts()

		assert len(accounts) == 0

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_empty_list(self, mock_api_get: MagicMock) -> None:
		"""Empty account list should return empty list."""
		mock_api_get.return_value = MOCK_EMPTY_ACCOUNTS_RESPONSE
		conn = _make_connector()
		accounts = conn.get_accounts()

		assert len(accounts) == 0

	@patch.object(EurobankConnector, "_api_get")
	def test_get_accounts_provider_metadata(self, mock_api_get: MagicMock) -> None:
		"""Provider metadata should include extra fields."""
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE
		conn = _make_connector()
		accounts = conn.get_accounts()

		eur_acc = next(a for a in accounts if a.currency == "EUR")
		assert eur_acc.provider_metadata["accountType"] == "CURRENT_ACCOUNT"
		assert eur_acc.provider_metadata["status"] == "ACTIVE"
		assert "balances" in eur_acc.provider_metadata
		assert eur_acc.provider_metadata["subscriberName"] == "TrustEdge Solutions"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestEurobankErrorHandling:
	"""API error handling tests."""

	@patch.object(requests.Session, "get")
	def test_401_raises_authentication_error(self, mock_get: MagicMock) -> None:
		"""HTTP 401 should raise AuthenticationError."""
		mock_get.return_value = MockResponse(401, MOCK_ERROR_401)
		conn = _make_connector()

		with pytest.raises(AuthenticationError, match="401"):
			conn._api_get("/v2/b2b/accounts")

	@patch.object(requests.Session, "get")
	def test_429_raises_rate_limit_error(self, mock_get: MagicMock) -> None:
		"""HTTP 429 should raise RateLimitError."""
		mock_get.return_value = MockResponse(429, MOCK_ERROR_429)
		conn = _make_connector()

		with pytest.raises(RateLimitError, match="429"):
			conn._api_get("/v2/b2b/accounts")

	@patch.object(requests.Session, "get")
	def test_429_retry_after_header(self, mock_get: MagicMock) -> None:
		"""RateLimitError should expose retry_after from headers."""
		resp = MockResponse(429, MOCK_ERROR_429)
		resp.headers["Retry-After"] = "30"
		mock_get.return_value = resp
		conn = _make_connector()

		with pytest.raises(RateLimitError) as exc_info:
			conn._api_get("/v2/b2b/accounts")
		assert exc_info.value.retry_after == 30.0

	@patch.object(requests.Session, "get")
	def test_500_raises_server_error(self, mock_get: MagicMock) -> None:
		"""HTTP 500 should raise ServerError."""
		mock_get.return_value = MockResponse(500, MOCK_ERROR_500)
		conn = _make_connector()

		with pytest.raises(ServerError, match="500"):
			conn._api_get("/v2/b2b/accounts")

	@patch.object(requests.Session, "get")
	def test_unknown_error_raises_api_error(self, mock_get: MagicMock) -> None:
		"""HTTP 403 should raise ApiError."""
		mock_get.return_value = MockResponse(403, {"error": "FORBIDDEN"})
		conn = _make_connector()

		with pytest.raises(ApiError, match="403"):
			conn._api_get("/v2/b2b/accounts")

	@patch.object(requests.Session, "get")
	def test_network_error(self, mock_get: MagicMock) -> None:
		"""Network failure should raise NetworkError."""
		mock_get.side_effect = requests.ConnectionError("Connection refused")
		conn = _make_connector()

		with pytest.raises(NetworkError, match="Network error"):
			conn._api_get("/v2/b2b/accounts")


# ---------------------------------------------------------------------------
# NotImplemented methods
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestEurobankRateLimiting:
	"""Rate-limiting tests."""

	@patch.object(EurobankConnector, "_get_access_token")
	@patch.object(requests.Session, "get")
	def test_rate_limit_respected(self, mock_get: MagicMock, mock_token: MagicMock) -> None:
		"""Requests should be throttled to respect rate_limit_rps."""
		mock_token.return_value = "test-token"
		mock_get.return_value = MockResponse(200, MOCK_ACCOUNTS_RESPONSE)

		config = _make_eurobank_config(rate_limit_rps=2.0)
		conn = EurobankConnector(config=config)
		conn._current_bank_account = "Test"

		import time

		start = time.monotonic()
		conn._api_get("/v2/b2b/accounts")
		conn._api_get("/v2/b2b/accounts")
		elapsed = time.monotonic() - start

		# At 2 RPS with 2 calls, should take at least ~0.5s.
		assert elapsed >= 0.4


# ---------------------------------------------------------------------------
# Token / OAuth flow
# ---------------------------------------------------------------------------


class TestEurobankOAuth:
	"""OAuth flow tests."""

	@patch.object(EurobankConnector, "_get_access_token")
	def test_get_access_token_returns_mock(self, mock_token: MagicMock) -> None:
		"""Mock token should be returned."""
		mock_token.return_value = "mock-access-token"
		conn = _make_connector()
		token = conn._get_access_token()
		assert token == "mock-access-token"

	def test_get_access_token_no_account(self) -> None:
		"""Calling _get_access_token without setting account should raise."""
		conn = _make_connector()
		conn._current_bank_account = None
		with pytest.raises(AuthenticationError, match="No bank account set"):
			conn._get_access_token()

	@patch.object(EurobankConnector, "_api_get")
	def test_account_discovery_calls_correct_endpoint(self, mock_api_get: MagicMock) -> None:
		"""get_accounts should call /v2/b2b/accounts."""
		mock_api_get.return_value = MOCK_ACCOUNTS_RESPONSE
		conn = _make_connector()
		conn.get_accounts()
		mock_api_get.assert_called_once_with("/v2/b2b/accounts")


# ---------------------------------------------------------------------------
# OAuth flow (module-level functions)
# ---------------------------------------------------------------------------


class TestEurobankOAuthFlowFunctions:
	"""Tests for the start_oauth_flow and oauth_callback module-level functions."""

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	def test_start_oauth_flow_checks_enabled(self, mock_frappe: MagicMock) -> None:
		"""Disabled connector should throw."""
		mock_doc = MagicMock()
		mock_doc.enabled = False
		mock_frappe.get_doc.return_value = mock_doc
		# Make frappe._() return a real string so formatting works.
		mock_frappe._ = lambda s, **_: s

		# Configure frappe.throw to actually raise an exception.
		mock_frappe.throw.side_effect = lambda msg: (_ for _ in ()).throw(Exception(msg))

		from erpnext_bank_import.connectors.eurobank import start_oauth_flow

		with pytest.raises(Exception, match="disabled"):
			start_oauth_flow("test-connector")

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	@patch("erpnext_bank_import.services.oauth.OAuth2Service")
	def test_start_oauth_flow_returns_url(
		self, mock_oauth_cls: MagicMock, mock_frappe: MagicMock
	) -> None:
		"""start_oauth_flow should return a consent URL."""
		mock_doc = MagicMock()
		mock_doc.enabled = True
		mock_doc.get_connector_config.return_value = _make_eurobank_config()
		mock_frappe.get_doc.return_value = mock_doc
		mock_frappe.cache.return_value.get.return_value = None
		mock_frappe._ = lambda s, **_: s
		mock_cache = MagicMock()
		mock_frappe.cache.return_value = mock_cache

		mock_oauth = MagicMock()
		mock_oauth.get_authorize_url.return_value = "https://sandbox-oauth.hellenicbank.com/v2/oauth2/auth?response_type=code&client_id=test&state=abc"
		mock_oauth_cls.return_value = mock_oauth

		from erpnext_bank_import.connectors.eurobank import start_oauth_flow

		url = start_oauth_flow("test-connector")

		assert url.startswith("https://")
		mock_cache.setex.assert_called_once()

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	@patch("erpnext_bank_import.services.oauth.OAuth2Service")
	def test_oauth_callback_exchanges_code(
		self, mock_oauth_cls: MagicMock, mock_frappe: MagicMock
	) -> None:
		"""oauth_callback should exchange the code and persist tokens."""
		mock_doc = MagicMock()
		mock_doc.enabled = True
		mock_doc.get_connector_config.return_value = _make_eurobank_config()
		mock_frappe.get_doc.return_value = mock_doc
		mock_frappe.cache.return_value.get.return_value = "test-connector"

		mock_oauth = MagicMock()
		mock_oauth_cls.return_value = mock_oauth

		from erpnext_bank_import.connectors.eurobank import oauth_callback

		result = oauth_callback(code="test-code", state="test-state")

		assert "message" in result
		mock_oauth.exchange_code_for_tokens.assert_called_once_with(
			code="test-code", bank_account="test-connector"
		)

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	def test_oauth_callback_missing_code(self, mock_frappe: MagicMock) -> None:
		"""Missing code should throw."""
		mock_frappe._ = lambda s, **_: s
		mock_frappe.throw.side_effect = lambda msg: (_ for _ in ()).throw(Exception(msg))

		from erpnext_bank_import.connectors.eurobank import oauth_callback

		with pytest.raises(Exception, match="Missing authorization code"):
			oauth_callback(code=None, state="test-state")

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	def test_oauth_callback_missing_state(self, mock_frappe: MagicMock) -> None:
		"""Missing state should throw."""
		mock_frappe._ = lambda s, **_: s
		mock_frappe.throw.side_effect = lambda msg: (_ for _ in ()).throw(Exception(msg))

		from erpnext_bank_import.connectors.eurobank import oauth_callback

		with pytest.raises(Exception, match="Missing state"):
			oauth_callback(code="test-code", state=None)

	@patch("erpnext_bank_import.connectors.eurobank.frappe")
	def test_oauth_callback_expired_state(self, mock_frappe: MagicMock) -> None:
		"""Expired state should throw."""
		mock_frappe.cache.return_value.get.return_value = None
		mock_frappe._ = lambda s, **_: s
		mock_frappe.throw.side_effect = lambda msg: (_ for _ in ()).throw(Exception(msg))

		from erpnext_bank_import.connectors.eurobank import oauth_callback

		with pytest.raises(Exception, match="expired or invalid"):
			oauth_callback(code="test-code", state="expired-state")


# ---------------------------------------------------------------------------
# Auth lifecycle methods
# ---------------------------------------------------------------------------


class TestEurobankAuthLifecycle:
	"""Auth lifecycle tests."""

	def test_authenticate_is_noop(self) -> None:
		"""authenticate should not raise."""
		conn = _make_connector()
		conn.authenticate()  # should not raise

	def test_is_authenticated_returns_false(self) -> None:
		"""is_authenticated should return False."""
		conn = _make_connector()
		assert conn.is_authenticated() is False

	def test_refresh_token_is_noop(self) -> None:
		"""refresh_token should not raise."""
		conn = _make_connector()
		conn.refresh_token()  # should not raise


# ---------------------------------------------------------------------------
# Transaction fetch
# ---------------------------------------------------------------------------


class TestEurobankTransactionFetch:
	"""Transaction fetching tests."""

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_returns_correct_count(self, mock_api_get: MagicMock) -> None:
		"""fetch_transactions should return all completed transactions."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_RESPONSE
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, next_token = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		assert len(txns) == 4

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_skips_pending(self, mock_api_get: MagicMock) -> None:
		"""Pending transactions should be filtered out."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_WITH_PENDING
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, next_token = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		assert len(txns) == 1
		assert txns[0]["external_id"] == "txn-completed-001"

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_empty_list(self, mock_api_get: MagicMock) -> None:
		"""Empty transaction list should return empty list."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_EMPTY
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, next_token = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		assert len(txns) == 0
		assert next_token is None

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_with_pagination(self, mock_api_get: MagicMock) -> None:
		"""When nextPage is present, next_token should be the next page number."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_MULTI_PAGE
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, next_token = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		assert len(txns) == 100
		# nextPage URL was present, so next_token should be page 1.
		assert next_token == "1"

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_second_page(self, mock_api_get: MagicMock) -> None:
		"""Passing a page_token should request the correct page."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_RESPONSE_PAGE_2
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, next_token = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
			page_token="1",
		)

		assert len(txns) == 1
		assert txns[0]["external_id"] == "e1f2g3h4i5j6"
		assert next_token is None  # page 2 has no nextPage URL

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_debit_amount_negative(self, mock_api_get: MagicMock) -> None:
		"""Debit transactions should have negative amounts."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_RESPONSE
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, _ = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		# First transaction is DEBIT for 72000.00
		assert txns[0]["amount"] == -72000.00

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_credit_amount_positive(self, mock_api_get: MagicMock) -> None:
		"""Credit transactions should have positive amounts."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_RESPONSE
		conn = _make_connector()
		conn._current_bank_account = "Test"
		txns, _ = conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		# Fourth transaction is CREDIT for 25000.00
		assert txns[3]["amount"] == 25000.00

	@patch.object(EurobankConnector, "_api_get")
	def test_fetch_passes_correct_params(self, mock_api_get: MagicMock) -> None:
		"""Verify that the correct date format and params are sent."""
		mock_api_get.return_value = MOCK_TRANSACTIONS_EMPTY
		conn = _make_connector()
		conn._current_bank_account = "Test"
		conn.fetch_transactions(
			account_id="0990145684237",
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		# Verify the API was called with the correct endpoint and params.
		expected_path = "/v2/b2b/accounts/0990145684237/transactions"
		args, kwargs = mock_api_get.call_args
		assert args[0] == expected_path
		params = kwargs.get("params", {})
		assert params["dateFrom"] == "202601010000"
		assert params["dateTo"] == "202601312359"
		assert params["bookingStatus"] == "booked"
		assert params["limit"] == 100


# ---------------------------------------------------------------------------
# Transaction normalisation
# ---------------------------------------------------------------------------


class TestEurobankTransactionNormalisation:
	"""Transaction normalisation tests."""

	def test_normalize_debit(self) -> None:
		"""DEBIT transaction should have negative amount."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-001"},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "DEBIT",
			"transactionAmount": {"currency": "EUR", "amount": 150.00},
			"description": "Test debit",
			"type": "TRANSFER",
			"counterParty": {"name": "Vendor Ltd", "accountNumber": "ACC-123"},
			"status": "COMPLETED",
		}
		txn = conn.normalize_transaction(raw)
		assert txn["external_id"] == "ref-001"
		assert txn["date"] == "2026-01-15"
		assert txn["amount"] == -150.00
		assert txn["currency"] == "EUR"
		assert txn["description"] == "Test debit"
		assert txn["transaction_type"] == "TRANSFER"
		assert txn["bank_party_name"] == "Vendor Ltd"
		assert txn["bank_party_account_number"] == "ACC-123"

	def test_normalize_credit(self) -> None:
		"""CREDIT transaction should have positive amount."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-002"},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "USD", "amount": 5000.00},
			"description": "Salary deposit",
			"type": "TRANSFER",
			"counterParty": {"name": "Employer Inc"},
			"status": "COMPLETED",
		}
		txn = conn.normalize_transaction(raw)
		assert txn["amount"] == 5000.00
		assert txn["currency"] == "USD"

	def test_normalize_missing_reference_id(self) -> None:
		"""Missing referenceId should raise NormalizationError."""
		conn = _make_connector()
		raw = {
			"references": {},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "EUR", "amount": 100.00},
			"description": "Test",
		}
		with pytest.raises(NormalizationError, match="referenceId"):
			conn.normalize_transaction(raw)

	def test_normalize_missing_amount(self) -> None:
		"""Missing amount should raise NormalizationError."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-003"},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "EUR"},
			"description": "Test",
		}
		with pytest.raises(NormalizationError, match="amount"):
			conn.normalize_transaction(raw)

	def test_normalize_missing_currency(self) -> None:
		"""Missing currency should raise NormalizationError."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-004"},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"amount": 100.00},
			"description": "Test",
		}
		with pytest.raises(NormalizationError, match="currency"):
			conn.normalize_transaction(raw)

	def test_normalize_missing_booking_date(self) -> None:
		"""Missing bookingDate should raise NormalizationError."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-005"},
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "EUR", "amount": 100.00},
			"description": "Test",
		}
		with pytest.raises(NormalizationError, match="bookingDate"):
			conn.normalize_transaction(raw)

	def test_normalize_without_counterparty(self) -> None:
		"""Missing counterparty should not raise — fields should be None."""
		conn = _make_connector()
		raw = {
			"references": {"referenceId": "ref-006"},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "EUR", "amount": 100.00},
			"description": "No counterparty",
			"type": "TRANSFER",
		}
		txn = conn.normalize_transaction(raw)
		assert txn["bank_party_name"] is None
		assert txn["bank_party_account_number"] is None

	def test_normalize_with_reference_number(self) -> None:
		"""paymentOrderId should be used as reference_number."""
		conn = _make_connector()
		raw = {
			"references": {
				"referenceId": "ref-007",
				"paymentOrderId": "PO-12345",
			},
			"bookingDate": "15/01/2026",
			"creditDebitIndicator": "CREDIT",
			"transactionAmount": {"currency": "EUR", "amount": 100.00},
			"description": "With ref",
			"type": "TRANSFER",
		}
		txn = conn.normalize_transaction(raw)
		assert txn["reference_number"] == "PO-12345"


# ---------------------------------------------------------------------------
# Date format helper
# ---------------------------------------------------------------------------


class TestEurobankDateFormat:
	"""Date format conversion tests."""

	def test_date_to_eurobank_format_start(self) -> None:
		"""YYYY-MM-DD should convert to yyyyMMdd0000."""
		result = EurobankConnector._date_to_eurobank_format("2026-01-01")
		assert result == "202601010000"

	def test_date_to_eurobank_format_end(self) -> None:
		"""YYYY-MM-DD should convert to yyyyMMdd2359 at end of day."""
		result = EurobankConnector._date_to_eurobank_format("2026-12-31", end_of_day=True)
		assert result == "202612312359"

	def test_date_to_eurobank_format_no_dashes(self) -> None:
		"""Date without dashes should still work."""
		result = EurobankConnector._date_to_eurobank_format("20260615")
		assert result == "202606150000"
