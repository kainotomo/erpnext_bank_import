"""Tests for the RunLogger context manager.

These tests validate:

1. Context manager creates and completes a run log record.
2. Run log captures correct counts (created/skipped).
3. Exception inside context manager sets status=Error and captures traceback.
4. Per-account results are serialized correctly.
5. ``set_error()`` marks the run as errored without raising.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_frappe():
	"""Patch the entire frappe module reference used by RunLogger."""
	frappe_patcher = patch("erpnext_bank_import.services.run_log.frappe", autospec=False)
	mock_frappe = frappe_patcher.start()

	mock_doc = MagicMock()
	mock_doc.name = "log-001"
	mock_frappe.get_doc.return_value = mock_doc
	mock_frappe.as_json.side_effect = lambda d: str(d)
	mock_frappe.db.set_value.return_value = None
	mock_frappe.db.get_value.return_value = None
	mock_frappe.log_error.return_value = None

	yield mock_frappe

	frappe_patcher.stop()


# =========================================================================
# Tests
# =========================================================================


class TestRunLoggerInit:
	"""Tests for RunLogger initialization and entry."""

	def test_creates_run_log_on_entry(self, mock_frappe):
		"""__enter__ creates a Bank Import Run Log document."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock", trigger="Manual") as run:
			assert run.name == "log-001"

		# Verify get_doc was called with correct doctype fields
		call_kwargs = mock_frappe.get_doc.call_args[0][0]
		assert call_kwargs["doctype"] == "Bank Import Run Log"
		assert call_kwargs["connector_name"] == "Test Connector"
		assert call_kwargs["provider_name"] == "mock"
		assert call_kwargs["trigger"] == "Manual"

	def test_default_trigger_is_manual(self, mock_frappe):
		"""Default trigger should be Manual."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock") as run:
			_ = run.name

		call_kwargs = mock_frappe.get_doc.call_args[0][0]
		assert call_kwargs["trigger"] == "Manual"


class TestRunLoggerHappyPath:
	"""Normal operation — no errors."""

	def test_records_account_results(self, mock_frappe):
		"""Per-account results are collected and stored."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock") as run:
			run.add_account_result(account_id="acc-001", created=5, skipped=2)
			run.add_account_result(account_id="acc-002", created=3, skipped=1)

			assert run.total_created == 8
			assert run.total_skipped == 3

		# On exit, the per-account results should be serialized
		set_value_calls = mock_frappe.db.set_value.call_args
		assert set_value_calls is not None
		field_values = set_value_calls[0][2]
		assert field_values["status"] == "Success"
		assert field_values["total_created"] == 8
		assert field_values["total_skipped"] == 3

	def test_partial_status_when_errors(self, mock_frappe):
		"""Partial status when some accounts have errors."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock") as run:
			run.add_account_result(account_id="acc-001", created=5, skipped=0)
			run.add_account_result(account_id="acc-002", created=0, skipped=0, error="Auth failed")

		set_value_calls = mock_frappe.db.set_value.call_args
		field_values = set_value_calls[0][2]
		assert field_values["status"] == "Partial"

	def test_no_results_sets_success(self, mock_frappe):
		"""No account results recorded → status is Success (no accounts)."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock") as run:
			_ = run.name

		set_value_calls = mock_frappe.db.set_value.call_args
		field_values = set_value_calls[0][2]
		assert field_values["status"] == "Success"
		assert field_values["total_created"] == 0
		assert field_values["total_skipped"] == 0


class TestRunLoggerErrorPath:
	"""Exception handling inside the context manager."""

	def test_exception_sets_status_error(self, mock_frappe):
		"""Exception inside context → status=Error with traceback."""
		from erpnext_bank_import.services.run_log import RunLogger

		with pytest.raises(RuntimeError, match="Something broke"):
			with RunLogger("Test Connector", "mock") as _:
				raise RuntimeError("Something broke")

		set_value_calls = mock_frappe.db.set_value.call_args
		field_values = set_value_calls[0][2]
		assert field_values["status"] == "Error"
		assert "RuntimeError" in field_values["error_summary"]
		assert field_values["error_traceback"] is not None

	def test_set_error_marks_without_exception(self, mock_frappe):
		"""set_error() marks the run without raising."""
		from erpnext_bank_import.services.run_log import RunLogger

		with RunLogger("Test Connector", "mock") as run:
			run.set_error(summary="Config error: missing API key")

		set_value_calls = mock_frappe.db.set_value.call_args
		field_values = set_value_calls[0][2]
		assert field_values["status"] == "Error"
		assert field_values["error_summary"] == "Config error: missing API key"

	def test_logger_does_not_suppress_exception(self, mock_frappe):
		"""The context manager must NOT suppress the original exception."""
		from erpnext_bank_import.services.run_log import RunLogger

		with pytest.raises(ValueError):
			with RunLogger("Test Connector", "mock") as run:
				run.add_account_result(account_id="acc-001", created=1, skipped=0)
				raise ValueError("Original error")


class TestRunLoggerAccountResults:
	"""Per-account result methods."""

	def test_account_results_property(self, mock_frappe):
		"""account_results property returns copy of list."""
		from erpnext_bank_import.services.run_log import RunLogger

		run = RunLogger("Test Connector", "mock")
		run.add_account_result(account_id="acc-001", created=5, skipped=2)
		run.add_account_result(account_id="acc-002", created=3, skipped=1, error="Oops")

		results = run.account_results
		assert len(results) == 2
		assert results[0]["account_id"] == "acc-001"
		assert results[1]["account_id"] == "acc-002"
		assert results[1]["error"] == "Oops"

	def test_extra_fields_in_account_result(self, mock_frappe):
		"""Extra kwargs are preserved in account results."""
		from erpnext_bank_import.services.run_log import RunLogger

		run = RunLogger("Test Connector", "mock")
		run.add_account_result(
			account_id="acc-001",
			created=5,
			skipped=0,
			date_from="2026-01-01",
			date_to="2026-01-31",
		)

		results = run.account_results
		assert results[0]["date_from"] == "2026-01-01"
		assert results[0]["date_to"] == "2026-01-31"
