"""Reusable import-service test helpers, mock documents, and patch contexts.

Provides mock implementations of Frappe DocTypes used by the import
orchestration layer, together with context managers that set up the
full set of patches needed by ``import_transactions`` tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.mock_provider import MockProvider

# ---------------------------------------------------------------------------
# Mock DocType helpers
# ---------------------------------------------------------------------------


class MockMapping:
    """Simulates a ``BankConnectorAccountMapping`` child-table row."""

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


class MockBankConnectorDoc:
    """Simulates a ``Bank Connector`` DocType record."""

    def __init__(self, enabled: bool = True, mappings: list[MockMapping] | None = None):
        self.enabled = enabled
        self.account_mappings = mappings if mappings is not None else [MockMapping()]

    def get_connector_config(self) -> ConnectorConfig:
        return ConnectorConfig(
            provider_name="mock",
            api_base_url="https://api.example.com",
        )


def make_mock_mapping(**overrides: Any) -> MockMapping:
    """Factory for ``MockMapping`` with overridable defaults."""
    defaults: dict[str, Any] = {
        "provider_account_id": "mock-acc-001",
        "bank_account": "BA-001",
        "is_enabled": True,
        "last_synced_at": None,
    }
    defaults.update(overrides)
    return MockMapping(**defaults)


def make_mock_connector_doc(
    enabled: bool = True,
    account_ids: list[str] | None = None,
) -> MockBankConnectorDoc:
    """Factory for ``MockBankConnectorDoc`` with one mapping per account ID.

    Args:
        enabled: Whether the connector doc is enabled.
        account_ids: Provider account IDs for mappings.
            Defaults to ``["mock-acc-001"]``.

    Returns:
        A ``MockBankConnectorDoc`` instance.
    """
    if account_ids is None:
        account_ids = ["mock-acc-001"]
    mappings = [make_mock_mapping(provider_account_id=aid, bank_account=f"BA-{i:03d}")
                for i, aid in enumerate(account_ids, start=1)]
    return MockBankConnectorDoc(enabled=enabled, mappings=mappings)


# ---------------------------------------------------------------------------
# Parameterized ID sets for dedup tests
# ---------------------------------------------------------------------------

EXISTING_IDS_NONE: set[str] = set()
"""No existing transaction IDs — every transaction is new."""

EXISTING_IDS_SOME: set[str] = {"mock-txn-1-1", "mock-txn-1-2"}
"""A few existing IDs — partial overlap."""

EXISTING_IDS_ALL: set[str] = {
    f"mock-txn-{p}-{i}"
    for p in range(1, 4)
    for i in range(1, 11)
}
"""All 30 mock transaction IDs — complete overlap."""


# ---------------------------------------------------------------------------
# Patch context manager
# ---------------------------------------------------------------------------


def patch_import_dependencies(
    config: ConnectorConfig | None = None,
    provider: MockProvider | None = None,
    get_all_return: list[str] | None = None,
    existing_ids: set[str] | None = None,
    doc_side_effect: Any = None,
) -> dict[str, Any]:
    """Set up ALL Frappe mocks needed by the import service.

    Returns a dict of patcher references so callers can stop them.

    Args:
        config: Connector config to return from ``get_connector_config``.
            Defaults to a minimal mock config.
        provider: Mock provider instance.  Defaults to a fresh ``MockProvider()``.
        get_all_return: Return value for ``frappe.get_all`` (the dedup query).
            Defaults to an empty list.
        existing_ids: Transaction IDs to treat as already imported.
            If provided, ``frappe.get_all`` returns this as a list.
        doc_side_effect: Custom side effect for ``frappe.get_doc``.

    Returns:
        Dict with keys ``"patches"`` (list of active patchers).
    """
    if config is None:
        config = ConnectorConfig(provider_name="mock", api_base_url="https://api.example.com")
    if provider is None:
        provider = MockProvider()
    if get_all_return is None:
        if existing_ids is not None:
            get_all_return = list(existing_ids)
        else:
            get_all_return = []

    frappe_patcher_doc = patch(
        "erpnext_bank_import.services.import_service.frappe.get_doc",
    )

    def _default_doc_side_effect(doctype: str, docname: str = ""):
        if doctype == "Bank Connector":
            return MockBankConnectorDoc()
        doc = MagicMock()
        doc.name = "test-run-log-001"
        doc.insert = MagicMock()
        doc.submit = MagicMock()
        return doc

    if doc_side_effect is None:
        doc_side_effect = _default_doc_side_effect

    m_frappe_doc = frappe_patcher_doc.start()
    m_frappe_doc.side_effect = doc_side_effect

    patches = [
        patch(
            "erpnext_bank_import.services.import_service.get_connector_config",
            return_value=config,
        ),
        patch(
            "erpnext_bank_import.services.import_service.get_connector",
            return_value=provider,
        ),
        patch("erpnext_bank_import.services.import_service.frappe.get_all", return_value=get_all_return),
        patch(
            "erpnext_bank_import.services.import_service.frappe.get_cached_value",
            return_value="_Test Company",
        ),
        patch("erpnext_bank_import.services.import_service.frappe.log_error"),
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

    return {
        "patches": patches + [frappe_patcher_doc],
    }


def unpatches(mocks: dict[str, Any]) -> None:
    """Stop all patches started by ``patch_import_dependencies``."""
    for p in mocks.get("patches", []):
        p.stop()


__all__ = [
    "EXISTING_IDS_ALL",
    "EXISTING_IDS_NONE",
    "EXISTING_IDS_SOME",
    "MockBankConnectorDoc",
    "MockMapping",
    "make_mock_connector_doc",
    "make_mock_mapping",
    "patch_import_dependencies",
    "unpatches",
]
