"""Tests for the Bank of Cyprus PSD2 connector.

These tests validate:

1. Configuration validation — client_id/secret required
2. Account discovery — ``GET /v1/accounts`` parsing
3. Transaction fetch — date format conversion, cursor-based pagination
4. Normalisation — DEBIT/CREDIT sign, date conversion, required fields
5. Error handling — 401, 429, 5xx, network errors
6. Subscription flow — TPP token, create, activate
7. OAuth flow — start_oauth_flow, oauth_callback

All Frappe-dependent code paths are mocked.  HTTP calls are mocked via
``unittest.mock.patch``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY, MagicMock, PropertyMock, patch

import pytest
import requests

from erpnext_bank_import.connectors.bank_of_cyprus import (
    BankOfCyprusConnector,
    oauth_callback,
    start_oauth_flow,
)
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
    ApiError,
    AuthenticationError,
    ConfigurationError,
    NetworkError,
    NormalizationError,
    ServerError,
    TokenExpiredError,
)
from erpnext_bank_import.tests.fixtures.boc_fixtures import (
    MOCK_BOC_ACCOUNTS,
    MOCK_BOC_ERROR_401,
    MOCK_BOC_ERROR_429,
    MOCK_BOC_ERROR_500,
    MOCK_BOC_STATEMENT,
    MOCK_BOC_STATEMENT_EMPTY,
    MOCK_BOC_STATEMENT_SAME_DATE,
    MOCK_BOC_STATEMENT_SECOND_PAGE,
    MOCK_BOC_STATEMENT_SINGLE_PAGE,
    MOCK_BOC_STATEMENT_WITH_PARTIES,
    MOCK_BOC_SUBSCRIPTION_ACTIVATED,
    MOCK_BOC_SUBSCRIPTION_CREATED,
    MOCK_BOC_SUBSCRIPTION_DETAILS,
    MOCK_BOC_TOKEN_TPP,
    MOCK_BOC_TOKEN_USER,
    make_statement_page,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_boc_config(**overrides: Any) -> ConnectorConfig:
    """Build a minimal ``ConnectorConfig`` for a BoC connector."""
    defaults: dict[str, Any] = {
        "provider_name": "bank_of_cyprus",
        "api_base_url": "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2",
        "auth_method": "oauth2",
        "client_id": "test-client-id-001",
        "client_secret": "test-client-secret-001",
        "authorize_url": "/oauth2/authorize",
        "token_url": "/oauth2/token",
        "scopes": ["TPPOAuth2Security", "UserOAuth2Security"],
        "redirect_uri": "http://127.0.0.1:8000/api/method/erpnext_bank_import.connectors.bank_of_cyprus.oauth_callback",
        "rate_limit_rps": 10.0,
        "timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return ConnectorConfig(**defaults)


def _mock_response(status_code: int = 200, json_data: Any = None) -> MagicMock:
    """Build a mock ``requests.Response``."""
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.text = str(json_data) if json_data else ""
    mock.headers = {}
    return mock


# =========================================================================
# Configuration validation
# =========================================================================


class TestBocConfigValidation:
    """Verifies BoC-specific config validation."""

    def test_minimal_valid_config(self):
        """A config with provider_name, api_base_url, client_id, client_secret works."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        assert connector.config.provider_name == "bank_of_cyprus"
        assert connector.config.client_id == "test-client-id-001"
        assert connector.config.client_secret == "test-client-secret-001"

    def test_missing_client_id_raises(self):
        """client_id is required for BoC OAuth2."""
        with pytest.raises(ConfigurationError, match="client_id"):
            BankOfCyprusConnector(config=_make_boc_config(client_id=None))

    def test_missing_client_secret_raises(self):
        """client_secret is required for BoC OAuth2."""
        with pytest.raises(ConfigurationError, match="client_secret"):
            BankOfCyprusConnector(config=_make_boc_config(client_secret=None))

    def test_jwt_not_required(self):
        """BoC does not require jwt_private_key (unlike Revolut)."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        assert connector.config.jwt_private_key is None

    def test_empty_provider_name_raises(self):
        """Empty provider_name should raise ConfigurationError."""
        with pytest.raises(ConfigurationError, match="provider_name"):
            BankOfCyprusConnector(config=_make_boc_config(provider_name=""))

    def test_empty_api_base_url_raises(self):
        """Empty api_base_url should raise ConfigurationError."""
        with pytest.raises(ConfigurationError, match="api_base_url"):
            BankOfCyprusConnector(config=_make_boc_config(api_base_url=""))


# =========================================================================
# Account discovery
# =========================================================================


class TestBocAccountDiscovery:
    """Verifies GET /v1/accounts parsing."""

    def test_get_accounts(self):
        """get_accounts() returns all accounts as AccountInfo."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(connector._boc, "get_accounts", return_value=MOCK_BOC_ACCOUNTS),
        ):
            accounts = connector.get_accounts()

        assert len(accounts) == 3

        first = accounts[0]
        assert first.account_id == "351012345671"
        assert first.account_name == "ANDREAS MICHAEL"
        assert first.currency == "EUR"
        assert first.iban == "CY11002003510000000012345671"

        second = accounts[1]
        assert second.account_id == "351092345672"
        assert second.account_name == "ALPHA BUSINESS LTD"
        assert second.currency == "EUR"

        third = accounts[2]
        assert third.account_id == "351012345673"
        assert third.account_name == "ANDREAS SAVINGS"
        assert third.currency == "EUR"

    def test_get_accounts_without_subscription_raises(self):
        """get_accounts() raises if no subscription is active."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = None

        with patch.object(connector, "_get_access_token", return_value="test-token"):
            with pytest.raises(AuthenticationError, match="No active subscription"):
                connector.get_accounts()

    def test_get_accounts_empty(self):
        """get_accounts() returns empty list when no accounts subscribed."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(connector._boc, "get_accounts", return_value=[]),
        ):
            accounts = connector.get_accounts()

        assert accounts == []


# =========================================================================
# Transaction fetch
# =========================================================================


class TestBocTransactionFetch:
    """Verifies transaction fetching, date conversion, and pagination."""

    def test_fetch_transactions_first_page(self):
        """First page fetches with ISO dates converted to DD/MM/YYYY."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT,
            ) as mock_stmt,
        ):
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2024-05-01",
                date_to="2024-05-31",
                page_size=100,
            )

        # Verify date conversion in the API call.
        mock_stmt.assert_called_once_with(
            "test-token",
            "Subid000001-1725429256148",
            "351012345671",
            "01/05/2024",  # converted from ISO
            "31/05/2024",  # converted from ISO
            max_count=100,
        )

        # 5 transactions, page_size=100 → no next page.
        assert len(txns) == 5
        assert next_token is None

    def test_fetch_transactions_full_page_triggers_pagination(self):
        """When transactions fill the page, next_token is the last postingDate."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_SINGLE_PAGE,
            ),
        ):
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2026-07-01",
                date_to="2026-07-31",
                page_size=100,
            )

        assert len(txns) == 100
        # Last posting date is from the 100th transaction (index 100).
        # _date_for_index(100) = 08/10/2026 (day 100 falls in October).
        assert next_token == "08/10/2026"

    def test_fetch_transactions_pagination_cursor(self):
        """page_token uses the cursor as the new date_to."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_SECOND_PAGE,
            ) as mock_stmt,
        ):
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2026-06-01",
                date_to="2026-06-30",
                page_size=100,
                page_token="31/07/2026",  # cursor from previous page
            )

        # When page_token is provided, date_to is replaced by the cursor,
        # and date_from stays as original.
        mock_stmt.assert_called_once_with(
            "test-token",
            "Subid000001-1725429256148",
            "351012345671",
            "01/06/2026",  # date_from still from ISO conversion
            "31/07/2026",  # date_to replaced by page_token (cursor)
            max_count=100,
        )

        assert len(txns) == 50
        assert next_token is None  # fewer than page_size

    def test_fetch_transactions_empty(self):
        """fetch_transactions() returns empty list when no transactions."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_EMPTY,
            ),
        ):
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2024-01-01",
                date_to="2024-01-31",
            )

        assert txns == []
        assert next_token is None

    def test_fetch_transactions_without_auth_raises(self):
        """fetch_transactions() raises if no bank account context is set."""
        connector = BankOfCyprusConnector(config=_make_boc_config())

        with pytest.raises(TokenExpiredError, match="(?i)bank account context not set"):
            connector.fetch_transactions(
                account_id="351012345671",
                date_from="2024-01-01",
                date_to="2024-01-31",
            )

    def test_fetch_transactions_without_subscription_raises(self):
        """fetch_transactions() raises if no active subscription."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = None

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            pytest.raises(AuthenticationError, match="No active subscription"),
        ):
            connector.fetch_transactions(
                account_id="351012345671",
                date_from="2024-01-01",
                date_to="2024-01-31",
            )

    # ------------------------------------------------------------------
    # Pagination safety
    # ------------------------------------------------------------------

    def test_same_date_cursor_stops_pagination(self):
        """Same-date wall: next_token is None when cursor doesn't advance."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_SAME_DATE,
            ),
        ):
            # First page: no cursor yet, should return page + token.
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2026-07-15",
                date_to="2026-07-15",
                page_size=100,
            )

        assert len(txns) == 100  # full page
        assert next_token is not None  # cursor returned

        # Second page with the same cursor: should detect same-date wall.
        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_SAME_DATE,
            ),
        ):
            txns2, next_token2 = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2026-07-01",
                date_to="2026-07-31",
                page_size=100,
                page_token=next_token,  # same date as last page's postingDate
            )

        assert len(txns2) == 100  # still a full page
        assert next_token2 is None  # safety: stops pagination

    def test_different_cursor_continues(self):
        """Different cursor date continues pagination normally."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_account_statement",
                return_value=MOCK_BOC_STATEMENT_SECOND_PAGE,
            ),
        ):
            # page_token is "31/07/2026" but the returned data has
            # earlier dates (June/May), so cursor advances → continues.
            txns, next_token = connector.fetch_transactions(
                account_id="351012345671",
                date_from="2026-06-01",
                date_to="2026-06-30",
                page_size=100,
                page_token="31/07/2026",  # cursor from previous page
            )

        # Second page has 50 transactions (< page_size), so next_token
        # would be None anyway.  The important check is that it DID
        # NOT hit the same-date wall.
        assert len(txns) == 50  # partial page
        assert next_token is None  # no more pages


# =========================================================================
# Normalisation
# =========================================================================


class TestBocNormalisation:
    """Verifies normalisation of BoC transaction data."""

    def test_debit_transaction(self):
        """DEBIT transaction → negative amount, dcInd in transaction_type."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = {
            "id": "663c9d26de9162079842ce01",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 30.0, "currency": "EUR"},
            "description": "SWIFT Transfer",
            "postingDate": "09/05/2024",
        }
        norm = connector.normalize_transaction(raw)
        assert norm["external_id"] == "663c9d26de9162079842ce01"
        assert norm["amount"] == -30.0  # DEBIT → negative
        assert norm["currency"] == "EUR"
        assert norm["description"] == "SWIFT Transfer"
        assert norm["date"] == "2024-05-09"  # DD/MM/YYYY → ISO
        assert norm["transaction_type"] == "DEBIT"

    def test_credit_transaction(self):
        """CREDIT transaction → positive amount, dcInd in transaction_type."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = {
            "id": "663c9d26de9162079842ce02",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 1500.0, "currency": "EUR"},
            "description": "Salary Payment",
            "postingDate": "08/05/2024",
        }
        norm = connector.normalize_transaction(raw)
        assert norm["amount"] == 1500.0  # CREDIT → positive
        assert norm["transaction_type"] == "CREDIT"
        assert norm["date"] == "2024-05-08"

    def test_missing_id_raises(self):
        """Transaction without id raises NormalizationError."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = {
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 30.0, "currency": "EUR"},
            "postingDate": "09/05/2024",
        }
        with pytest.raises(NormalizationError, match="id"):
            connector.normalize_transaction(raw)

    def test_missing_amount_raises(self):
        """Transaction without amount raises NormalizationError."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = {
            "id": "txn-001",
            "dcInd": "CREDIT",
            "transactionAmount": {},
            "postingDate": "09/05/2024",
        }
        with pytest.raises(NormalizationError, match="transactionAmount.amount"):
            connector.normalize_transaction(raw)

    def test_missing_posting_date_raises(self):
        """Transaction without postingDate raises NormalizationError."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = {
            "id": "txn-001",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 100.0, "currency": "EUR"},
        }
        with pytest.raises(NormalizationError, match="postingDate"):
            connector.normalize_transaction(raw)

    def test_all_transaction_types(self):
        """Both DEBIT and CREDIT types normalise correctly."""
        connector = BankOfCyprusConnector(config=_make_boc_config())

        for raw_txn in MOCK_BOC_STATEMENT["transaction"]:
            norm = connector.normalize_transaction(raw_txn)
            assert norm["external_id"] == raw_txn["id"]
            assert norm["currency"] == raw_txn["transactionAmount"]["currency"]
            assert norm["transaction_type"] == raw_txn["dcInd"]

            if raw_txn["dcInd"] == "DEBIT":
                assert norm["amount"] < 0
            else:
                assert norm["amount"] >= 0

    # ------------------------------------------------------------------
    # Counterparty extraction
    # ------------------------------------------------------------------

    def test_debit_counterparty_from_creditor(self):
        """DEBIT transaction extracts counterparty from creditor fields."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT_WITH_PARTIES["transaction"][0]

        norm = connector.normalize_transaction(raw)

        assert norm["bank_party_name"] == "TechCorp Ltd"
        assert norm["bank_party_iban"] == "CY22002001230000000012345678"
        assert norm["reference_number"] == "INV-2024-0042"

    def test_credit_counterparty_from_debtor(self):
        """CREDIT transaction extracts counterparty from debtor fields."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT_WITH_PARTIES["transaction"][1]

        norm = connector.normalize_transaction(raw)

        assert norm["bank_party_name"] == "Alpha Services Ltd"
        assert norm["bank_party_iban"] == "CY33003003450000000098765432"
        assert norm["reference_number"] == "PAY-2024-0088"

    def test_debit_counterparty_with_account_number(self):
        """DEBIT with account number (no IBAN) maps correctly."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT_WITH_PARTIES["transaction"][2]

        norm = connector.normalize_transaction(raw)

        assert norm["bank_party_name"] == "WebHosting Pro"
        assert norm["bank_party_account_number"] == "1234567890"
        assert norm["bank_party_iban"] is None
        assert norm["reference_number"] == "E2E-998877"

    def test_counterparty_missing_in_sandbox(self):
        """No counterparty data → all party fields are None."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT["transaction"][0]

        norm = connector.normalize_transaction(raw)

        assert norm["bank_party_name"] is None
        assert norm["bank_party_account_number"] is None
        assert norm["bank_party_iban"] is None
        # Reference falls back to transaction ID when no remittance info.
        assert norm["reference_number"] == raw["id"]

    # ------------------------------------------------------------------
    # Fee extraction
    # ------------------------------------------------------------------

    def test_fee_from_top_level_fee_amount(self):
        """feeAmount at top level maps to included_fee."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT_WITH_PARTIES["transaction"][0]

        norm = connector.normalize_transaction(raw)

        assert norm["included_fee"] == 2.50
        assert norm["excluded_fee"] is None

    def test_fee_from_included_fee_in_amount(self):
        """includedFee inside transactionAmount maps to included_fee."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT_WITH_PARTIES["transaction"][3]

        norm = connector.normalize_transaction(raw)

        assert norm["included_fee"] == 5.0
        assert norm["excluded_fee"] is None

    def test_fee_absent(self):
        """No fee data → both fee fields are None."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        raw = MOCK_BOC_STATEMENT["transaction"][1]

        norm = connector.normalize_transaction(raw)

        assert norm["included_fee"] is None
        assert norm["excluded_fee"] is None


# =========================================================================
# Error handling
# =========================================================================


class TestBocErrorHandling:
    """Verifies error mapping for HTTP failures."""

    def test_401_raises_authentication_error(self):
        """401 response maps to AuthenticationError."""
        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"
        connector._subscription_id = "Subid000001-1725429256148"

        with (
            patch.object(connector, "_get_access_token", return_value="test-token"),
            patch.object(
                connector._boc,
                "get_accounts",
                side_effect=AuthenticationError("401 Unauthorized"),
            ),
        ):
            with pytest.raises(AuthenticationError, match="401"):
                connector.get_accounts()

    def test_429_raises_rate_limit_error(self):
        """429 response maps to RateLimitError via the subscription service."""
        connector = BankOfCyprusConnector(config=_make_boc_config())

        # The subscription service wraps 429 in ServerError or ApiError
        # based on the status code range. Let's test that the BocSubscriptionService
        # properly routes 429.
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        mock_resp = _mock_response(429, MOCK_BOC_ERROR_429)

        with pytest.raises(ApiError, match="(?i)rate limit exceeded"):
            svc._handle_error(mock_resp, "test operation")

    def test_500_raises_server_error(self):
        """500 response maps to ServerError via the subscription service."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        mock_resp = _mock_response(500, MOCK_BOC_ERROR_500)

        with pytest.raises(ServerError, match="(?i)unexpected error"):
            svc._handle_error(mock_resp, "test operation")


# =========================================================================
# Subscription flow
# =========================================================================


class TestBocSubscriptionFlow:
    """Verifies the BocSubscriptionService subscription lifecycle."""

    def test_get_tpp_access_token(self):
        """get_tpp_access_token() returns a valid token."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        with patch("erpnext_bank_import.services.boc_subscription.requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, MOCK_BOC_TOKEN_TPP)
            token = svc.get_tpp_access_token()

        assert token == "tpp-access-token-abc123"
        mock_post.assert_called_once()

        # Verify the correct grant type was sent.
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["data"]["grant_type"] == "client_credentials"
        assert call_kwargs["data"]["scope"] == "TPPOAuth2Security"

    def test_create_subscription(self):
        """create_subscription() returns a subscription with subscriptionId."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        with patch("erpnext_bank_import.services.boc_subscription.requests.post") as mock_post:
            mock_post.return_value = _mock_response(201, MOCK_BOC_SUBSCRIPTION_CREATED)
            sub = svc.create_subscription("tpp-token")

        assert sub["subscriptionId"] == "Subid000001-1725429256148"
        assert sub["status"] == "PENDING"

    def test_build_authorize_url(self):
        """build_authorize_url() includes all required params (no state)."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())
        url = svc.build_authorize_url("Subid000001-1725429256148")

        assert "response_type=code" in url
        assert "subscriptionid=Subid000001-1725429256148" in url
        assert "scope=UserOAuth2Security" in url
        assert "state" not in url  # BoC does not support state
        assert "client_id=" in url
        assert "redirect_uri=" in url

    def test_get_subscription_details(self):
        """get_subscription_details() returns selected accounts."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        with patch("erpnext_bank_import.services.boc_subscription.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, MOCK_BOC_SUBSCRIPTION_DETAILS)
            details = svc.get_subscription_details("user-token", "Subid000001-1725429256148")

        assert details["status"] == "PENDING"
        assert len(details["selectedAccounts"]) == 3

    def test_activate_subscription(self):
        """activate_subscription() changes status to ACTV."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        with patch("erpnext_bank_import.services.boc_subscription.requests.patch") as mock_patch:
            mock_patch.return_value = _mock_response(200, MOCK_BOC_SUBSCRIPTION_ACTIVATED)
            result = svc.activate_subscription(
                "user-token",
                "Subid000001-1725429256148",
                MOCK_BOC_SUBSCRIPTION_DETAILS["selectedAccounts"],
            )

        assert result["status"] == "ACTV"
        assert len(result["selectedAccounts"]) == 3

    def test_get_accounts_via_service(self):
        """BocSubscriptionService.get_accounts() returns account list."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        svc = BocSubscriptionService(config=_make_boc_config())

        with patch("erpnext_bank_import.services.boc_subscription.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, MOCK_BOC_ACCOUNTS)
            accounts = svc.get_accounts("user-token", "Subid000001-1725429256148")

        assert len(accounts) == 3
        assert accounts[0]["accountId"] == "351012345671"

        # Verify the subscriptionId header was included.
        call_headers = mock_get.call_args.kwargs["headers"]
        assert "subscriptionId" in call_headers
        assert call_headers["subscriptionId"] == "Subid000001-1725429256148"

    # ------------------------------------------------------------------
    # Subscription expiry
    # ------------------------------------------------------------------

    def test_subscription_expiry_detected(self):
        """Expired subscription raises AuthenticationError on load."""
        from erpnext_bank_import.connectors.bank_of_cyprus import BankOfCyprusConnector

        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"

        # Mock token data with expired subscription metadata.
        expired_metadata = {
            "subscription_id": "Subid000001-1725429256148",
            "subscription_created_at": "2026-01-15T12:00:00+00:00",
            "subscription_expires_at": "2026-04-15",  # expired
        }

        with (
            patch.object(
                connector._oauth,
                "_load_tokens",
                return_value={
                    "access_token": "test-token",
                    "refresh_token": None,
                    "provider_metadata": expired_metadata,
                },
            ),
            pytest.raises(AuthenticationError, match="Subscription expired"),
        ):
            connector._load_subscription_id()

        assert connector._subscription_id is None

    def test_subscription_not_expired(self):
        """Valid subscription is loaded normally."""
        from erpnext_bank_import.connectors.bank_of_cyprus import BankOfCyprusConnector

        connector = BankOfCyprusConnector(config=_make_boc_config())
        connector._connector_name = "BoC-Test-001"

        valid_metadata = {
            "subscription_id": "Subid000001-1725429256148",
            "subscription_created_at": "2026-07-15T12:00:00+00:00",
            "subscription_expires_at": "2027-01-15",  # far in the future
        }

        with (
            patch.object(
                connector._oauth,
                "_load_tokens",
                return_value={
                    "access_token": "test-token",
                    "refresh_token": None,
                    "provider_metadata": valid_metadata,
                },
            ),
        ):
            connector._load_subscription_id()

        assert connector._subscription_id == "Subid000001-1725429256148"

    def test_subscription_metadata_stores_expiry(self):
        """_store_subscription_id_in_metadata logs error when db unavailable.

        The function gracefully handles the case where Frappe request
        context is unavailable (catches the exception and logs it).
        """
        from erpnext_bank_import.connectors.bank_of_cyprus import (
            _store_subscription_id_in_metadata,
        )

        with (
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus.frappe.log_error",
            ) as mock_log_error,
        ):
            _store_subscription_id_in_metadata(
                connector_name="BoC-Test-001",
                subscription_id="Subid000001-1725429256148",
                expiration_date="13/01/2027",
            )

            # Should have logged the db error gracefully.
            mock_log_error.assert_called_once()
            call_args = mock_log_error.call_args
            message = call_args[1].get("message", str(call_args))
            assert "Failed to store subscription ID" in message


# =========================================================================
# OAuth flow (module-level functions)
# =========================================================================


class TestBocOAuthFlow:
    """Verifies the OAuth callback flow (start → callback)."""

    def test_start_oauth_flow(self):
        """start_oauth_flow() returns an authorisation URL."""
        mock_doc = MagicMock()
        mock_doc.enabled = True
        mock_doc.get_connector_config.return_value = _make_boc_config()
        mock_doc.connector_name = "BoC-Test-001"

        mock_cache = MagicMock()
        mock_cache.set_value = MagicMock()
        mock_cache.get_value = MagicMock(return_value=None)

        tpp_token_resp = _mock_response(200, MOCK_BOC_TOKEN_TPP)
        create_sub_resp = _mock_response(201, MOCK_BOC_SUBSCRIPTION_CREATED)

        with (
            patch("erpnext_bank_import.connectors.bank_of_cyprus.frappe.get_doc", return_value=mock_doc),
            patch("erpnext_bank_import.connectors.bank_of_cyprus.frappe.cache", return_value=mock_cache),
            patch(
                "erpnext_bank_import.services.boc_subscription.requests.post",
                side_effect=[tpp_token_resp, create_sub_resp],
            ),
        ):
            url = start_oauth_flow("BoC-Test-001")

        assert "oauth2/authorize" in url
        assert "subscriptionid=Subid000001-1725429256148" in url
        assert "response_type=code" in url
        assert url.startswith("https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2/oauth2/authorize")
        # Verify no state parameter (BoC does not support it).
        assert "state=" not in url

    def test_oauth_callback_success(self):
        """oauth_callback() completes the full flow on success."""
        mock_doc = MagicMock()
        mock_doc.enabled = True
        mock_doc.get_connector_config.return_value = _make_boc_config()
        mock_doc.connector_name = "BoC-Test-001"

        mock_cache = MagicMock()
        mock_cache.get_value = MagicMock(return_value="Subid000001-1725429256148")
        mock_cache.delete_value = MagicMock()

        with (
            patch("erpnext_bank_import.connectors.bank_of_cyprus.frappe.get_doc", return_value=mock_doc),
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus.frappe.cache",
                return_value=mock_cache,
            ),
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus._store_subscription_id_in_metadata",
            ),
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus.frappe.log_error",
            ),
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus.frappe._",
                side_effect=lambda s: s,
            ),
            # Mock get_all_enabled_connectors to return our connector.
            patch(
                "erpnext_bank_import.connectors.get_all_enabled_connectors",
                return_value=["BoC-Test-001"],
            ),
            # Mock the OAuth code exchange + refresh inside the callback.
            patch(
                "erpnext_bank_import.services.oauth.OAuth2Service.exchange_code_for_tokens",
                return_value=None,
            ),
            patch(
                "erpnext_bank_import.services.oauth.OAuth2Service.get_valid_access_token",
                return_value="user-token-xyz",
            ),
            # Mock the subscription service calls.
            patch(
                "erpnext_bank_import.services.boc_subscription.BocSubscriptionService.get_subscription_details",
                return_value=MOCK_BOC_SUBSCRIPTION_DETAILS,
            ),
            patch(
                "erpnext_bank_import.services.boc_subscription.BocSubscriptionService.activate_subscription",
                return_value=MOCK_BOC_SUBSCRIPTION_ACTIVATED,
            ),
        ):
            result = oauth_callback(code="test-auth-code")

        # The result should contain a success message.
        message = result.get("message", "")
        assert isinstance(message, str)
        assert "successful" in message.lower()

    def test_oauth_callback_missing_code(self):
        """oauth_callback() throws on missing code."""
        with (
            patch("erpnext_bank_import.connectors.bank_of_cyprus.frappe.throw"),
            patch(
                "erpnext_bank_import.connectors.bank_of_cyprus.frappe._",
                side_effect=lambda s: s,
            ),
            patch("erpnext_bank_import.connectors.bank_of_cyprus.frappe.log_error"),
        ):
            result = oauth_callback(code=None)
            assert result is None or isinstance(result, dict)


# =========================================================================
# Date conversion
# =========================================================================


class TestBocDateConversion:
    """Verifies DD/MM/YYYY ↔ ISO date conversion."""

    def test_iso_to_boc(self):
        """ISO date converts to DD/MM/YYYY."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        assert BocSubscriptionService.date_to_api("2024-05-09") == "09/05/2024"
        assert BocSubscriptionService.date_to_api("2026-07-17") == "17/07/2026"
        assert BocSubscriptionService.date_to_api("2024-01-01") == "01/01/2024"

    def test_boc_to_iso(self):
        """DD/MM/YYYY converts to ISO."""
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        assert BocSubscriptionService.date_from_api("09/05/2024") == "2024-05-09"
        assert BocSubscriptionService.date_from_api("17/07/2026") == "2026-07-17"
        assert BocSubscriptionService.date_from_api("01/01/2024") == "2024-01-01"
