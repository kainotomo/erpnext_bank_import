"""Tests for the Bank Connector DocType validation and config conversion.

These tests require a running Frappe site (run via ``bench run-tests``).
They validate:

1. Field validation rules (required fields, URL format, OAuth2 fields).
2. Account mapping validations (duplicate IDs, duplicate bank accounts,
   minimum mappings when enabled).
3. ``get_connector_config()`` round-trip correctness.
4. ``get_enabled_mappings()`` filtering.
5. URL normalisation in ``before_save()``.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.exceptions import DuplicateEntryError


class TestBankConnector(unittest.TestCase):
	"""Validates the Bank Connector DocType lifecycle."""

	TEST_BANK_ACCOUNT: str = "_Test Bank - _Test Bank"
	TEST_BANK_ACCOUNT_2: str = "_Test Bank 2 - _Test Bank"

	@classmethod
	def setUpClass(cls):
		"""Create test fixtures needed by all tests.

		Idempotent — safe to run even if some fixtures already exist
		(e.g. when ERPNext's own test setup has already created them).
		"""
		super().setUpClass()

		_company = "_Test Company"
		_abbr = "_TC"

		# ------------------------------------------------------------------
		# Warehouse Type (required by ERPNext's Company on_update hooks)
		# ------------------------------------------------------------------
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()

		# ------------------------------------------------------------------
		# Company
		# ------------------------------------------------------------------
		if not frappe.db.exists("Company", _company):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": _company,
					"abbr": _abbr,
					"country": "India",
					"default_currency": "INR",
				}
			).insert()

		# ------------------------------------------------------------------
		# Bank master
		# ------------------------------------------------------------------
		if not frappe.db.exists("Bank", "_Test Bank"):
			frappe.get_doc({"doctype": "Bank", "bank_name": "_Test Bank"}).insert()

		# ------------------------------------------------------------------
		# Parent Bank Accounts group (needed for Account tree)
		# ------------------------------------------------------------------
		def _get_bank_group_parent() -> str | None:
			"""Find a suitable parent group account for bank accounts."""
			# Try with company-abbr suffix, then without, then any Bank group.
			for name in (f"Bank Accounts - {_abbr}", "Bank Accounts"):
				parent = frappe.db.get_value(
					"Account",
					{"company": _company, "account_name": name.split(" - ")[0], "is_group": 1},
				)
				if parent:
					return parent
			# Fallback: any group account with account_type="Bank"
			return frappe.db.get_value(
				"Account",
				{"company": _company, "account_type": "Bank", "is_group": 1},
			)

		# ------------------------------------------------------------------
		# Bank GL Account #1  →  name: "_Test Bank - _TC"
		# ------------------------------------------------------------------
		if not frappe.db.exists("Account", f"_Test Bank - {_abbr}"):
			parent = _get_bank_group_parent()
			if not parent:
				# Create the group account as a last resort.
				current_assets = frappe.db.get_value(
					"Account",
					{"company": _company, "account_name": "Current Assets", "is_group": 1},
				) or frappe.db.get_value(
					"Account",
					{"company": _company, "account_type": "Current Asset", "is_group": 1},
				)
				if current_assets:
					parent_doc = frappe.get_doc(
						{
							"doctype": "Account",
							"account_name": f"Bank Accounts - {_abbr}",
							"is_group": 1,
							"company": _company,
							"parent_account": current_assets,
							"account_type": "Bank",
							"root_type": "Asset",
						}
					)
					parent_doc.insert()
					parent = parent_doc.name

			if parent:
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": "_Test Bank",
						"account_type": "Bank",
						"company": _company,
						"parent_account": parent,
						"account_currency": "INR",
					}
				).insert()

		# ------------------------------------------------------------------
		# Bank Account #1  →  name: "_Test Bank - _Test Bank"
		# ------------------------------------------------------------------
		if not frappe.db.exists("Bank Account", cls.TEST_BANK_ACCOUNT):
			frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": "_Test Bank",
					"bank": "_Test Bank",
					"account": f"_Test Bank - {_abbr}",
					"company": _company,
					"is_company_account": 1,
				}
			).insert()

		# ------------------------------------------------------------------
		# Bank GL Account #2  →  name: "_Test Bank 2 - _TC"
		# ------------------------------------------------------------------
		if not frappe.db.exists("Account", f"_Test Bank 2 - {_abbr}"):
			parent = _get_bank_group_parent()
			if parent:
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": "_Test Bank 2",
						"account_type": "Bank",
						"company": _company,
						"parent_account": parent,
						"account_currency": "USD",
					}
				).insert()

		# ------------------------------------------------------------------
		# Bank Account #2  →  name: "_Test Bank 2 - _Test Bank"
		# ------------------------------------------------------------------
		if not frappe.db.exists("Bank Account", cls.TEST_BANK_ACCOUNT_2):
			frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": "_Test Bank 2",
					"bank": "_Test Bank",
					"account": f"_Test Bank 2 - {_abbr}",
					"company": _company,
					"is_company_account": 1,
				}
			).insert()

	def setUp(self):
		"""Ensure a clean state before each test."""
		# Clean any existing test connectors.
		for name in frappe.db.get_all("Bank Connector", pluck="connector_name"):
			if name.startswith("_Test"):
				frappe.delete_doc("Bank Connector", name, force=True)

	@staticmethod
	def _make_connector_dict(**overrides: str | int | bool | list) -> dict:
		"""Build a minimal valid Bank Connector document dict."""
		defaults: dict = {
			"doctype": "Bank Connector",
			"connector_name": "_Test Connector",
			"provider_name": "mock",
			"company": "_Test Company",
			"enabled": 1,
			"api_base_url": "https://api.example.com",
			"auth_method": "oauth2",
			"client_id": "test-client-id",
			"client_secret": "test-client-secret",
			"authorize_url": "https://api.example.com/auth/authorize",
			"token_url": "https://api.example.com/auth/token",
			"scopes": "transactions:read accounts:read",
			"redirect_uri": "https://my-erpnext.example.com/callback",
			"account_mappings": [
				{
					"provider_account_id": "acc-001",
					"provider_account_name": "Business EUR",
					"bank_account": "_Test Bank - _Test Bank",
					"is_enabled": 1,
				},
			],
		}
		defaults.update(overrides)
		return defaults

	# ------------------------------------------------------------------
	# Required fields
	# ------------------------------------------------------------------

	def test_minimal_valid_connector(self):
		"""A connector with all required fields and at least one mapping should save."""
		doc = frappe.get_doc(self._make_connector_dict())
		doc.insert()
		self.assertTrue(doc.name)
		self.assertEqual(doc.provider_name, "mock")

	def test_missing_connector_name_raises(self):
		"""Connector Name is required."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(connector_name="")).insert()

	def test_missing_provider_name_raises(self):
		"""Provider Name is required."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(provider_name="")).insert()

	def test_missing_company_raises(self):
		"""Company is required."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(company="")).insert()

	def test_missing_api_base_url_raises(self):
		"""API Base URL is required."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(api_base_url="")).insert()

	def test_duplicate_connector_name_raises(self):
		"""Connector Name must be unique."""
		frappe.get_doc(self._make_connector_dict()).insert()
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(self._make_connector_dict()).insert()

	# ------------------------------------------------------------------
	# URL validation
	# ------------------------------------------------------------------

	def test_invalid_api_base_url_raises(self):
		"""API Base URL must start with http:// or https://."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(api_base_url="not-a-url")).insert()

	def test_invalid_authorize_url_raises(self):
		"""Authorize URL must be valid."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(authorize_url="ftp://bad")).insert()

	# ------------------------------------------------------------------
	# OAuth2 field validation
	# ------------------------------------------------------------------

	def test_oauth2_missing_client_id_raises(self):
		"""Client ID is required when auth_method is oauth2."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(client_id="")).insert()

	def test_oauth2_missing_client_secret_raises(self):
		"""Client Secret is required when auth_method is oauth2."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(client_secret="")).insert()

	def test_oauth2_missing_authorize_url_raises(self):
		"""Authorize URL is required when auth_method is oauth2."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(authorize_url="")).insert()

	def test_oauth2_missing_token_url_raises(self):
		"""Token URL is required when auth_method is oauth2."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(self._make_connector_dict(token_url="")).insert()

	# ------------------------------------------------------------------
	# Account mapping validation
	# ------------------------------------------------------------------

	def test_no_enabled_mappings_when_enabled_saves(self):
		"""Connector saves without mappings (mappings can be added later)."""
		doc = frappe.get_doc(self._make_connector_dict(account_mappings=[]))
		doc.insert()
		self.assertTrue(doc.enabled)

	def test_all_mappings_disabled_when_enabled_saves(self):
		"""If all mappings are disabled, the connector should still save."""
		doc = frappe.get_doc(
			self._make_connector_dict(
				account_mappings=[
					{
						"provider_account_id": "acc-001",
						"bank_account": TestBankConnector.TEST_BANK_ACCOUNT,
						"is_enabled": 0,
					},
				],
			)
		)
		doc.insert()
		self.assertTrue(doc.name)

	def test_disabled_connector_allows_zero_mappings(self):
		"""A disabled connector should not require any account mappings."""
		doc = frappe.get_doc(self._make_connector_dict(enabled=0, account_mappings=[]))
		doc.insert()
		self.assertTrue(doc.name)

	def test_duplicate_provider_account_id_raises(self):
		"""Duplicate provider_account_id within the same connector should raise."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				self._make_connector_dict(
					account_mappings=[
						{
							"provider_account_id": "acc-001",
							"bank_account": TestBankConnector.TEST_BANK_ACCOUNT,
							"is_enabled": 1,
						},
						{
							"provider_account_id": "acc-001",
							"bank_account": TestBankConnector.TEST_BANK_ACCOUNT_2,
							"is_enabled": 1,
						},
					],
				)
			).insert()

	def test_duplicate_bank_account_raises(self):
		"""Duplicate bank_account links within the same connector should raise."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				self._make_connector_dict(
					account_mappings=[
						{
							"provider_account_id": "acc-001",
							"bank_account": TestBankConnector.TEST_BANK_ACCOUNT,
							"is_enabled": 1,
						},
						{
							"provider_account_id": "acc-002",
							"bank_account": TestBankConnector.TEST_BANK_ACCOUNT,
							"is_enabled": 1,
						},
					],
				)
			).insert()

	# ------------------------------------------------------------------
	# get_connector_config()
	# ------------------------------------------------------------------

	def test_get_connector_config_returns_valid_config(self):
		"""get_connector_config() should return a ConnectorConfig with all fields populated."""
		doc = frappe.get_doc(self._make_connector_dict())
		doc.insert()
		config = doc.get_connector_config()

		self.assertEqual(config.provider_name, "mock")
		self.assertEqual(config.api_base_url, "https://api.example.com")
		self.assertEqual(config.auth_method, "oauth2")
		self.assertEqual(config.client_id, "test-client-id")
		self.assertEqual(config.client_secret, "test-client-secret")
		self.assertEqual(config.authorize_url, "https://api.example.com/auth/authorize")
		self.assertEqual(config.token_url, "https://api.example.com/auth/token")
		self.assertEqual(config.redirect_uri, "https://my-erpnext.example.com/callback")
		self.assertEqual(config.scopes, ["transactions:read", "accounts:read"])
		self.assertEqual(config.rate_limit_rps, None)
		self.assertEqual(config.timeout_seconds, 30.0)
		self.assertEqual(config.token_safety_buffer_seconds, 60)

	def test_get_connector_config_no_oauth_returns_none_secrets(self):
		"""When auth_method is not oauth2, client_secret should be None."""
		doc = frappe.get_doc(
			self._make_connector_dict(
				auth_method="api_key",
				client_id="",
				client_secret="",
				authorize_url="",
				token_url="",
				scopes="",
				redirect_uri="",
			)
		)
		doc.insert()
		config = doc.get_connector_config()
		self.assertIsNone(config.client_id)
		self.assertIsNone(config.client_secret)

	def test_get_connector_config_scopes_split_correctly(self):
		"""Space-separated scopes should be split into a list."""
		doc = frappe.get_doc(self._make_connector_dict(scopes="read write admin"))
		doc.insert()
		config = doc.get_connector_config()
		self.assertEqual(config.scopes, ["read", "write", "admin"])

	def test_get_connector_config_empty_scopes(self):
		"""Empty scopes should return an empty list."""
		doc = frappe.get_doc(self._make_connector_dict(scopes=""))
		doc.insert()
		config = doc.get_connector_config()
		self.assertEqual(config.scopes, [])

	# ------------------------------------------------------------------
	# get_enabled_mappings()
	# ------------------------------------------------------------------

	def test_get_enabled_mappings_filters_correctly(self):
		"""Only enabled mappings should be returned."""
		doc = frappe.get_doc(
			self._make_connector_dict(
				account_mappings=[
					{
						"provider_account_id": "acc-001",
						"provider_account_name": "EUR Account",
						"bank_account": TestBankConnector.TEST_BANK_ACCOUNT,
						"is_enabled": 1,
					},
					{
						"provider_account_id": "acc-002",
						"provider_account_name": "USD Account",
						"bank_account": TestBankConnector.TEST_BANK_ACCOUNT_2,
						"is_enabled": 0,
					},
				],
			)
		)
		doc.insert()
		mappings = doc.get_enabled_mappings()
		self.assertEqual(len(mappings), 1)
		self.assertEqual(mappings[0]["provider_account_id"], "acc-001")

	# ------------------------------------------------------------------
	# URL normalisation
	# ------------------------------------------------------------------

	def test_before_save_normalises_trailing_slashes(self):
		"""Trailing slashes on URL fields should be stripped."""
		doc = frappe.get_doc(
			self._make_connector_dict(
				api_base_url="https://api.example.com/",
				authorize_url="https://api.example.com/auth/authorize/",
				token_url="https://api.example.com/auth/token/",
			)
		)
		doc.insert()
		self.assertEqual(doc.api_base_url, "https://api.example.com")
		self.assertEqual(doc.authorize_url, "https://api.example.com/auth/authorize")
		self.assertEqual(doc.token_url, "https://api.example.com/auth/token")
