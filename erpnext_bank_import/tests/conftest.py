"""Shared pytest fixtures for all ``erpnext_bank_import`` test modules.

Fixtures defined here are auto-discovered by pytest and available in
every test file under ``erpnext_bank_import/tests/``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.mock_provider import MockProvider

# =========================================================================
# Connector configuration fixtures
# =========================================================================


@pytest.fixture(scope="session")
def connector_config_dict() -> dict[str, Any]:
	"""Default valid ``ConnectorConfig`` keyword arguments (session-scoped)."""
	return {
		"provider_name": "mock",
		"api_base_url": "https://mock-bank.example.com/api",
		"auth_method": "oauth2",
		"client_id": "test-client-id",
		"client_secret": "test-client-secret",
		"authorize_url": "https://mock-bank.example.com/api/auth/authorize",
		"token_url": "https://mock-bank.example.com/api/auth/token",
		"scopes": ["transactions:read"],
	}


@pytest.fixture
def connector_config(connector_config_dict: dict[str, Any]) -> ConnectorConfig:
	"""A ``ConnectorConfig`` instance built from default dict (function-scoped)."""
	return ConnectorConfig(**connector_config_dict)


# =========================================================================
# Mock provider fixtures
# =========================================================================


@pytest.fixture
def mock_provider() -> MockProvider:
	"""A pre-authenticated ``MockProvider`` ready for use."""
	provider = MockProvider()
	provider.authenticate()
	return provider


@pytest.fixture
def mock_provider_auth_fail() -> MockProvider:
	"""A ``MockProvider`` that fails on authentication."""
	return MockProvider(auth_should_fail=True)


@pytest.fixture
def mock_provider_configured() -> type:
	"""Factory fixture for a ``MockProvider`` with custom parameters.

	Usage
	-----
	.. code-block:: python

	    def test_something(mock_provider_configured):
	        provider = mock_provider_configured(account_count=2, total_pages=1, transactions_per_page=5)
	        provider.authenticate()
	        ...
	"""

	def _make(**kwargs: Any) -> MockProvider:
		return MockProvider(**kwargs)

	return _make


# =========================================================================
# Frappe mock (session-scoped autouse)
# =========================================================================


@pytest.fixture(scope="session", autouse=True)
def mock_frappe_for_services():
	"""Patch ``frappe`` across all service modules with a safe MagicMock.

	This fixture starts automatically for every test session so that
	unit tests do not trigger Frappe's thread-local proxy (which
	requires a request context).  Individual tests can override specific
	mock methods as needed.

	Modules patched:
	* ``erpnext_bank_import.services.oauth.frappe``
	* ``erpnext_bank_import.services.import_service.frappe``
	* ``erpnext_bank_import.services.run_log.frappe``
	* ``erpnext_bank_import.services.retry.frappe``
	* ``erpnext_bank_import.services.diagnostics.frappe``
	* ``erpnext_bank_import.connectors.frappe``
	"""
	module_paths = [
		"erpnext_bank_import.services.oauth",
		"erpnext_bank_import.services.import_service",
		"erpnext_bank_import.services.run_log",
		"erpnext_bank_import.services.retry",
		"erpnext_bank_import.connectors",
	]
	patches = []
	for path in module_paths:
		p = patch(f"{path}.frappe", autospec=False)
		m = p.start()
		# Set up sensible defaults on the mock
		m.db.exists.return_value = False
		m.db.get_all.return_value = []
		m.db.set_value.return_value = None
		m.db.get_value.return_value = None
		m.get_doc.return_value = MagicMock(name=f"MockDoc-{path}")
		m.get_doc.return_value.name = "mock-doc-001"
		m.get_doc.return_value.insert = MagicMock()
		m.get_doc.return_value.submit = MagicMock()
		m.new_doc.return_value = MagicMock()
		m.log_error.return_value = None
		m.logger.return_value.warning.return_value = None
		m.logger.return_value.error.return_value = None
		m.utils.today.return_value = "2026-07-10"
		m.utils.nowdate.return_value = "2026-07-10"
		m.utils.add_days.return_value = "2026-04-11"
		m.utils.now_datetime.return_value = None  # overridden per test if needed
		m.as_json.side_effect = lambda d: str(d) if isinstance(d, (list, dict)) else d
		m.parse_json.side_effect = lambda s: eval(s) if isinstance(s, str) and s.startswith("[") else s
		patches.append(p)

	yield

	for p in patches:
		p.stop()


@pytest.fixture
def mock_requests_post() -> MagicMock:
	"""A function-scoped fixture that patches ``requests.post``.

	Returns the mock so tests can configure return values:
	.. code-block:: python

	    def test_(mock_requests_post):
	        mock_requests_post.return_value.status_code = 200
	        mock_requests_post.return_value.json.return_value = {...}
	"""
	with patch("erpnext_bank_import.services.oauth.requests.post") as m:
		m.return_value.status_code = 200
		m.return_value.json.return_value = {
			"access_token": "new-access",
			"refresh_token": "new-refresh",
			"token_type": "Bearer",
			"expires_in": 3600,
			"scope": "read",
		}
		yield m


# =========================================================================
# Misc helpers
# =========================================================================


@pytest.fixture
def mock_frappe_logger():
	"""Patch ``frappe.logger`` across the retry service."""
	with patch("erpnext_bank_import.services.retry.frappe.logger") as m:
		m.return_value.warning.return_value = None
		yield m
