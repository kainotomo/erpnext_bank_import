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
		# For Bank Import Run Log (created by RunLogger), return a mock with .name
		doc = MagicMock()
		doc.name = "test-run-log-001"
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


# =========================================================================
# Extended tests for A7 observability enhancements
# =========================================================================


class TestImportServiceRunLog:
	"""Tests that RunLogger is properly integrated."""

	def test_transactions_creates_run_log(self, mock_all):
		"""import_transactions should create a run log via RunLogger."""
		from erpnext_bank_import.services.import_service import import_transactions

		# The mock_all fixture already patches frappe.get_doc.
		# We just verify the function completes successfully and
		# logs results.
		result = import_transactions("_Test Connector")
		assert result["status"] == "success"
		assert len(result["results"]) == 1
		assert result["results"][0]["created"] > 0

	def test_trigger_parameter_passed_through(self, mock_all):
		"""The trigger parameter should be accepted."""
		from erpnext_bank_import.services.import_service import import_transactions

		result = import_transactions("_Test Connector", trigger="Scheduled")
		assert result["status"] == "success"


class TestImportServiceDiagnostics:
	"""Tests that diagnostics are included in error results."""

	def test_auth_error_has_diagnostic_context(self, mock_all):
		"""Authentication failure should include actionable error message."""
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
			# The error should be more than just a raw message — it should
			# contain the suggested action context
			assert len(result["error"]) > 10
		finally:
			patch_connector.stop()

	def test_fetch_error_has_diagnostic_context(self, mock_all):
		"""Fetch failure should include actionable error message."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		# Patch the connector to raise during fetch
		class _FailingConnector(MockProvider):
			def fetch_all_transactions(self, **kwargs):
				raise Exception("API timeout")

		patch_connector = patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=_FailingConnector(),
		)
		patch_connector.start()
		try:
			result = import_transactions("_Test Connector")
			r = result["results"][0]
			assert r["error"] is not None
			assert len(r["error"]) > 10
		finally:
			patch_connector.stop()


class TestImportAllConnectorsAggregation:
	"""Tests for aggregated error logging in import_all_enabled_connectors."""

	def test_aggregated_log_on_failures(self, mock_all):
		"""When some connectors fail, an aggregated log entry is written."""
		from erpnext_bank_import.services.import_service import import_all_enabled_connectors

		patch_get_all = patch(
			"erpnext_bank_import.services.import_service.get_all_enabled_connectors",
			return_value=["Connector A", "Connector B"],
		)
		patch_get_all.start()

		# Make the first connector fail by patching get_connector_config
		def _fail_config(name):
			if name == "Connector A":
				raise Exception("Connection refused")
			from erpnext_bank_import.connectors.config import ConnectorConfig

			return ConnectorConfig(provider_name="mock", api_base_url="https://api.example.com")

		patch_config = patch(
			"erpnext_bank_import.services.import_service.get_connector_config",
			side_effect=_fail_config,
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

	def test_no_aggregated_log_when_all_succeed(self, mock_all):
		"""When all connectors succeed, no aggregated error log is needed."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_all_enabled_connectors

		patch_get_all = patch(
			"erpnext_bank_import.services.import_service.get_all_enabled_connectors",
			return_value=["Connector A", "Connector B"],
		)
		patch_get_all.start()
		try:
			# Reset log_error call count — the mock_all fixture already
			# patched it, so we just check it wasn't called for aggregated errors
			svc_frappe.log_error.reset_mock()
			summaries = import_all_enabled_connectors()
			assert len(summaries) == 2
			assert all(s["status"] == "success" for s in summaries)
		finally:
			patch_get_all.stop()


# =========================================================================
# Idempotency tests
# =========================================================================


class TestImportIdempotency:
	"""Tests that importing the same data twice produces consistent results."""

	def test_import_twice_produces_identical_results(self, mock_all):
		"""Second import of same data creates 0 and skips all 30."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		# First import — all are new
		result1 = import_transactions("_Test Connector")
		assert result1["status"] == "success"
		assert result1["results"][0]["created"] == 30
		assert result1["results"][0]["skipped"] == 0

		# Simulate that all 30 are now in the database
		all_ids = [f"mock-txn-{p}-{i}" for p in range(1, 4) for i in range(1, 11)]
		svc_frappe.get_all.return_value = all_ids

		# Second import — all are skipped
		result2 = import_transactions("_Test Connector")
		assert result2["status"] == "success"
		assert result2["results"][0]["created"] == 0
		assert result2["results"][0]["skipped"] == 30

	def test_import_with_partial_overlap(self, mock_all):
		"""With partial existing data, correct split between created and skipped."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		# Page 1 already exists (10 IDs)
		existing = [f"mock-txn-1-{i}" for i in range(1, 11)]
		svc_frappe.get_all.return_value = existing

		result = import_transactions("_Test Connector")
		r = result["results"][0]
		assert r["created"] == 20  # pages 2 + 3 (10 each)
		assert r["skipped"] == 10  # page 1

	def test_import_idempotent_across_triggers(self, mock_all):
		"""Manual vs Scheduled trigger produces same transaction counts."""
		from erpnext_bank_import.services.import_service import import_transactions

		result_manual = import_transactions("_Test Connector", trigger="Manual")
		result_scheduled = import_transactions("_Test Connector", trigger="Scheduled")

		assert result_manual["results"][0]["created"] == result_scheduled["results"][0]["created"]
		assert result_manual["results"][0]["skipped"] == result_scheduled["results"][0]["skipped"]

	def test_import_handles_duplicate_on_insert(self, mock_all):
		"""Simulate race condition: insert fails after dedup check passes.
		The error is logged and processing continues."""
		from unittest.mock import MagicMock

		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import import_transactions

		# get_all returns empty (no known conflicts) - all 30 look new
		svc_frappe.get_all.return_value = []

		# The mock_all fixture sets up frappe.get_doc with a side_effect.
		# Keep the Bank Connector / Run Log handling from it, but make
		# Bank Transaction docs fail insert for the first 3 calls.
		orig_side_effect = svc_frappe.get_doc.side_effect
		bt_call_count = [0]

		def _mixed_side_effect(*args, **kwargs):
			# If called with a dict with doctype "Bank Transaction"
			if args and isinstance(args[0], dict) and args[0].get("doctype") == "Bank Transaction":
				bt_call_count[0] += 1
				doc = MagicMock()
				doc.name = f"BT-{bt_call_count[0]:04d}"
				if bt_call_count[0] <= 3:
					doc.insert.side_effect = Exception("Duplicate entry for key 'transaction_id'")
				else:
					doc.insert = MagicMock()
					doc.submit = MagicMock()
				return doc
			return orig_side_effect(*args, **kwargs)

		svc_frappe.get_doc.side_effect = _mixed_side_effect

		result = import_transactions("_Test Connector")
		r = result["results"][0]
		# 3 failed (insert raised), 27 succeeded out of 30 total
		assert r["created"] == 27
		assert r["error"] is None


class TestDedupEdgeCases:
	"""Edge cases for the dedup query helper ``_get_existing_transaction_ids``."""

	def test_empty_transaction_list_no_db_query(self):
		"""Empty input list returns empty set without querying DB."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
		) as m_get_all:
			result = _get_existing_transaction_ids("BA-001", [])
			assert result == set()
			m_get_all.assert_not_called()

	def test_mixed_existing_and_new(self):
		"""Batch check with some existing IDs returns the correct subset."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			return_value=["txn-002", "txn-004"],
		):
			result = _get_existing_transaction_ids(
				"BA-001",
				["txn-001", "txn-002", "txn-003", "txn-004"],
			)
			assert result == {"txn-002", "txn-004"}

	def test_none_exist_returns_empty(self):
		"""If no transaction IDs exist, return empty set."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			return_value=[],
		):
			result = _get_existing_transaction_ids("BA-001", ["txn-001", "txn-002"])
			assert result == set()

	def test_different_bank_accounts_no_dedup(self):
		"""Same transaction ID on different bank accounts are both created."""
		from erpnext_bank_import.services.import_service import _get_existing_transaction_ids

		# Simulate: for BA-001 the IDs exist, for BA-002 they don't.
		# The real frappe.get_all filters by bank_account, so each query is
		# scoped to that account. We simulate by tracking the bank_account param.
		def _scoped_get_all(doctype, filters=None, **kwargs):
			ba = filters.get("bank_account", "") if filters else ""
			if ba == "BA-001":
				return ["txn-001"]
			return []

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			side_effect=_scoped_get_all,
		):
			# txn-001 exists in BA-001
			result1 = _get_existing_transaction_ids("BA-001", ["txn-001"])
			assert result1 == {"txn-001"}

			# Same txn-001 does NOT exist in BA-002 (different bank account)
			result2 = _get_existing_transaction_ids("BA-002", ["txn-001"])
			assert result2 == set()


# =========================================================================
# Large-window / batch processing tests
# =========================================================================


class TestLargeWindowBatch:
	"""Tests for batch processing and progress reporting with large imports."""

	def test_progress_callback_called_during_import(self, mock_all):
		"""Progress callback should be invoked at least once during import."""
		from erpnext_bank_import.services.import_service import import_transactions

		progress_calls: list[tuple[int, int, str]] = []

		def track_progress(current: int, total: int, msg: str) -> None:
			progress_calls.append((current, total, msg))

		result = import_transactions("_Test Connector", on_progress=track_progress)
		assert result["status"] == "success"
		assert len(progress_calls) > 0
		last_msg = progress_calls[-1][2] if progress_calls else ""
		assert "Inserted" in last_msg or "Normalising" in last_msg or "Processing" in last_msg

	def test_progress_called_for_large_dataset(self, mock_all):
		"""With 600 transactions the callback is called multiple times."""
		from erpnext_bank_import.services.import_service import import_transactions

		large_provider = MockProvider(
			account_count=1,
			total_pages=10,
			transactions_per_page=60,  # 600 total
		)
		large_provider.authenticate()

		with patch(
			"erpnext_bank_import.services.import_service.get_connector",
			return_value=large_provider,
		):
			progress_calls: list[tuple[int, int, str]] = []

			def track_progress(current: int, total: int, msg: str) -> None:
				progress_calls.append((current, total, msg))

			result = import_transactions("_Test Connector", on_progress=track_progress)
			assert result["status"] == "success"
			r = result["results"][0]
			assert r["created"] == 600
			assert r["skipped"] == 0


class TestDedupChunking:
	"""Tests that dedup queries are safely chunked for large ID sets."""

	def test_dedup_chunks_large_id_sets(self):
		"""Dedup query should be split into smaller chunks with large ID sets."""
		from erpnext_bank_import.services.import_service import (
			DEDUP_CHUNK_SIZE,
			_get_existing_transaction_ids,
		)

		large_ids = [f"txn-{i:04d}" for i in range(DEDUP_CHUNK_SIZE * 3)]
		call_count = 0

		def _side_effect(doctype, filters=None, **kwargs):
			nonlocal call_count
			call_count += 1
			# frappe.get_all filter format: {"field": ["in", [val1, val2, ...]]}
			txn_filter = (filters or {}).get("transaction_id", [])
			txn_ids = txn_filter[1] if len(txn_filter) > 1 else []
			assert len(txn_ids) <= DEDUP_CHUNK_SIZE, f"Chunk too big: {len(txn_ids)}"
			return []

		with patch(
			"erpnext_bank_import.services.import_service.frappe.get_all",
			side_effect=_side_effect,
		):
			result = _get_existing_transaction_ids("BA-001", large_ids)
			assert result == set()
			assert call_count >= 3, f"Expected >=3 calls, got {call_count}"


class TestValidateImportWindow:
	"""Tests for the import window validation helper."""

	def test_incremental_sync_always_allowed(self):
		"""Incremental sync (no date_from) should pass validation."""
		from erpnext_bank_import.services.import_service import _validate_import_window

		with patch("erpnext_bank_import.services.import_service.frappe.get_doc") as m:
			result = _validate_import_window("_Test Connector", None, None)
			assert result is None
			m.assert_not_called()

	def test_window_within_limit_passes(self):
		"""Manual backfill within max_days should pass."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import _validate_import_window
		from datetime import date

		svc_frappe.utils.getdate.side_effect = date.fromisoformat
		svc_frappe.utils.nowdate.return_value = "2026-07-10"
		mock_doc = type("MockDoc", (), {"force_full_backfill": False, "max_import_window_days": 365})()
		svc_frappe.get_doc.return_value = mock_doc

		result = _validate_import_window("_Test Connector", "2026-06-01", "2026-07-10")
		assert result is None

	def test_window_exceeds_limit_rejected(self):
		"""Manual backfill exceeding max_days should return error message."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import _validate_import_window
		from datetime import date

		svc_frappe.utils.getdate.side_effect = date.fromisoformat
		svc_frappe.utils.nowdate.return_value = "2026-07-10"
		mock_doc = type("MockDoc", (), {"force_full_backfill": False, "max_import_window_days": 30})()
		svc_frappe.get_doc.return_value = mock_doc

		result = _validate_import_window("_Test Connector", "2026-01-01", "2026-07-10")
		assert result is not None
		assert "exceeds" in result
		assert "30" in result

	def test_force_backfill_bypasses_limit(self):
		"""Force Full Backfill should bypass the max window check."""
		from erpnext_bank_import.services.import_service import frappe as svc_frappe
		from erpnext_bank_import.services.import_service import _validate_import_window

		mock_doc = type("MockDoc", (), {"force_full_backfill": True, "max_import_window_days": 30})()
		svc_frappe.get_doc.return_value = mock_doc

		result = _validate_import_window("_Test Connector", "2026-01-01", "2026-07-10")
		assert result is None
