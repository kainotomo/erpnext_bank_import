"""Tests for the Bank Import Run Log DocType.

These tests validate:

1. Record creation with valid fields.
2. Required field validation (connector_name is mandatory).
3. Status enum constraint.
4. Duration is computed on save.

Run with ``bench run-tests --app erpnext_bank_import``.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase


class TestBankImportRunLog(FrappeTestCase):
	"""Doctype validation tests for Bank Import Run Log."""

	def setUp(self):
		"""Create a valid run log record for test reuse."""
		self.doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"provider_name": "mock",
				"status": "Success",
				"trigger": "Manual",
				"started_at": frappe.utils.now_datetime(),
			}
		)

	def test_create_run_log(self):
		"""A valid run log record should be insertable."""
		doc = self.doc.insert()
		self.assertIsNotNone(doc.name)
		self.assertEqual(doc.connector_name, "_Test Connector")
		self.assertEqual(doc.status, "Success")

	def test_connector_name_is_required(self):
		"""connector_name is mandatory — insertion should fail without it."""
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"status": "Success",
				"started_at": frappe.utils.now_datetime(),
			}
		)
		with self.assertRaises(frappe.MandatoryError):
			doc.insert()

	def test_status_defaults_to_first_option(self):
		"""status is a Select field — defaults to first option (Success)."""
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"started_at": frappe.utils.now_datetime(),
			}
		)
		doc.insert()
		self.assertEqual(doc.status, "Success")

	def test_status_enum(self):
		"""Only Success/Partial/Error are valid status values."""
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"status": "Invalid",
				"started_at": frappe.utils.now_datetime(),
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_duration_computed_on_save(self):
		"""Duration should be computed from started_at and ended_at."""
		from frappe.utils import add_to_date, now_datetime

		started = now_datetime()
		ended = add_to_date(started, seconds=30, as_datetime=True)

		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"status": "Success",
				"trigger": "Scheduled",
				"started_at": started,
				"ended_at": ended,
			}
		)
		doc.insert()
		# Duration should be approximately 30 seconds
		self.assertGreaterEqual(doc.duration_seconds, 29.0)
		self.assertLessEqual(doc.duration_seconds, 31.0)

	def test_per_account_results_json(self):
		"""per_account_results JSON field stores and retrieves correctly."""
		results = [
			{"account_id": "acc-001", "created": 5, "skipped": 2},
			{"account_id": "acc-002", "created": 3, "skipped": 1, "error": "Auth failed"},
		]
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"status": "Partial",
				"trigger": "Scheduled",
				"started_at": frappe.utils.now_datetime(),
				"per_account_results": frappe.as_json(results),
			}
		)
		doc.insert()
		self.assertIsNotNone(doc.per_account_results)
		loaded = frappe.parse_json(doc.per_account_results)
		self.assertEqual(len(loaded), 2)
		self.assertEqual(loaded[0]["account_id"], "acc-001")
		self.assertEqual(loaded[1]["error"], "Auth failed")

	def test_error_traceback_storage(self):
		"""Long traceback text should be storable."""
		long_traceback = "Traceback (most recent call last):\n  File \"test.py\", line 1, in <module>\n    raise RuntimeError('test')\nRuntimeError: test"
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": "_Test Connector",
				"status": "Error",
				"trigger": "Scheduled",
				"started_at": frappe.utils.now_datetime(),
				"error_summary": "RuntimeError: test",
				"error_traceback": long_traceback,
			}
		)
		doc.insert()
		self.assertEqual(doc.error_summary, "RuntimeError: test")
		self.assertIn("RuntimeError", doc.error_traceback)
