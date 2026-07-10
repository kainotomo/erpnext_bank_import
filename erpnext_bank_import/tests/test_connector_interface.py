"""Tests for the shared connector interface and mock provider.

These tests validate:

1. The ``BankConnector`` ABC contract via the ``MockProvider``.
2. The exception hierarchy.
3. The configuration data models.
4. The full round-trip: mock fetch → normalize → apply_mapping → no prohibited fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from erpnext_bank_import.connectors import PROVIDER_REGISTRY, get_connector
from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	ConnectorError,
	NormalizationError,
	RateLimitError,
)
from erpnext_bank_import.connectors.mock_provider import MockProvider
from erpnext_bank_import.schema.reconciliation import assert_no_prohibited_fields
from erpnext_bank_import.schema.transaction import NormalizedTransaction, apply_mapping

# =========================================================================
# Exception hierarchy
# =========================================================================


class TestConnectorExceptions:
	"""Verifies exception types and inheritance."""

	def test_all_exceptions_inherit_from_connector_error(self):
		"""Every connector exception should be catchable as ConnectorError."""
		exceptions = [
			AuthenticationError(),
			RateLimitError(),
			RateLimitError(retry_after=5.0),
			ApiError(status_code=500, response_body="Internal Server Error"),
			ConfigurationError(),
			NormalizationError(),
		]
		for exc in exceptions:
			assert isinstance(exc, ConnectorError), f"{type(exc).__name__} is not a ConnectorError"

	def test_rate_limit_error_retry_after(self):
		"""RateLimitError should carry the optional retry_after attribute."""
		exc = RateLimitError(retry_after=5.0)
		assert exc.retry_after == 5.0

		exc_default = RateLimitError()
		assert exc_default.retry_after is None

	def test_api_error_attributes(self):
		"""ApiError should carry status_code and response_body."""
		exc = ApiError("Bad request", status_code=400, response_body='{"error":"invalid"}')
		assert exc.status_code == 400
		assert exc.response_body == '{"error":"invalid"}'


# =========================================================================
# Configuration models
# =========================================================================


class TestConnectorConfig:
	"""Verifies configuration dataclass."""

	def test_minimal_config(self):
		"""Minimal config requires only provider_name and api_base_url."""
		cfg = ConnectorConfig(provider_name="test", api_base_url="https://api.example.com")
		assert cfg.provider_name == "test"
		assert cfg.api_base_url == "https://api.example.com"
		assert cfg.auth_method == "oauth2"  # default
		assert cfg.timeout_seconds == 30.0  # default

	def test_full_config(self):
		"""All fields can be set explicitly."""
		cfg = ConnectorConfig(
			provider_name="revolut",
			api_base_url="https://api.revolut.com",
			auth_method="api_key",
			client_id="client-123",
			token_url="/auth/token",
			rate_limit_rps=10.0,
			timeout_seconds=60.0,
			extra={"sandbox": True},
		)
		assert cfg.client_id == "client-123"
		assert cfg.token_url == "/auth/token"
		assert cfg.rate_limit_rps == 10.0
		assert cfg.timeout_seconds == 60.0
		assert cfg.extra == {"sandbox": True}

	def test_account_info_dataclass(self):
		"""AccountInfo should store and expose its fields."""
		info = AccountInfo(
			account_id="acc-001",
			account_name="Business EUR",
			currency="EUR",
			iban="IE12ABCD12345678901234",
		)
		assert info.account_id == "acc-001"
		assert info.account_name == "Business EUR"
		assert info.currency == "EUR"
		assert info.iban == "IE12ABCD12345678901234"
		assert info.account_number is None
		assert info.provider_metadata == {}


# =========================================================================
# Abstract base connector validation
# =========================================================================


class TestBankConnectorValidation:
	"""Verifies base class config validation."""

	def test_empty_provider_name_raises(self):
		"""Empty provider_name should raise ConfigurationError."""
		with pytest.raises(ConfigurationError, match="provider_name"):
			MockProvider(provider_name="", api_base_url="https://example.com")

	def test_empty_api_base_url_raises(self):
		"""Empty api_base_url should raise ConfigurationError."""
		with pytest.raises(ConfigurationError, match="api_base_url"):
			MockProvider(provider_name="mock", api_base_url="")


# =========================================================================
# Mock provider — auth lifecycle
# =========================================================================


class TestMockProviderAuth:
	"""Verifies authentication lifecycle on MockProvider."""

	def test_auth_success(self):
		"""authenticate() should succeed and is_authenticated() returns True."""
		provider = MockProvider()
		assert not provider.is_authenticated()
		provider.authenticate()
		assert provider.is_authenticated()

	def test_auth_failure(self):
		"""authenticate() should raise AuthenticationError when configured to fail."""
		provider = MockProvider(auth_should_fail=True)
		with pytest.raises(AuthenticationError, match="auth_should_fail"):
			provider.authenticate()
		assert not provider.is_authenticated()

	def test_refresh_token_success(self):
		"""refresh_token() should succeed by default."""
		provider = MockProvider()
		provider.refresh_token()
		assert provider.is_authenticated()

	def test_refresh_token_failure(self):
		"""refresh_token() should raise AuthenticationError when configured to fail."""
		provider = MockProvider(auth_should_fail=True)
		with pytest.raises(AuthenticationError, match="auth_should_fail"):
			provider.refresh_token()


# =========================================================================
# Mock provider — account discovery
# =========================================================================


class TestMockProviderAccounts:
	"""Verifies account discovery on MockProvider."""

	def test_get_accounts_requires_auth(self):
		"""get_accounts() should raise AuthenticationError if not authenticated."""
		provider = MockProvider()
		with pytest.raises(AuthenticationError, match="Not authenticated"):
			provider.get_accounts()

	def test_get_accounts_default_count(self):
		"""Default MockProvider should return 3 accounts."""
		provider = MockProvider()
		provider.authenticate()
		accounts = provider.get_accounts()
		assert len(accounts) == 3

		for acc in accounts:
			assert isinstance(acc, AccountInfo)
			assert acc.account_id
			assert acc.account_name
			assert acc.currency

	def test_get_accounts_custom_count(self):
		"""MockProvider should respect account_count parameter."""
		provider = MockProvider(account_count=2)
		provider.authenticate()
		accounts = provider.get_accounts()
		assert len(accounts) == 2

	def test_accounts_have_unique_ids(self):
		"""Returned accounts should have unique account_ids."""
		provider = MockProvider()
		provider.authenticate()
		accounts = provider.get_accounts()
		ids = [a.account_id for a in accounts]
		assert len(ids) == len(set(ids))


# =========================================================================
# Mock provider — transaction fetch
# =========================================================================


class TestMockProviderTransactions:
	"""Verifies transaction fetching on MockProvider."""

	def test_fetch_requires_auth(self):
		"""fetch_transactions() should raise AuthenticationError if not authenticated."""
		provider = MockProvider()
		with pytest.raises(AuthenticationError, match="Not authenticated"):
			provider.fetch_transactions("mock-acc-001", "2026-01-01", "2026-06-30")

	def test_fetch_first_page(self):
		"""fetch_transactions() with no page_token returns the first page."""
		provider = MockProvider()
		provider.authenticate()
		txns, next_token = provider.fetch_transactions("mock-acc-001", "2026-01-01", "2026-06-30")
		assert len(txns) == provider._transactions_per_page
		assert next_token is not None  # there is a next page

		for txn in txns:
			assert isinstance(txn, dict)
			assert "external_id" in txn
			assert "amount" in txn

	def test_fetch_all_pages(self):
		"""fetch_all_transactions() should return all transactions across pages."""
		provider = MockProvider()
		provider.authenticate()
		txns = provider.fetch_all_transactions("mock-acc-001", "2026-01-01", "2026-06-30")
		expected_total = provider._total_pages * provider._transactions_per_page
		assert len(txns) == expected_total

	def test_fetch_unique_ids(self):
		"""Transaction external_ids should be unique across all pages."""
		provider = MockProvider()
		provider.authenticate()
		txns = provider.fetch_all_transactions("mock-acc-001", "2026-01-01", "2026-06-30")
		ids = [t["external_id"] for t in txns]
		assert len(ids) == len(set(ids))

	def test_fetch_beyond_max_pages(self):
		"""fetch_transactions() beyond total_pages should return empty."""
		provider = MockProvider()
		provider.authenticate()
		txns, next_token = provider.fetch_transactions(
			"mock-acc-001",
			"2026-01-01",
			"2026-06-30",
			page_token=str(provider._total_pages + 1),
		)
		assert len(txns) == 0
		assert next_token is None

	def test_fetch_respects_page_size(self):
		"""fetch_transactions() should respect the page_size parameter."""
		provider = MockProvider(transactions_per_page=5)
		provider.authenticate()
		txns = provider.fetch_transactions("mock-acc-001", "2026-01-01", "2026-06-30", page_size=5)[0]
		assert len(txns) == 5


# =========================================================================
# Mock provider — normalization
# =========================================================================


class TestMockProviderNormalization:
	"""Verifies normalization on MockProvider."""

	def test_normalize_valid_transaction(self):
		"""Normalizing a valid transaction should succeed."""
		provider = MockProvider()
		raw = {
			"external_id": "txn-001",
			"date": "2026-06-15",
			"amount": 1500.00,
			"currency": "EUR",
			"description": "Invoice payment",
		}
		result = provider.normalize_transaction(raw)
		assert isinstance(result, dict)
		assert result["external_id"] == "txn-001"
		assert result["amount"] == 1500.00

	def test_normalize_with_all_fields(self):
		"""Normalizing with all optional fields should include them."""
		provider = MockProvider()
		raw = {
			"external_id": "txn-002",
			"date": "2026-06-15",
			"amount": 250.00,
			"currency": "USD",
			"description": "Payment",
			"reference_number": "REF-001",
			"bank_party_name": "Acme Corp",
			"bank_party_account_number": "12345678",
			"bank_party_iban": "US12ABCD12345678",
			"transaction_type": "TRANSFER",
			"included_fee": 2.50,
			"excluded_fee": None,
			"provider_metadata": {"key": "value"},
		}
		result = provider.normalize_transaction(raw)
		assert result["reference_number"] == "REF-001"
		assert result["bank_party_name"] == "Acme Corp"
		assert result["included_fee"] == 2.50
		assert result["excluded_fee"] is None
		assert result["provider_metadata"] == {"key": "value"}

	def test_normalize_missing_required_field(self):
		"""Normalizing with a missing required field should raise NormalizationError."""
		provider = MockProvider()
		raw = {
			"external_id": "txn-003",
			"date": "2026-06-15",
			# "amount" is missing
			"currency": "EUR",
			"description": "Broken",
		}
		with pytest.raises(NormalizationError, match="amount"):
			provider.normalize_transaction(raw)

	def test_normalize_invalid_amount_type(self):
		"""Normalizing with non-numeric amount should raise NormalizationError."""
		provider = MockProvider()
		raw = {
			"external_id": "txn-004",
			"date": "2026-06-15",
			"amount": "not-a-number",
			"currency": "EUR",
			"description": "Bad amount",
		}
		with pytest.raises(NormalizationError, match="numeric"):
			provider.normalize_transaction(raw)


# =========================================================================
# Round-trip: mock → normalize → apply_mapping → no prohibited fields
# =========================================================================


class TestNormalizationRoundTrip:
	"""End-to-end round-trip through the full pipeline."""

	@pytest.fixture
	def provider(self) -> MockProvider:
		p = MockProvider()
		p.authenticate()
		return p

	def test_round_trip_all_transactions(self, provider: MockProvider):
		"""All mock transactions should normalise and map without prohibited fields."""
		accounts = provider.get_accounts()
		for account in accounts:
			txns = provider.fetch_all_transactions(
				account_id=account.account_id,
				date_from="2026-01-01",
				date_to="2026-06-30",
			)
			for txn in txns:
				mapped = apply_mapping(txn)
				assert_no_prohibited_fields(mapped)
				# Basic sanity checks on mapped output
				assert "deposit" in mapped or "withdrawal" in mapped
				assert mapped.get("date")
				assert mapped.get("currency")

	def test_round_trip_deposit_withdrawal_distinct(self, provider: MockProvider):
		"""Deposits and withdrawals should be correctly separated."""
		accounts = provider.get_accounts()
		txns = provider.fetch_all_transactions(
			account_id=accounts[0].account_id,
			date_from="2026-01-01",
			date_to="2026-06-30",
		)
		for txn in txns:
			mapped = apply_mapping(txn)
			if txn["amount"] >= 0:
				assert mapped.get("deposit") == txn["amount"]
				assert "withdrawal" not in mapped
			else:
				assert mapped.get("withdrawal") == abs(txn["amount"])
				assert "deposit" not in mapped

	def test_all_normalized_transactions_are_valid_typeddicts(self, provider: MockProvider):
		"""Every NormalizedTransaction from mock should have correct types."""
		accounts = provider.get_accounts()
		txns = provider.fetch_all_transactions(
			account_id=accounts[0].account_id,
			date_from="2026-01-01",
			date_to="2026-06-30",
		)
		for txn in txns:
			assert isinstance(txn["external_id"], str)
			assert isinstance(txn["date"], str)
			assert isinstance(txn["amount"], float)
			assert isinstance(txn["currency"], str)
			assert isinstance(txn["description"], str)
			# Optional fields should be str, float, or None
			for opt_field in [
				"reference_number",
				"bank_party_name",
				"bank_party_account_number",
				"bank_party_iban",
				"transaction_type",
			]:
				assert txn[opt_field] is None or isinstance(txn[opt_field], str)
			for fee_field in ["included_fee", "excluded_fee"]:
				assert txn[fee_field] is None or isinstance(txn[fee_field], float)


# =========================================================================
# Provider registry and factory
# =========================================================================


class TestProviderRegistry:
	"""Verifies the PROVIDER_REGISTRY and get_connector factory."""

	def test_mock_is_registered(self):
		"""The mock provider should be registered."""
		assert "mock" in PROVIDER_REGISTRY
		assert PROVIDER_REGISTRY["mock"] is MockProvider

	def test_all_registered_are_bank_connector_subclasses(self):
		"""Every registered provider should be a BankConnector subclass."""
		for name, cls in PROVIDER_REGISTRY.items():
			assert issubclass(cls, BankConnector), f"{name} is not a BankConnector subclass"

	def test_get_connector_returns_instance(self):
		"""get_connector() should return an initialised connector instance."""
		conn = get_connector("mock")
		assert isinstance(conn, MockProvider)
		assert isinstance(conn, BankConnector)

	def test_get_connector_unknown(self):
		"""get_connector() should raise KeyError for unknown providers."""
		with pytest.raises(KeyError, match="unknown_provider"):
			get_connector("unknown_provider")

	def test_get_connector_passes_kwargs(self):
		"""get_connector() should forward kwargs to the connector constructor."""
		conn = get_connector("mock", auth_should_fail=True)
		assert conn._auth_should_fail is True

	def test_registry_is_extensible(self):
		"""The registry dict should accept new entries."""
		assert isinstance(PROVIDER_REGISTRY, dict)

		# Register a temporary provider
		class TempProvider(BankConnector):
			def authenticate(self): ...
			def is_authenticated(self):
				return True

			def refresh_token(self): ...
			def get_accounts(self):
				return []

			def fetch_transactions(self, account_id, date_from, date_to, *, page_size=100, page_token=None):
				return [], None

			def normalize_transaction(self, raw): ...

		PROVIDER_REGISTRY["temp"] = TempProvider
		assert "temp" in PROVIDER_REGISTRY
		conn = get_connector("temp", provider_name="temp", api_base_url="https://example.com")
		assert isinstance(conn, TempProvider)
		# Clean up
		del PROVIDER_REGISTRY["temp"]
