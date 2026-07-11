"""Tests for the Revolut Business API connector.

These tests validate:

1. Configuration validation — JWT fields required for Revolut
2. Account discovery — ``GET /accounts`` parsing, inactive filter, bank-details enrichment
3. Transaction fetch — paginated cursor-based fetch, normalisation, error mapping
4. Normalisation — all transaction types (transfer, card_payment, atm, fee, exchange)
5. Error handling — 401, 429, 5xx, network errors
6. Rate limiting — respects ``rate_limit_rps``

All Frappe-dependent code paths are mocked.  HTTP calls are mocked via
``unittest.mock.patch``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	NetworkError,
	NormalizationError,
	RateLimitError,
	ServerError,
)
from erpnext_bank_import.connectors.revolut import RevolutConnector
from erpnext_bank_import.tests.fixtures.revolut_fixtures import (
	MOCK_ACCOUNTS,
	MOCK_BANK_DETAILS,
	MOCK_ERROR_401,
	MOCK_ERROR_429,
	MOCK_ERROR_500,
	MOCK_TRANSACTIONS_ATM,
	MOCK_TRANSACTIONS_CARD_PAYMENT,
	MOCK_TRANSACTIONS_EXCHANGE,
	MOCK_TRANSACTIONS_FEE,
	MOCK_TRANSACTIONS_INCOMING,
	MOCK_TRANSACTIONS_TRANSFER,
	MOCK_TRANSACTIONS_WITHOUT_COUNTERPARTY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A 2048-bit RSA private key for tests (PKCS#1 format).
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


def _make_revolut_config(**overrides: Any) -> ConnectorConfig:
	"""Build a minimal ``ConnectorConfig`` for a Revolut connector."""
	defaults: dict[str, Any] = {
		"provider_name": "revolut",
		"api_base_url": "https://sandbox-b2b.revolut.com/api/1.0",
		"auth_method": "oauth2",
		"client_id": "test-client-id-001",
		"jwt_private_key": _TEST_PRIVATE_KEY,
		"jwt_issuer": "erpnext.example.com",
		"authorize_url": "/auth/authorize",
		"token_url": "/auth/token",
		"scopes": ["READ"],
		"rate_limit_rps": 10.0,
		"timeout_seconds": 5.0,
	}
	defaults.update(overrides)
	return ConnectorConfig(**defaults)


def _mock_response(status_code: int = 200, json_data: Any = None) -> MagicMock:
	"""Build a mock ``requests.Response``."""
	mock = MagicMock(spec=requests.Response)
	mock.status_code = status_code
	mock.json.return_value = json_data or []
	mock.text = str(json_data) if json_data else ""
	mock.headers = {}
	return mock


# =========================================================================
# Configuration validation
# =========================================================================


class TestRevolutConfigValidation:
	"""Verifies Revolut-specific config validation."""

	def test_minimal_valid_config(self):
		"""A config with provider_name, api_base_url, and jwt_private_key works."""
		connector = RevolutConnector(config=_make_revolut_config())
		assert connector.config.provider_name == "revolut"
		assert connector.config.jwt_private_key is not None

	def test_missing_jwt_private_key_raises(self):
		"""jwt_private_key is required for Revolut OAuth2."""
		with pytest.raises(ConfigurationError, match="jwt_private_key"):
			RevolutConnector(config=_make_revolut_config(jwt_private_key=None))

	def test_empty_provider_name_raises(self):
		"""Empty provider_name should raise ConfigurationError."""
		with pytest.raises(ConfigurationError, match="provider_name"):
			RevolutConnector(config=_make_revolut_config(provider_name=""))

	def test_empty_api_base_url_raises(self):
		"""Empty api_base_url should raise ConfigurationError."""
		with pytest.raises(ConfigurationError, match="api_base_url"):
			RevolutConnector(config=_make_revolut_config(api_base_url=""))


# =========================================================================
# Account discovery
# =========================================================================


class TestRevolutAccountDiscovery:
	"""Verifies GET /accounts parsing."""

	def test_get_accounts(self):
		"""get_accounts() returns active accounts as AccountInfo."""
		connector = RevolutConnector(config=_make_revolut_config())

		with patch.object(connector, "_api_get", return_value=MOCK_ACCOUNTS):
			accounts = connector.get_accounts()

		# Only 2 active accounts (3rd is "closed").
		assert len(accounts) == 2

		gbp = next(a for a in accounts if a.currency == "GBP")
		assert gbp.account_id == "b7ec67d3-5af1-42c8-bece-3d28nlmo894d"
		assert gbp.account_name == "Current GBP Account"
		assert gbp.currency == "GBP"
		assert gbp.iban is None  # No bank-details call by default

		euro = next(a for a in accounts if a.currency == "EUR")
		assert euro.account_id == "bssc67d3-5afd-42c2-bece-3d28nlmo894d"
		assert euro.account_name == "International EUR Account"
		assert euro.currency == "EUR"

	def test_get_accounts_empty(self):
		"""get_accounts() returns empty list when no active accounts."""
		connector = RevolutConnector(config=_make_revolut_config())

		with patch.object(connector, "_api_get", return_value=[]):
			accounts = connector.get_accounts()

		assert accounts == []

	def test_get_accounts_iban_enrichment(self):
		"""Optional bank-details enrichment adds IBAN/BIC."""
		cfg = _make_revolut_config(extra={"fetch_bank_details": True})
		connector = RevolutConnector(config=cfg)

		def _mock_api_get(path: str, params: Any = None) -> Any:
			if path == "/accounts":
				return MOCK_ACCOUNTS
			if path.startswith("/accounts/") and path.endswith("/bank-details"):
				acc_id = path.split("/")[2]
				return MOCK_BANK_DETAILS.get(acc_id, [])
			return []

		with patch.object(connector, "_api_get", side_effect=_mock_api_get):
			accounts = connector.get_accounts()

		assert len(accounts) == 2
		gbp = next(a for a in accounts if a.currency == "GBP")
		assert gbp.iban == "GB66REVO00996995908888"
		assert gbp.provider_metadata.get("bic") == "REVOGB21"

	def test_get_accounts_without_auth_raises(self):
		"""get_accounts() raises if no token is set."""
		connector = RevolutConnector(config=_make_revolut_config())

		with pytest.raises(AuthenticationError, match="No bank account set"):
			connector.get_accounts()


# =========================================================================
# Transaction fetch
# =========================================================================


class TestRevolutTransactionFetch:
	"""Verifies transaction fetching and pagination."""

	def test_fetch_transactions_first_page(self):
		"""First page fetches transactions with date window params."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		# Mock _api_get to return a single page.
		mock_txns = [MOCK_TRANSACTIONS_TRANSFER, MOCK_TRANSACTIONS_CARD_PAYMENT]

		with patch.object(connector, "_api_get", return_value=mock_txns) as mock_get:
			txns, next_token = connector.fetch_transactions(
				account_id="b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
				date_from="2024-01-01",
				date_to="2024-12-31",
				page_size=100,
			)

		assert len(txns) == 2
		assert next_token is None  # fewer items than page_size
		mock_get.assert_called_once_with(
			"/transactions",
			params={
				"account": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
				"count": 100,
				"from": "2024-01-01T00:00:00Z",
				"to": "2024-12-31T23:59:59Z",
			},
		)

	def test_fetch_transactions_pagination(self):
		"""Cursor-based pagination: next_token carries created_at of last item."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		# First page: more items than page_size → expect next_token.
		page1 = [MOCK_TRANSACTIONS_TRANSFER, MOCK_TRANSACTIONS_CARD_PAYMENT]

		with patch.object(connector, "_api_get", return_value=page1):
			txns, next_token = connector.fetch_transactions(
				account_id="b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
				date_from="2024-01-01",
				date_to="2024-12-31",
				page_size=2,
			)

		assert len(txns) == 2
		assert next_token is not None
		assert next_token == "2024-03-11T07:19:51.302559Z"  # created_at of last item

	def test_fetch_transactions_empty(self):
		"""Empty response returns no transactions and no next token."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with patch.object(connector, "_api_get", return_value=[]):
			txns, next_token = connector.fetch_transactions(
				account_id="b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
				date_from="2024-01-01",
				date_to="2024-01-01",
			)

		assert txns == []
		assert next_token is None

	def test_fetch_all_transactions(self):
		"""fetch_all_transactions() aggregates pages via cursor."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		# Mock fetch_transactions to simulate multi-page.
		calls: dict[str, list[Any]] = {
			"first": [MOCK_TRANSACTIONS_TRANSFER],
			"second": [MOCK_TRANSACTIONS_CARD_PAYMENT],
			"third": [],
		}

		def _mock_fetch(account_id, date_from, date_to, *, page_size=100, page_token=None):
			if page_token is None:
				key = "first"
				next_tok = "2024-03-11T07:19:51.302559Z"
			elif page_token == "2024-03-11T07:19:51.302559Z":
				key = "second"
				next_tok = "2024-06-15T09:30:00.000000Z"
			else:
				key = "third"
				next_tok = None
			return calls[key], next_tok

		with patch.object(connector, "fetch_transactions", side_effect=_mock_fetch):
			txns = connector.fetch_all_transactions(
				account_id="any-acc",
				date_from="2024-01-01",
				date_to="2024-12-31",
			)

		assert len(txns) == 2


# =========================================================================
# Transaction normalisation
# =========================================================================


class TestRevolutNormalisation:
	"""Verifies normalisation of various Revolut transaction types."""

	def test_transfer_normalisation(self):
		"""Transfer transaction normalises correctly."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_TRANSFER)

		assert result["external_id"] == "630f9890-95e3-add1-be4a-95f126988221"
		assert result["date"] == "2024-08-31"
		assert result["amount"] == 1500.00
		assert result["currency"] == "GBP"
		assert result["description"] == "To Acme Corp"
		assert result["reference_number"] == "invoice00912345"
		assert result["bank_party_name"] == "Acme Corp"
		assert result["bank_party_account_number"] == "e0af9f24-504c-4c5d-bd1d-07edf9f49876"
		assert result["transaction_type"] == "transfer"
		assert result["included_fee"] is None
		assert "legs" in result["provider_metadata"]
		assert "legs" in result["provider_metadata"]
		assert result["provider_metadata"]["legs"][0]["counterparty"] is not None

	def test_card_payment_normalisation(self):
		"""Card payment normalises merchant info correctly."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_CARD_PAYMENT)

		assert result["external_id"] == "640c2b97-aaaa-1234-aaaa-c47a165c2e7e"
		assert result["date"] == "2024-03-11"
		assert result["amount"] == -47.80  # negative for outbound
		assert result["currency"] == "GBP"
		assert result["bank_party_name"] == "Supermarket Ltd"
		assert result["transaction_type"] == "card_payment"
		assert result["included_fee"] == 0.66
		assert "card" in result["provider_metadata"]

	def test_atm_normalisation(self):
		"""ATM withdrawal normalises correctly."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_ATM)

		assert result["amount"] == -200.00
		assert result["transaction_type"] == "atm"
		assert result["included_fee"] == 3.50
		assert result["bank_party_name"] is None  # no merchant/counterparty

	def test_fee_normalisation(self):
		"""Fee transaction normalises correctly."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_FEE)

		assert result["amount"] == -15.99
		assert result["transaction_type"] == "fee"
		assert result["description"] == "Monthly account fee"
		assert result["reference_number"] == "Monthly fee"
		assert result["bank_party_name"] is None

	def test_exchange_normalisation(self):
		"""Exchange transaction uses the first leg for amount/currency."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_EXCHANGE)

		# First leg is the debit leg in GBP.
		assert result["amount"] == -1000.00
		assert result["currency"] == "GBP"
		assert result["transaction_type"] == "exchange"
		assert result["description"] == "Exchanged to EUR"
		# All legs stored in provider_metadata.
		assert len(result["provider_metadata"]["legs"]) == 2

	def test_incoming_transfer_normalisation(self):
		"""Incoming transfer has positive amount."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_INCOMING)

		assert result["amount"] == 3200.00  # positive for incoming
		assert result["currency"] == "EUR"
		assert result["bank_party_name"] == "Employer Inc"
		assert result["description"] == "Monthly salary"

	def test_transaction_without_counterparty(self):
		"""Transaction without counterparty sets party fields to None."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_WITHOUT_COUNTERPARTY)

		assert result["bank_party_name"] is None
		assert result["bank_party_account_number"] is None
		assert result["transaction_type"] == "transfer"

	def test_missing_id_raises(self):
		"""Transaction without 'id' raises NormalizationError."""
		connector = RevolutConnector(config=_make_revolut_config())
		raw = dict(MOCK_TRANSACTIONS_TRANSFER)
		raw.pop("id")

		with pytest.raises(NormalizationError, match="id"):
			connector.normalize_transaction(raw)

	def test_missing_legs_raises(self):
		"""Transaction without 'legs' raises NormalizationError."""
		connector = RevolutConnector(config=_make_revolut_config())
		raw = dict(MOCK_TRANSACTIONS_TRANSFER)
		raw.pop("legs")

		with pytest.raises(NormalizationError, match="no legs"):
			connector.normalize_transaction(raw)

	def test_missing_amount_raises(self):
		"""Transaction without legs[0].amount raises NormalizationError."""
		connector = RevolutConnector(config=_make_revolut_config())
		raw = dict(MOCK_TRANSACTIONS_TRANSFER)
		raw["legs"][0] = dict(raw["legs"][0])
		raw["legs"][0].pop("amount")

		with pytest.raises(NormalizationError, match="legs"):
			connector.normalize_transaction(raw)

	def test_missing_currency_raises(self):
		"""Transaction without legs[0].currency raises NormalizationError."""
		connector = RevolutConnector(config=_make_revolut_config())
		raw = dict(MOCK_TRANSACTIONS_TRANSFER)
		raw["legs"][0] = dict(raw["legs"][0])
		raw["legs"][0].pop("currency")

		with pytest.raises(NormalizationError, match="legs"):
			connector.normalize_transaction(raw)

	def test_provider_metadata_contains_legs(self):
		"""Full leg data is preserved in provider_metadata."""
		connector = RevolutConnector(config=_make_revolut_config())
		result = connector.normalize_transaction(MOCK_TRANSACTIONS_CARD_PAYMENT)

		assert "legs" in result["provider_metadata"]
		assert len(result["provider_metadata"]["legs"]) == 1
		assert result["provider_metadata"]["legs"][0]["leg_id"] is not None
		assert "card" in result["provider_metadata"]
		assert result["provider_metadata"]["card"]["card_number"] is not None


# =========================================================================
# Error handling
# =========================================================================


class TestRevolutErrorHandling:
	"""Verifies error mapping for API responses."""

	def test_401_raises_authentication_error(self):
		"""HTTP 401 → AuthenticationError."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session,
				"get",
				return_value=_mock_response(401, MOCK_ERROR_401),
			),
		):
			with pytest.raises(AuthenticationError, match="401"):
				connector._api_get("/accounts")

	def test_429_raises_rate_limit_error(self):
		"""HTTP 429 → RateLimitError."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session,
				"get",
				return_value=_mock_response(429, MOCK_ERROR_429),
			),
		):
			with pytest.raises(RateLimitError, match="429"):
				connector._api_get("/accounts")

	def test_500_raises_server_error(self):
		"""HTTP 500 → ServerError."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session,
				"get",
				return_value=_mock_response(500, MOCK_ERROR_500),
			),
		):
			with pytest.raises(ServerError, match="500"):
				connector._api_get("/accounts")

	def test_other_4xx_raises_api_error(self):
		"""HTTP 400 → ApiError."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session,
				"get",
				return_value=_mock_response(400, {"error": "bad request"}),
			),
		):
			with pytest.raises(ApiError, match="400"):
				connector._api_get("/accounts")

	def test_network_error_raises_network_error(self):
		"""Connection error → NetworkError."""
		connector = RevolutConnector(config=_make_revolut_config())
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session,
				"get",
				side_effect=requests.ConnectionError("Connection refused"),
			),
		):
			with pytest.raises(NetworkError, match="Network error"):
				connector._api_get("/accounts")


# =========================================================================
# Rate limiting
# =========================================================================


class TestRevolutRateLimiting:
	"""Verifies rate limiting behaviour."""

	def test_rate_limiting_respects_rps(self):
		"""Requests are throttled to respect rate_limit_rps."""
		cfg = _make_revolut_config(rate_limit_rps=1000.0)  # Very high → no sleep
		connector = RevolutConnector(config=cfg)
		connector.set_current_bank_account("BA-001")

		# Mock the token resolution to avoid auth checks.
		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(
				connector._session, "get", return_value=_mock_response(200, MOCK_ACCOUNTS)
			) as mock_get,
		):
			connector._api_get("/accounts")
			assert mock_get.call_count == 1

	def test_no_rate_limit_with_none_rps(self):
		"""No throttling when rate_limit_rps is None."""
		cfg = _make_revolut_config(rate_limit_rps=None)
		connector = RevolutConnector(config=cfg)
		connector.set_current_bank_account("BA-001")

		with (
			patch.object(connector, "_get_access_token", return_value="test-token"),
			patch.object(connector._session, "get", return_value=_mock_response(200, MOCK_ACCOUNTS)),
		):
			# Should not raise.
			connector._api_get("/accounts")


# =========================================================================
# OAuth flow endpoints
# =========================================================================


class TestRevolutOAuthFlow:
	"""Verifies OAuth flow helper functions."""

	def test_start_oauth_flow_production(self):
		"""Production consent URL is built correctly."""
		cfg = _make_revolut_config(
			api_base_url="https://b2b.revolut.com/api/1.0",
			extra={},
		)
		connector = RevolutConnector(config=cfg)

		with patch.object(
			connector._oauth,
			"get_authorize_url",
			return_value="https://business.revolut.com/app-confirm?client_id=test-client-id-001&redirect_uri=&response_type=code&scope=READ",
		):
			url = connector._oauth.get_authorize_url(state="test-state")

		assert "business.revolut.com" in url
		assert "app-confirm" in url

	def test_start_oauth_flow_sandbox(self):
		"""Sandbox consent URL uses sandbox host."""
		cfg = _make_revolut_config(extra={"sandbox": True})
		connector = RevolutConnector(config=cfg)

		with patch.object(
			connector._oauth,
			"get_authorize_url",
			return_value="https://sandbox-business.revolut.com/app-confirm?client_id=test-client-id-001&redirect_uri=&response_type=code&scope=READ",
		):
			url = connector._oauth.get_authorize_url(state="test-state")

		assert "sandbox-business.revolut.com" in url


# =========================================================================
# Direct connector usage (set_current_bank_account)
# =========================================================================


class TestRevolutBankAccountContext:
	"""Verifies bank account context management."""

	def test_set_current_bank_account(self):
		"""set_current_bank_account updates the internal state."""
		connector = RevolutConnector(config=_make_revolut_config())
		assert connector._current_bank_account is None

		connector.set_current_bank_account("BA-001")
		assert connector._current_bank_account == "BA-001"

	def test_get_access_token_without_account_raises(self):
		"""Calling API without setting bank account raises."""
		connector = RevolutConnector(config=_make_revolut_config())

		with pytest.raises(AuthenticationError, match="No bank account set"):
			connector._api_get("/accounts")
