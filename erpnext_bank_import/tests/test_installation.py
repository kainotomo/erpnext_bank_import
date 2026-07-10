import unittest

import frappe

import erpnext_bank_import


class TestInstallation(unittest.TestCase):
	"""Smoke tests to validate the app installed and registered correctly."""

	def test_app_name_in_hooks(self):
		"""App name should be registered in Frappe hooks."""
		app_name = frappe.get_hooks("app_name")
		self.assertIn("erpnext_bank_import", app_name)

	def test_app_version_is_defined(self):
		"""__version__ should be a non-empty string."""
		self.assertTrue(erpnext_bank_import.__version__)
		self.assertIsInstance(erpnext_bank_import.__version__, str)

	def test_required_apps_include_erpnext(self):
		"""required_apps should declare ERPNext as a dependency."""
		required = frappe.get_hooks("required_apps")
		self.assertIn("erpnext", required)
