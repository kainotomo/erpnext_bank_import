"""Tests for the import orchestration service.

These tests validate:

1. Idempotent imports — existing transactions are skipped.
2. Error isolation — per-account failures don't abort the entire import.
3. ``import_all_enabled_connectors`` — aggregates multiple connectors.

All Frappe-dependent code paths are mocked to keep tests fast and
deterministic.

NOTE: Import of the service module is done inside test methods so that
pytest fixtures apply patches *before* function references are bound.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.mock_provider import MockProvider

# =========================================================================
# Mock helpers
# =========================================================================


class _MockMapping:
	"""Simulates a BankConnectorAccountMapping child-table row."""

	def __init__(
		self,
		provider_account_id: str = "mock-acc-001",
		bank_account: str = "BA-001",
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

	def __init__(self, enabled: bool = True, mappings: list[_MockMapping] | None = None):
		self.enabled = enabled
		self.account_mappings = mappings if mappings is not None else [_MockMapping()]


@pytest.fixture
def mock_all():
	"""Set up ALL Frappe mocks needed by the import service."""
	from unittest.mock import MagicMock

	from erpnext_bank_import.connectors.config import ConnectorConfig

	# Build the get_doc side effect before starting patches
	def _get_doc_side_effect(doctype: str, docname: str = ""):
		if doctype == "Bank Connector":
			return _MockBankConnectorDoc()
		doc = MagicMock()
		doc.insert = MagicMock()
		doc.submit = MagicMock()
		return doc

	patches = [
		patch(
			"erpnext_bank_import.services.import_service.get_connector_config",
			return_value=ConnectorConfig(provider_name="mock", api_base_url="https://api.example.com"),
		),
		patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=MockProvider(),
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
		patch("erpnext_bank_import.services.import_service.frappe.utils.today", return_value="2026-07-10"),
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


# =========================================================================
# Tests
# =========================================================================


class TestImportTransactions:
	"""Tests for the main ``import_transactions`` function."""

	def test_import_creates_new_transactions(self, mock_all):
		"""All transactions are new -> all created."""
		from erpnext_bank_import.services.import_service import import_transactions

		result = import_transactions("_Test Connector")
		assert result["status"] == "success"
		assert len(result["results"]) == 1
		r = result["results"][0]
		assert r["created"] > 0
		assert r["skipped"] == 0
		assert r["error"] is None

	def test_import_skips_existing(self, mock_all):
		"""Some transactions already exist -> some skipped."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		svc_frappe.get_all.return_value = ["mock-txn-1-1", "mock-txn-1-2"]
		result = import_transactions("_Test Connector")
		r = result["results"][0]
		# 30 total, 2 exist -> 28 created, 2 skipped
		assert r["created"] == 28, f"Expected 28 created, got {r['created']}"
		assert r["skipped"] == 2, f"Expected 2 skipped, got {r['skipped']}"
		assert r["error"] is None

	def test_import_all_duplicates_skipped(self, mock_all):
		"""All transactions already exist -> 0 created, all skipped."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		svc_frappe.get_all.return_value = [
			"mock-txn-1-1",
			"mock-txn-1-2",
			"mock-txn-1-3",
			"mock-txn-1-4",
			"mock-txn-1-5",
			"mock-txn-1-6",
			"mock-txn-1-7",
			"mock-txn-1-8",
			"mock-txn-1-9",
			"mock-txn-1-10",
			"mock-txn-2-1",
			"mock-txn-2-2",
			"mock-txn-2-3",
			"mock-txn-2-4",
			"mock-txn-2-5",
			"mock-txn-2-6",
			"mock-txn-2-7",
			"mock-txn-2-8",
			"mock-txn-2-9",
			"mock-txn-2-10",
			"mock-txn-3-1",
			"mock-txn-3-2",
			"mock-txn-3-3",
			"mock-txn-3-4",
			"mock-txn-3-5",
			"mock-txn-3-6",
			"mock-txn-3-7",
			"mock-txn-3-8",
			"mock-txn-3-9",
			"mock-txn-3-10",
		]
		result = import_transactions("_Test Connector")
		r = result["results"][0]
		assert r["created"] == 0
		assert r["skipped"] == 30

	def test_no_enabled_mappings_returns_error(self, mock_all):
		"""Connector with no enabled mappings should report error."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		def _no_mappings_doc(doctype: str, docname: str = ""):
			if doctype == "Bank Connector":
				return _MockBankConnectorDoc(mappings=[])
			doc = MagicMock()
			doc.insert = MagicMock()
			doc.submit = MagicMock()
			return doc

		svc_frappe.get_doc.side_effect = _no_mappings_doc
		result = import_transactions("_Test Connector")
		assert result["status"] == "error"

	def test_auth_error_isolated(self, mock_all):
		"""Authentication failure should return error status."""
		from erpnext_bank_import.services.import_service import import_transactions

		patch_connector = patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=MockProvider(auth_should_fail=True),
		)
		patch_connector.start()
		try:
			result = import_transactions("_Test Connector")
			assert result["status"] == "error"
			assert result["error"] is not None
		finally:
			patch_connector.stop()


class TestGetExistingTransactionIds:
	"""Tests for the dedup query helper."""

	def test_returns_set_of_existing_ids(self):
		"""Should return a set of transaction_ids that exist."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			return_value=["txn-001", "txn-003"],
		):
			result = _get_existing_transaction_ids("BA-001", ["txn-001", "txn-002", "txn-003"])
			assert result == {"txn-001", "txn-003"}

	def test_returns_empty_set_when_none_exist(self):
		"""If no transactions exist, return empty set."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			return_value=[],
		):
			result = _get_existing_transaction_ids("BA-001", ["txn-001", "txn-002"])
			assert result == set()

	def test_returns_empty_set_when_no_ids_provided(self):
		"""Empty input list should return empty set without querying DB."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
		) as m_get_all:
			result = _get_existing_transaction_ids("BA-001", [])
			assert result == set()
			m_get_all.assert_not_called()


class TestImportAllEnabledConnectors:
	"""Tests for the scheduled-job entry point."""

	def test_imports_all_connectors(self, mock_all):
		"""Should import transactions for every enabled connector."""
		from erpnext_bank_import.services.import_service import import_all_enabled_connectors

		patch_get_all = patch(
			"erpnext_bank_import.services.import_service.get_all_enabled_connectors",
			return_value=["Connector A", "Connector B"],
		)
		patch_get_all.start()
		try:
			summaries = import_all_enabled_connectors()
			assert len(summaries) == 2
			for s in summaries:
				assert s["status"] == "success"
		finally:
			patch_get_all.stop()

	def test_one_failure_does_not_abort_others(self, mock_all):
		"""If one connector fails, others should still be processed."""
		from erpnext_bank_import.services.import_service import import_all_enabled_connectors

		patch_get_all = patch(
			"erpnext_bank_import.services.import_service.get_all_enabled_connectors",
			return_value=["Failing", "Working"],
		)
		patch_get_all.start()
		patch_config = patch(
			"erpnext_bank_import.services.import_service.get_connector_config",
			side_effect=[Exception("Config load failed"), MagicMock()],
		)
		patch_config.start()
		patch_connector = patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=MockProvider(),
		)
		patch_connector.start()
		try:
			summaries = import_all_enabled_connectors()
			assert len(summaries) == 2
			assert summaries[0]["status"] == "error"
			assert summaries[1]["status"] == "success"
		finally:
			patch_get_all.stop()
			patch_config.stop()
			patch_connector.stop()
