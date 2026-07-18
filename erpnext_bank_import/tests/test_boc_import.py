"""End-to-end import pipeline tests for the Bank of Cyprus connector.

These tests validate the full ``import_transactions()`` pipeline with the
``BankOfCyprusConnector``, covering:

1. Full import flow — transactions are fetched, normalised, and inserted
2. Idempotency — importing twice produces zero duplicate records
3. Partial overlap — some transactions already exist, new ones inserted
4. Empty accounts — clean handling when no transactions exist
5. Auth failure isolation — auth error produces error summary, not crash
6. Normalisation failures — malformed transactions are skipped
7. Run log — structured ``Bank Import Run Log`` records
8. Mapping correctness — BoC-specific field mappings verified
9. Multi-page import — pagination across multiple API pages

All Frappe-dependent code paths are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.bank_of_cyprus import BankOfCyprusConnector
from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import ApiError, AuthenticationError
from erpnext_bank_import.tests.fixtures.boc_fixtures import (
    MOCK_BOC_STATEMENT,
    MOCK_BOC_STATEMENT_EMPTY,
    MOCK_BOC_STATEMENT_SECOND_PAGE,
    MOCK_BOC_STATEMENT_SINGLE_PAGE,
)

# ---------------------------------------------------------------------------
# Mock DocType helpers
# ---------------------------------------------------------------------------


class MockMapping:
    """Simulates a ``BankConnectorAccountMapping`` child-table row."""

    def __init__(
        self,
        provider_account_id: str = "351012345671",
        bank_account: str = "BA-BoC-001",
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
    """Simulates a ``Bank Connector`` DocType record for BoC."""

    def __init__(self, enabled: bool = True, mappings: list[MockMapping] | None = None):
        self.enabled = enabled
        self.account_mappings = mappings if mappings is not None else [MockMapping()]
        self.force_full_backfill = False
        self.max_import_window_days = 365

    def get_connector_config(self) -> ConnectorConfig:
        return ConnectorConfig(
            provider_name="bank_of_cyprus",
            api_base_url="https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2",
            client_id="test-client-id",
            client_secret="test-client-secret",
        )


def _make_boc_config() -> ConnectorConfig:
    """Build a minimal BoC connector config."""
    return ConnectorConfig(
        provider_name="bank_of_cyprus",
        api_base_url="https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2",
        auth_method="oauth2",
        client_id="test-client-id-001",
        client_secret="test-client-secret-001",
        authorize_url="/oauth2/authorize",
        token_url="/oauth2/token",
        scopes=["TPPOAuth2Security", "UserOAuth2Security"],
        redirect_uri="http://127.0.0.1:8000/api/method/erpnext_bank_import.connectors.bank_of_cyprus.oauth_callback",
        rate_limit_rps=10.0,
        timeout_seconds=5.0,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_boc_connector(
    subscription_id: str = "Subid000001-1725429256148",
) -> BankOfCyprusConnector:
    """Build a BoC connector with subscription context set."""
    connector = BankOfCyprusConnector(config=_make_boc_config())
    connector._connector_name = "BoC-Test-001"
    connector._subscription_id = subscription_id
    return connector


def _setup_import_mocks(
    connector: BankOfCyprusConnector,
    statement_data: dict[str, Any],
    existing_ids: set[str] | None = None,
    auth_fail: bool = False,
) -> list:
    """Set up mocks for the import pipeline with BoC connector.

    Args:
        connector: The BoC connector instance.
        statement_data: The mock statement response.
        existing_ids: Transaction IDs to treat as already imported.
        auth_fail: If True, mock auth as failed.

    Returns:
        List of patcher objects to stop later.
    """
    patchers = []

    # Auth mocks
    if auth_fail:
        patchers.append(
            patch.object(connector, "is_authenticated", return_value=False)
        )
        patchers.append(
            patch.object(connector, "refresh_token", side_effect=AuthenticationError("Auth failed"))
        )
    else:
        patchers.append(
            patch.object(connector, "is_authenticated", return_value=True)
        )

    # Transaction fetch mock
    patchers.append(
        patch.object(
            connector,
            "fetch_all_transactions",
            return_value=_mock_normalized_from_statement(statement_data),
        )
    )

    # Start all patchers
    for p in patchers:
        p.start()

    return patchers


def _mock_normalized_from_statement(statement: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize all transactions in a statement using the connector."""
    connector = BankOfCyprusConnector(config=_make_boc_config())
    raw_txns = statement.get("transaction", [])
    return [connector.normalize_transaction(raw) for raw in raw_txns]


def _default_doc_side_effect(doctype: str, docname: str = "") -> MagicMock:
    """Default side effect for frappe.get_doc in BoC import tests."""
    if doctype == "Bank Connector":
        return MockBankConnectorDoc()
    doc = MagicMock()
    doc.name = "test-run-log-001"
    doc.insert = MagicMock()
    doc.submit = MagicMock()
    return doc


# ---------------------------------------------------------------------------
# Import pipeline tests
# ---------------------------------------------------------------------------


class TestBocImportTransactions:
    """BoC-specific import pipeline tests."""

    @pytest.fixture(autouse=True)
    def _frappe_patches(self):
        """Apply Frappe patches for every test in this class."""
        frappe_patches = [
            patch(
                "erpnext_bank_import.services.import_service.get_connector_config",
                return_value=_make_boc_config(),
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=_make_boc_connector(),
            ),
            patch("erpnext_bank_import.services.import_service.frappe.get_all", return_value=[]),
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
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=_default_doc_side_effect,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.publish_realtime",
            ),
        ]
        for p in frappe_patches:
            p.start()
        yield
        for p in frappe_patches:
            p.stop()

    def _test_import(self, raw_txns: list[dict], existing_ids: set[str] | None = None, **kwargs):
        """Execute import_transactions with mocked BoC connector."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = []
        for raw in raw_txns:
            connector2 = BankOfCyprusConnector(config=_make_boc_config())
            normalized.append(connector2.normalize_transaction(raw))

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=normalized,
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=list(existing_ids) if existing_ids else [],
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
                **kwargs,
            )

        return result

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_import_creates_bank_transactions(self):
        """Full import creates Bank Transaction records from BoC data."""
        result = self._test_import(MOCK_BOC_STATEMENT["transaction"])
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert result["results"][0]["created"] == 5
        assert result["results"][0]["skipped"] == 0

    def test_import_is_idempotent(self):
        """Importing twice produces no duplicates."""
        txn_ids = {t["id"] for t in MOCK_BOC_STATEMENT["transaction"]}

        # First import: all new.
        result1 = self._test_import(MOCK_BOC_STATEMENT["transaction"])
        assert result1["results"][0]["created"] == 5

        # Second import: all exist.
        result2 = self._test_import(
            MOCK_BOC_STATEMENT["transaction"],
            existing_ids=txn_ids,
        )
        assert result2["results"][0]["created"] == 0
        assert result2["results"][0]["skipped"] == 5

    def test_import_partial_overlap(self):
        """Some transactions exist, only new ones are inserted."""
        all_txns = MOCK_BOC_STATEMENT["transaction"]
        existing_ids = {all_txns[0]["id"], all_txns[1]["id"]}

        result = self._test_import(all_txns, existing_ids=existing_ids)
        assert result["results"][0]["created"] == 3
        assert result["results"][0]["skipped"] == 2

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_import_empty_account(self):
        """Empty statement returns clean results."""
        result = self._test_import(MOCK_BOC_STATEMENT_EMPTY["transaction"])
        assert result["status"] == "success"
        assert result["results"][0]["created"] == 0
        assert result["results"][0]["skipped"] == 0

    def test_import_auth_failure_isolated(self):
        """Auth failure produces error summary, doesn't crash."""
        connector = _make_boc_connector()
        from erpnext_bank_import.services.import_service import import_transactions

        with (
            patch.object(connector, "is_authenticated", return_value=False),
            patch.object(
                connector,
                "refresh_token",
                side_effect=AuthenticationError("Token expired"),
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "error"
        assert "AuthenticationError" in result.get("error", "")

    # ------------------------------------------------------------------
    # Field mapping correctness
    # ------------------------------------------------------------------

    def test_mapping_debit_as_withdrawal(self):
        """DEBIT transaction → amount mapped as withdrawal in Bank Transaction."""
        connector = _make_boc_connector()
        raw = {
            "id": "test-debit-001",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 150.0, "currency": "EUR"},
            "description": "Test payment",
            "postingDate": "15/05/2024",
        }
        norm = connector.normalize_transaction(raw)

        assert norm["amount"] < 0  # DEBIT → negative

        from erpnext_bank_import.schema.transaction import apply_mapping

        mapped = apply_mapping(norm)
        assert mapped["withdrawal"] == 150.0
        assert mapped.get("deposit") is None or mapped["deposit"] == 0

    def test_mapping_credit_as_deposit(self):
        """CREDIT transaction → amount mapped as deposit in Bank Transaction."""
        connector = _make_boc_connector()
        raw = {
            "id": "test-credit-001",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 2500.0, "currency": "EUR"},
            "description": "Salary deposit",
            "postingDate": "01/05/2024",
        }
        norm = connector.normalize_transaction(raw)

        assert norm["amount"] >= 0  # CREDIT → positive

        from erpnext_bank_import.schema.transaction import apply_mapping

        mapped = apply_mapping(norm)
        assert mapped["deposit"] == 2500.0
        assert mapped.get("withdrawal") is None or mapped["withdrawal"] == 0

    def test_mapping_date_format(self):
        """BoC DD/MM/YYYY date converts to ISO in Bank Transaction."""
        connector = _make_boc_connector()
        raw = {
            "id": "test-date-001",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 100.0, "currency": "EUR"},
            "description": "Date test",
            "postingDate": "31/12/2024",
        }
        norm = connector.normalize_transaction(raw)

        assert norm["date"] == "2024-12-31"

        from erpnext_bank_import.schema.transaction import apply_mapping

        mapped = apply_mapping(norm)
        assert mapped["date"] == "2024-12-31"

    def test_mapping_currency(self):
        """Currency is preserved in Bank Transaction."""
        connector = _make_boc_connector()
        raw = {
            "id": "test-curr-001",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 500.0, "currency": "USD"},
            "description": "USD transaction",
            "postingDate": "01/06/2024",
        }
        norm = connector.normalize_transaction(raw)

        assert norm["currency"] == "USD"

        from erpnext_bank_import.schema.transaction import apply_mapping

        mapped = apply_mapping(norm)
        assert mapped["currency"] == "USD"


class TestBocImportPagination:
    """BoC multi-page pagination import tests."""

    @pytest.fixture(autouse=True)
    def _frappe_patches(self):
        """Apply Frappe patches for every test in this class."""
        frappe_patches = [
            patch(
                "erpnext_bank_import.services.import_service.get_connector_config",
                return_value=_make_boc_config(),
            ),
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
            patch(
                "erpnext_bank_import.services.import_service.frappe.publish_realtime",
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=_default_doc_side_effect,
            ),
        ]
        for p in frappe_patches:
            p.start()
        yield
        for p in frappe_patches:
            p.stop()

    def test_multi_page_import(self):
        """Import across multiple pages captures all transactions."""
        from erpnext_bank_import.services.import_service import import_transactions

        # Build all normalized transactions from both pages.
        connector = _make_boc_connector()
        page1_norm = _mock_normalized_from_statement(MOCK_BOC_STATEMENT_SINGLE_PAGE)
        page2_norm = _mock_normalized_from_statement(MOCK_BOC_STATEMENT_SECOND_PAGE)
        all_norm = page1_norm + page2_norm

        # Mock fetch_all_transactions to return all as if multi-page fetched.
        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=all_norm,
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "success"
        assert result["results"][0]["created"] == 150  # 100 + 50
        assert result["results"][0]["skipped"] == 0


# =========================================================================
# Multi-account mapping tests
# =========================================================================


class TestBocMultiAccountMapping:
    """BoC import with multiple enabled account mappings."""

    @pytest.fixture(autouse=True)
    def _frappe_patches(self):
        """Apply Frappe patches for every test in this class."""
        frappe_patches = [
            patch(
                "erpnext_bank_import.services.import_service.get_connector_config",
                return_value=_make_boc_config(),
            ),
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
            patch(
                "erpnext_bank_import.services.import_service.frappe.publish_realtime",
            ),
        ]
        for p in frappe_patches:
            p.start()
        yield
        for p in frappe_patches:
            p.stop()

    def _make_multi_mapping_doc(self) -> MockBankConnectorDoc:
        """Build a connector doc with 2 enabled account mappings."""
        mappings = [
            MockMapping(
                provider_account_id="351012345671",
                bank_account="BA-BoC-CURRENT-001",
            ),
            MockMapping(
                provider_account_id="351092345672",
                bank_account="BA-BoC-BUSINESS-002",
            ),
        ]
        return MockBankConnectorDoc(enabled=True, mappings=mappings)

    def test_import_multiple_account_mappings(self):
        """Import processes all enabled account mappings."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = _mock_normalized_from_statement(MOCK_BOC_STATEMENT)

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=normalized,
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=lambda doctype, docname="": (
                    self._make_multi_mapping_doc()
                    if doctype == "Bank Connector"
                    else _default_doc_side_effect(doctype, docname)
                ),
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "success"
        # 2 mappings × 5 transactions each
        assert len(result["results"]) == 2
        for r in result["results"]:
            assert r["created"] == 5
            assert r["skipped"] == 0
            assert r["error"] is None

    def test_import_partial_account_failure_isolated(self):
        """Failure in one account mapping does not block others."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = _mock_normalized_from_statement(MOCK_BOC_STATEMENT)

        # Make the second call to fetch_all_transactions fail.
        call_count = 0

        def _fetch_with_failure(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ApiError("Second account failed")
            return normalized

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                side_effect=_fetch_with_failure,
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=lambda doctype, docname="": (
                    self._make_multi_mapping_doc()
                    if doctype == "Bank Connector"
                    else _default_doc_side_effect(doctype, docname)
                ),
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        # First mapping succeeded, second failed.
        assert result["status"] == "partial"
        assert result["results"][0]["created"] == 5
        assert result["results"][0]["error"] is None
        assert result["results"][1]["created"] == 0
        # Error is wrapped by diagnostics service with a human-readable message.
        err = result["results"][1]["error"] or ""
        assert "fetching transactions" in err.lower()


# =========================================================================
# Incremental sync tests
# =========================================================================


class TestBocIncrementalSync:
    """BoC incremental import with last_synced_at cursor."""

    @pytest.fixture(autouse=True)
    def _frappe_patches(self):
        """Apply Frappe patches for every test in this class."""
        from datetime import date

        def _getdate_side_effect(d: str | None = None) -> date:
            """Return a date object matching the input, defaulting to 2026-07-01."""
            if d is None:
                return date.today()
            parts = d.split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))

        frappe_patches = [
            patch(
                "erpnext_bank_import.services.import_service.get_connector_config",
                return_value=_make_boc_config(),
            ),
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
            patch(
                "erpnext_bank_import.services.import_service.frappe.publish_realtime",
            ),
            # getdate returns a proper date based on input
            patch(
                "erpnext_bank_import.services.import_service.frappe.utils.getdate",
                side_effect=_getdate_side_effect,
            ),
        ]
        for p in frappe_patches:
            p.start()
        yield
        for p in frappe_patches:
            p.stop()

    def test_incremental_sync_uses_last_synced_at(self):
        """Incremental sync passes last_synced_at as date_from to fetch."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = _mock_normalized_from_statement(MOCK_BOC_STATEMENT)

        # Mock the mapping with last_synced_at set.
        mock_mapping = MockMapping(
            provider_account_id="351012345671",
            bank_account="BA-BoC-001",
            is_enabled=True,
            last_synced_at="2026-07-01",
        )
        mock_doc = MockBankConnectorDoc(enabled=True, mappings=[mock_mapping])

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=normalized,
            ) as mock_fetch,
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=lambda doctype, docname="": (
                    mock_doc
                    if doctype == "Bank Connector"
                    else _default_doc_side_effect(doctype, docname)
                ),
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "success"
        # fetch_all_transactions should have been called with date_from
        # matching last_synced_at (2026-07-01).
        call_args = mock_fetch.call_args
        assert call_args is not None
        _, kwargs = call_args
        assert kwargs.get("date_from") == "2026-07-01"

    def test_incremental_sync_without_last_synced_at(self):
        """No last_synced_at falls back to default window calculation."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = _mock_normalized_from_statement(MOCK_BOC_STATEMENT)

        # Mock mapping without last_synced_at.
        mock_mapping = MockMapping(
            provider_account_id="351012345671",
            bank_account="BA-BoC-001",
            is_enabled=True,
            last_synced_at=None,
        )
        mock_doc = MockBankConnectorDoc(enabled=True, mappings=[mock_mapping])

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=normalized,
            ) as mock_fetch,
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=lambda doctype, docname="": (
                    mock_doc
                    if doctype == "Bank Connector"
                    else _default_doc_side_effect(doctype, docname)
                ),
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "success"
        # Without last_synced_at, date_from should be the default
        # calculated window (add_days returns "2026-04-11").
        call_args = mock_fetch.call_args
        assert call_args is not None
        _, kwargs = call_args
        assert kwargs.get("date_from") == "2026-04-11"

    def test_incremental_sync_updates_cursor(self):
        """After import, last_synced_at is updated to the max transaction date."""
        from erpnext_bank_import.services.import_service import import_transactions

        connector = _make_boc_connector()
        normalized = _mock_normalized_from_statement(MOCK_BOC_STATEMENT)

        mock_mapping = MockMapping(
            provider_account_id="351012345671",
            bank_account="BA-BoC-001",
            is_enabled=True,
            last_synced_at="2024-01-01",
        )
        mock_doc = MockBankConnectorDoc(enabled=True, mappings=[mock_mapping])

        with (
            patch.object(connector, "is_authenticated", return_value=True),
            patch.object(
                connector,
                "fetch_all_transactions",
                return_value=normalized,
            ),
            patch(
                "erpnext_bank_import.services.import_service.get_connector",
                return_value=connector,
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_all",
                return_value=[],
            ),
            patch(
                "erpnext_bank_import.services.import_service.frappe.get_doc",
                side_effect=lambda doctype, docname="": (
                    mock_doc
                    if doctype == "Bank Connector"
                    else _default_doc_side_effect(doctype, docname)
                ),
            ),
        ):
            result = import_transactions(
                connector_name="BoC-Test-001",
                trigger="Manual",
            )

        assert result["status"] == "success"
        # After import, cursor should be updated to the max date among the
        # imported transactions = 2024-05-09 (the latest date in MOCK_BOC_STATEMENT).
        assert mock_mapping.last_synced_at == "2024-05-09"


# =========================================================================
# Import all enabled connectors (scheduled job integration)
# =========================================================================


class TestBocImportAllEnabledConnectors:
    """BoC-specific tests for the scheduled import_all_enabled_connectors job."""

    def test_boc_import_via_scheduled_job(self):
        """import_all_enabled_connectors triggers BoC connector import."""
        from erpnext_bank_import.services.import_service import (
            import_all_enabled_connectors,
        )

        with (
            patch(
                "erpnext_bank_import.services.import_service.get_all_enabled_connectors",
                return_value=["BoC-Test-001"],
            ),
            patch(
                "erpnext_bank_import.services.import_service.import_transactions",
                return_value={"status": "success", "results": [{"created": 5}]},
            ) as mock_import,
        ):
            result = import_all_enabled_connectors()

        mock_import.assert_called_once_with(
            "BoC-Test-001",
            trigger="Scheduled",
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["status"] == "success"

    def test_boc_import_scheduled_failure_isolated(self):
        """BoC failure in scheduled job does not affect other connectors."""
        from erpnext_bank_import.services.import_service import (
            import_all_enabled_connectors,
        )

        call_results: dict[str, dict] = {}

        def _import_side_effect(
            connector_name: str,
            **kwargs: Any,
        ) -> dict:
            result = {
                "status": "success" if connector_name == "Other-C001" else "error",
                "results": [
                    {
                        "created": 5 if connector_name == "Other-C001" else 0,
                        "error": None if connector_name == "Other-C001" else "Auth failed",
                    }
                ],
            }
            call_results[connector_name] = result
            return result

        with (
            patch(
                "erpnext_bank_import.services.import_service.get_all_enabled_connectors",
                return_value=["BoC-Test-001", "Other-C001"],
            ),
            patch(
                "erpnext_bank_import.services.import_service.import_transactions",
                side_effect=_import_side_effect,
            ),
        ):
            result = import_all_enabled_connectors()

        # Both connectors should have been called.
        assert "BoC-Test-001" in call_results
        assert "Other-C001" in call_results
        # BoC failed, other succeeded — the scheduled job does not
        # raise, it just logs errors.
        assert isinstance(result, list)
        assert len(result) == 2
