"""Integration tests for the Bank Connector ↔ ConnectorConfig pipeline.

These tests use mocked Frappe dependencies and validate the
``connectors/__init__.py`` entry points:

1. ``get_connector_config()`` — loads a ``ConnectorConfig`` from a
   ``Bank Connector`` DocType record.
2. ``get_all_enabled_connectors()`` — lists all enabled connectors.
3. ``get_connector_config_by_provider_company()`` — filters by
   provider + company.

All Frappe-dependent code paths are mocked to keep tests fast.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors import (
	get_all_enabled_connectors,
	get_connector_config,
	get_connector_config_by_provider_company,
)
from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import ConfigurationError


# =========================================================================
# Mock helpers
# =========================================================================


class _MockBankConnectorDoc:
	"""Simulates a ``Bank Connector`` DocType record for testing."""

	def __init__(self, **kwargs: Any) -> None:
		self._data = dict(kwargs)
		for k, v in kwargs.items():
			setattr(self, k, v)
		self.enabled = kwargs.get("enabled", True)
		self.provider_name = kwargs.get("provider_name", "mock")
		self.company = kwargs.get("company", "_Test Company")
		self.api_base_url = kwargs.get("api_base_url", "https://api.example.com")
		self.auth_method = kwargs.get("auth_method", "oauth2")
		self.client_id = kwargs.get("client_id", "cid")
		self.client_secret = kwargs.get("client_secret", "secret")
		self.authorize_url = kwargs.get("authorize_url", "/auth/authorize")
		self.token_url = kwargs.get("token_url", "/auth/token")
		self.revoke_url = kwargs.get("revoke_url", None)
		self.scopes = kwargs.get("scopes", "read write")
		self.redirect_uri = kwargs.get("redirect_uri", None)
		self.rate_limit_rps = kwargs.get("rate_limit_rps", None)
		self.timeout_seconds = kwargs.get("timeout_seconds", 30)
		self.token_safety_buffer_seconds = kwargs.get("token_safety_buffer_seconds", 60)
		self.account_mappings = kwargs.get("account_mappings", [])

	def get_password(self, fieldname: str, *args: Any, **kwargs: Any) -> str:
		return self._data.get(fieldname, "")

	def get_connector_config(self) -> ConnectorConfig:
		"""Replicates the real DocType's get_connector_config()."""
		scopes_list: list[str] = []
		if self.scopes:
			scopes_list = [s.strip() for s in self.scopes.split() if s.strip()]

		return ConnectorConfig(
			provider_name=self.provider_name,
			api_base_url=self.api_base_url,
			auth_method=self.auth_method,
			client_id=self.client_id,
			client_secret=self.get_password("client_secret") if self.client_secret else None,
			authorize_url=self.authorize_url,
			token_url=self.token_url,
			revoke_url=self.revoke_url,
			scopes=scopes_list,
			redirect_uri=self.redirect_uri,
			rate_limit_rps=self.rate_limit_rps,
			timeout_seconds=self.timeout_seconds or 30.0,
			token_safety_buffer_seconds=self.token_safety_buffer_seconds or 60,
		)

	def get_enabled_mappings(self) -> list[dict[str, str]]:
		return [
			{
				"provider_account_id": m["provider_account_id"],
				"bank_account": m["bank_account"],
			}
			for m in self.account_mappings
			if m.get("is_enabled", True)
		]


def _patch_frappe(
	exists: bool = True,
	doc_kwargs: dict[str, Any] | None = None,
	all_records: list[dict[str, Any]] | None = None,
) -> MagicMock:
	"""Patch ``frappe`` in the ``erpnext_bank_import.connectors`` module."""
	patch_frappe = patch("erpnext_bank_import.connectors.frappe", autospec=False)
	m_frappe = patch_frappe.start()

	m_frappe.db.exists.return_value = exists
	m_frappe.get_doc.return_value = _MockBankConnectorDoc(**(doc_kwargs or {}))

	if all_records is not None:
		m_frappe.db.get_all.return_value = all_records
	else:
		m_frappe.db.get_all.return_value = []

	return m_frappe


# =========================================================================
# Tests
# =========================================================================


class TestGetConnectorConfig:
	"""Tests for ``get_connector_config()``."""

	def test_returns_connector_config_for_existing_enabled_connector(self):
		"""A valid, enabled connector should return a ConnectorConfig."""
		mock = _patch_frappe(
			exists=True,
			doc_kwargs={
				"connector_name": "Test Revolut",
				"provider_name": "revolut",
				"company": "Acme Ltd",
				"api_base_url": "https://api.revolut.com",
				"client_id": "my-client-id",
				"client_secret": "my-client-secret",
			},
		)
		try:
			config = get_connector_config("Test Revolut")
			assert isinstance(config, ConnectorConfig)
			assert config.provider_name == "revolut"
			assert config.client_id == "my-client-id"
			assert config.client_secret == "my-client-secret"
			assert config.api_base_url == "https://api.revolut.com"
		finally:
			mock.stop()

	def test_raises_for_nonexistent_connector(self):
		"""A non-existent connector should raise ConfigurationError."""
		mock = _patch_frappe(exists=False)
		try:
			with pytest.raises(ConfigurationError, match="does not exist"):
				get_connector_config("NonExistent")
		finally:
			mock.stop()

	def test_raises_for_disabled_connector(self):
		"""A disabled connector should raise ConfigurationError."""
		mock = _patch_frappe(
			exists=True,
			doc_kwargs={"connector_name": "Disabled", "enabled": False},
		)
		try:
			with pytest.raises(ConfigurationError, match="disabled"):
				get_connector_config("Disabled")
		finally:
			mock.stop()

	def test_client_secret_retrieved_via_get_password(self):
		"""client_secret should be retrieved via get_password()."""
		mock = _patch_frappe(
			exists=True,
			doc_kwargs={
				"client_secret": "encrypted-secret",
			},
		)
		try:
			config = get_connector_config("Test")
			assert config.client_secret == "encrypted-secret"
		finally:
			mock.stop()


class TestGetAllEnabledConnectors:
	"""Tests for ``get_all_enabled_connectors()``."""

	def test_returns_names_of_enabled_connectors(self):
		"""Should return connector_name for all enabled records."""
		mock = _patch_frappe(
			all_records=[
				{"connector_name": "Revolut Prod"},
				{"connector_name": "Revolut Sandbox"},
			],
		)
		try:
			names = get_all_enabled_connectors()
			assert names == ["Revolut Prod", "Revolut Sandbox"]
		finally:
			mock.stop()

	def test_returns_empty_list_when_none_enabled(self):
		"""Should return empty list when no enabled connectors exist."""
		mock = _patch_frappe(all_records=[])
		try:
			names = get_all_enabled_connectors()
			assert names == []
		finally:
			mock.stop()


class TestGetConnectorConfigByProviderCompany:
	"""Tests for ``get_connector_config_by_provider_company()``."""

	def test_returns_matching_configs(self):
		"""Should return ConnectorConfig for each matching record."""
		mock = _patch_frappe(
			all_records=[
				{"connector_name": "Revolut Acme"},
				{"connector_name": "Revolut Acme Sandbox"},
			],
			doc_kwargs={"provider_name": "revolut", "company": "Acme Ltd"},
		)
		try:
			configs = get_connector_config_by_provider_company("revolut", "Acme Ltd")
			assert len(configs) == 2
			assert all(isinstance(c, ConnectorConfig) for c in configs)
			assert all(c.provider_name == "revolut" for c in configs)
		finally:
			mock.stop()

	def test_returns_empty_list_when_no_match(self):
		"""Should return empty list when no connectors match the filter."""
		mock = _patch_frappe(all_records=[])
		try:
			configs = get_connector_config_by_provider_company("revolut", "NoSuchCompany")
			assert configs == []
		finally:
			mock.stop()
