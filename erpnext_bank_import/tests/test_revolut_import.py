"""Integration tests for the Revolut connector in the import pipeline.

These tests validate the full Revolut import flow:

1. Full pipeline — fetch → normalise → map → insert
2. Normalisation errors do not block the import (logged and skipped)
3. Pending transactions are filtered out before normalisation
4. Idempotency — re-running the same window skips all transactions
5. Mixed state pages — only completed/reverted transactions are imported

All Frappe-dependent code paths and HTTP calls are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.revolut import RevolutConnector
from erpnext_bank_import.tests.fixtures.revolut_fixtures import (
	MOCK_ACCOUNTS,
	MOCK_TRANSACTIONS_CARD_PAYMENT,
	MOCK_TRANSACTIONS_DECLINED,
	MOCK_TRANSACTIONS_FAILED,
	MOCK_TRANSACTIONS_PENDING,
	MOCK_TRANSACTIONS_TRANSFER,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

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


def _make_revolut_config() -> ConnectorConfig:
	"""Build a Revolut ConnectorConfig for test imports."""
	return ConnectorConfig(
		provider_name="revolut",
		api_base_url="https://sandbox-b2b.revolut.com/api/1.0",
		auth_method="oauth2",
		client_id="test-client-id-001",
		jwt_private_key=_TEST_PRIVATE_KEY,
		jwt_issuer="erpnext.example.com",
		authorize_url="/auth/authorize",
		token_url="/auth/token",
		scopes=["READ"],
		rate_limit_rps=10.0,
		timeout_seconds=5.0,
		extra={"sandbox": True},
	)


class _MockMapping:
	"""Simulates a BankConnectorAccountMapping child-table row."""

	def __init__(
		self,
		provider_account_id: str = "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
		bank_account: str = "BA-Revolut-GBP",
		is_enabled: bool = True,
		last_synced_at: str | None = None,
	):
		self.provider_account_id = provider_account_id
		self.bank_account = bank_account
		self.is_enabled = is_enabled
		self.last_synced_at = last_synced_at

	def db_set(self, field: str, value: str) -> None:
		setattr(self, field, value)


class _MockBankConnectorDoc:
	"""Simulates a Bank Connector DocType record."""

	def __init__(self, mappings: list[_MockMapping] | None = None):
		self.enabled = True
		self.account_mappings = mappings if mappings is not None else [_MockMapping()]


@pytest.fixture
def mock_revolut_import():
	"""Set up all mocks needed for a Revolut import test.

	Patches Frappe, the connector factory, and the Revolut _api_get method.
	"""

	# Build the get_doc side effect
	def _get_doc_side_effect(doctype: str, docname: str = ""):
		if doctype == "Bank Connector":
			return _MockBankConnectorDoc()
		doc = MagicMock()
		doc.name = "test-revolut-run-log-001"
		doc.insert = MagicMock()
		doc.submit = MagicMock()
		return doc

	patches = [
		patch(
			"erpnext_bank_import.services.import_service.get_connector_config",
			return_value=_make_revolut_config(),
		),
		patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=RevolutConnector(config=_make_revolut_config()),
		),
		patch("erpnext_bank_import.services.import_service.frappe.get_all", return_value=[]),
		patch(
			"erpnext_bank_import.services.import_service.frappe.get_cached_value",
			return_value="_Test Company",
		),
		patch("erpnext_bank_import.services.import_service.frappe.log_error"),
		patch(
			"erpnext_bank_import.services.import_service.frappe.get_doc",
			side_effect=_get_doc_side_effect,
		),
		patch(
			"erpnext_bank_import.services.import_service.frappe.utils.today",
			return_value="2026-07-10",
		),
		patch(
			"erpnext_bank_import.services.import_service.frappe.utils.nowdate",
			return_value="2026-07-10",
		),
		patch(
			"erpnext_bank_import.services.import_service.frappe.utils.add_days",
			return_value="2026-04-11",
		),
	]

	for p in patches:
		p.start()

	yield

	for p in patches:
		p.stop()


@pytest.fixture
def revolut_connector_with_mock_api():
	"""Return a RevolutConnector whose _api_get is pre-mocked."""
	connector = RevolutConnector(config=_make_revolut_config())
	connector.set_current_bank_account("BA-Revolut-GBP")
	return connector


# =========================================================================
# Tests
# =========================================================================


class TestRevolutImportPipeline:
	"""Full import pipeline with mocked Revolut API."""

	def test_full_import_pipeline(self, mock_revolut_import):
		"""Revolut transactions flow through fetch → normalise → map → insert."""
		# Patch the connector's _api_get to return Revolut fixture data
		import erpnext_bank_import.services.import_service as svc

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: (
				MOCK_ACCOUNTS
				if path == "/accounts"
				else [MOCK_TRANSACTIONS_TRANSFER, MOCK_TRANSACTIONS_CARD_PAYMENT]
			),
		):
			from erpnext_bank_import.services.import_service import import_transactions

			result = import_transactions("Revolut Sandbox")

		assert result["status"] == "success"
		assert len(result["results"]) == 1
		r = result["results"][0]
		assert r["created"] == 2
		assert r["skipped"] == 0
		assert r["error"] is None

	def test_normalization_error_does_not_block_import(self, mock_revolut_import):
		"""A malformed transaction is logged and skipped; import continues."""
		import erpnext_bank_import.services.import_service as svc

		# Include a transaction without legs (will fail normalization)
		bad_txn = {
			"id": "bad-txn-001",
			"type": "transfer",
			"state": "completed",
			"created_at": "2024-01-01T00:00:00Z",
		}

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: (
				MOCK_ACCOUNTS if path == "/accounts" else [bad_txn, MOCK_TRANSACTIONS_TRANSFER]
			),
		):
			from erpnext_bank_import.services.import_service import import_transactions

			result = import_transactions("Revolut Sandbox")

		assert result["status"] == "success"
		r = result["results"][0]
		assert r["created"] == 1  # only the valid transaction
		assert r["error"] is None

	def test_pending_transactions_not_imported(self, mock_revolut_import):
		"""Pending transactions are filtered out before normalization."""
		import erpnext_bank_import.services.import_service as svc

		txns = [MOCK_TRANSACTIONS_PENDING, MOCK_TRANSACTIONS_TRANSFER]

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: MOCK_ACCOUNTS if path == "/accounts" else txns,
		):
			from erpnext_bank_import.services.import_service import import_transactions

			result = import_transactions("Revolut Sandbox")

		assert result["status"] == "success"
		r = result["results"][0]
		assert r["created"] == 1  # only the completed transaction
		assert r["skipped"] == 0

	def test_declined_and_failed_not_imported(self, mock_revolut_import):
		"""Declined and failed transactions are filtered out."""
		import erpnext_bank_import.services.import_service as svc

		txns = [
			MOCK_TRANSACTIONS_DECLINED,
			MOCK_TRANSACTIONS_FAILED,
			MOCK_TRANSACTIONS_TRANSFER,
		]

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: MOCK_ACCOUNTS if path == "/accounts" else txns,
		):
			from erpnext_bank_import.services.import_service import import_transactions

			result = import_transactions("Revolut Sandbox")

		assert result["status"] == "success"
		r = result["results"][0]
		assert r["created"] == 1  # only transfer
		assert r["skipped"] == 0

	def test_dedup_across_runs(self, mock_revolut_import):
		"""Second import with same data skips all previously created transactions."""
		import erpnext_bank_import.services.import_service as svc

		ids = [
			MOCK_TRANSACTIONS_TRANSFER["id"],
			MOCK_TRANSACTIONS_CARD_PAYMENT["id"],
		]

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: (
				MOCK_ACCOUNTS
				if path == "/accounts"
				else [MOCK_TRANSACTIONS_TRANSFER, MOCK_TRANSACTIONS_CARD_PAYMENT]
			),
		):
			from erpnext_bank_import.services.import_service import import_transactions

			# First run: all created
			result1 = import_transactions("Revolut Sandbox")
			assert result1["results"][0]["created"] == 2

		# Second run: simulate all IDs already exist
		svc.frappe.get_all.return_value = ids

		with patch.object(
			svc.get_connector(provider_name="revolut"),
			"_api_get",
			side_effect=lambda path, params=None: (
				MOCK_ACCOUNTS
				if path == "/accounts"
				else [MOCK_TRANSACTIONS_TRANSFER, MOCK_TRANSACTIONS_CARD_PAYMENT]
			),
		):
			result2 = import_transactions("Revolut Sandbox")

		assert result2["status"] == "success"
		r = result2["results"][0]
		assert r["created"] == 0
		assert r["skipped"] == 2
