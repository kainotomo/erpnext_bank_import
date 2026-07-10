"""Mock bank provider — reference implementation of ``BankConnector``.

This module provides ``MockProvider``, a fully functional in-memory
implementation of the ``BankConnector`` ABC.  It returns deterministic
synthetic data and exercises every phase of the connector lifecycle:

* Authentication (with configurable failure for testing error paths)
* Account discovery (2-3 synthetic accounts)
* Paginated transaction fetch (configurable pages / page size)
* Normalisation (pass-through for valid dicts; error on malformed input)

Usage
-----
.. code-block:: python

    from erpnext_bank_import.connectors.mock_provider import MockProvider

    provider = MockProvider()
    provider.authenticate()
    accounts = provider.get_accounts()
    txns = provider.fetch_all_transactions(
        account_id=accounts[0].account_id,
        date_from="2026-01-01",
        date_to="2026-06-30",
    )

The mock is used in tests to validate the connector interface contract
and serves as a reference for real provider implementations.
"""

from __future__ import annotations

import copy
from typing import Any

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
    AuthenticationError,
    NormalizationError,
)
from erpnext_bank_import.schema.transaction import NormalizedTransaction

# ---------------------------------------------------------------------------
# Synthetic fixture data
# ---------------------------------------------------------------------------

_MOCK_ACCOUNTS: list[dict[str, Any]] = [
    {
        "account_id": "mock-acc-001",
        "account_name": "Business EUR Account",
        "currency": "EUR",
        "iban": "IE12ABCD12345678901234",
        "account_number": "12345678",
    },
    {
        "account_id": "mock-acc-002",
        "account_name": "Personal USD Account",
        "currency": "USD",
        "iban": None,
        "account_number": "87654321",
    },
    {
        "account_id": "mock-acc-003",
        "account_name": "Savings GBP Account",
        "currency": "GBP",
        "iban": "GB12ABCD1234567890",
        "account_number": "11223344",
    },
]

_TRANSACTION_TEMPLATES: list[dict[str, Any]] = [
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": 1500.00,
        "currency": "EUR",
        "description": "Invoice payment — Acme Corp",
        "reference_number": "INV-{page}-{idx}",
        "bank_party_name": "Acme Corp",
        "bank_party_account_number": "99887766",
        "bank_party_iban": "DE12ABCD9988776655",
        "transaction_type": "TRANSFER",
        "included_fee": None,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": -250.00,
        "currency": "EUR",
        "description": "Card purchase — Supermarket Ltd",
        "reference_number": None,
        "bank_party_name": "Supermarket Ltd",
        "bank_party_account_number": None,
        "bank_party_iban": None,
        "transaction_type": "CARD_PAYMENT",
        "included_fee": None,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": -89.99,
        "currency": "EUR",
        "description": "Subscription — Cloud Services Inc.",
        "reference_number": None,
        "bank_party_name": "Cloud Services Inc.",
        "bank_party_account_number": None,
        "bank_party_iban": None,
        "transaction_type": "CARD_PAYMENT",
        "included_fee": 0.50,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": 3200.00,
        "currency": "USD",
        "description": "Client payment — Global LLC",
        "reference_number": "WIRE-{page}-{idx}",
        "bank_party_name": "Global LLC",
        "bank_party_account_number": "55443322",
        "bank_party_iban": "US12ABCD5544332211",
        "transaction_type": "WIRE_TRANSFER",
        "included_fee": 15.00,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": -45.00,
        "currency": "GBP",
        "description": "ATM withdrawal",
        "reference_number": None,
        "bank_party_name": None,
        "bank_party_account_number": None,
        "bank_party_iban": None,
        "transaction_type": "ATM",
        "included_fee": 3.50,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": 780.00,
        "currency": "GBP",
        "description": "Refund — Insurance Co.",
        "reference_number": "RFND-{page}-{idx}",
        "bank_party_name": "Insurance Co.",
        "bank_party_account_number": "33221100",
        "bank_party_iban": "GB12ABCD3322110099",
        "transaction_type": "TRANSFER",
        "included_fee": None,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": -1200.00,
        "currency": "EUR",
        "description": "Office rent payment",
        "reference_number": "RENT-{page}-{idx}",
        "bank_party_name": "Property Mgmt Ltd",
        "bank_party_account_number": "77665544",
        "bank_party_iban": "FR12ABCD7766554433",
        "transaction_type": "TRANSFER",
        "included_fee": None,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": 55.50,
        "currency": "EUR",
        "description": "Interest payment",
        "reference_number": "INT-{page}-{idx}",
        "bank_party_name": "Bank",
        "bank_party_account_number": None,
        "bank_party_iban": None,
        "transaction_type": "INTEREST",
        "included_fee": None,
        "excluded_fee": None,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": -15.99,
        "currency": "EUR",
        "description": "Monthly service fee",
        "reference_number": None,
        "bank_party_name": "Bank",
        "bank_party_account_number": None,
        "bank_party_iban": None,
        "transaction_type": "FEE",
        "included_fee": None,
        "excluded_fee": 15.99,
    },
    {
        "external_id": "mock-txn-{page}-{idx}",
        "date": "2026-06-{day:02d}",
        "amount": 2000.00,
        "currency": "USD",
        "description": "Payroll deposit",
        "reference_number": "PAY-{page}-{idx}",
        "bank_party_name": "Employer Inc.",
        "bank_party_account_number": "11110000",
        "bank_party_iban": "US12ABCD1111000099",
        "transaction_type": "TRANSFER",
        "included_fee": None,
        "excluded_fee": None,
    },
]

_SUPPORTED_DATE_FMT = "%Y-%m-%d"


class MockProvider(BankConnector):
    """In-memory mock bank provider for testing and development.

    Configuration
    -------------
    Controlled via ``ConnectorConfig`` or constructor keyword arguments:

    * ``auth_should_fail`` (default ``False``) — if ``True``,
      ``authenticate()`` raises ``AuthenticationError``.
    * ``account_count`` (default ``3``) — number of synthetic accounts
      returned by ``get_accounts()``.
    * ``transactions_per_page`` (default ``10``) — number of
      transactions in each page.
    * ``total_pages`` (default ``3``) — how many pages of transactions
      are available.
    """

    def __init__(self, config: ConnectorConfig | None = None, **kwargs: Any) -> None:
        # Pop mock-specific parameters before passing to base.
        self._auth_should_fail = kwargs.pop("auth_should_fail", False)
        self._account_count = kwargs.pop("account_count", 3)
        self._transactions_per_page = kwargs.pop("transactions_per_page", 10)
        self._total_pages = kwargs.pop("total_pages", 3)

        # Provide a sensible default config so callers can do
        # ``MockProvider()`` without any arguments.
        if config is None and not kwargs:
            kwargs = {
                "provider_name": "mock",
                "api_base_url": "https://mock-bank.example.com/api",
                "auth_method": "oauth2",
            }

        super().__init__(config=config, **kwargs)

        self._authenticated: bool = False

    # ------------------------------------------------------------------
    # Auth lifecycle
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        if self._auth_should_fail:
            raise AuthenticationError("Mock authentication failed (auth_should_fail=True)")
        self._authenticated = True

    def is_authenticated(self) -> bool:
        return self._authenticated

    def refresh_token(self) -> None:
        if self._auth_should_fail:
            raise AuthenticationError("Mock token refresh failed (auth_should_fail=True)")
        self._authenticated = True

    # ------------------------------------------------------------------
    # Account discovery
    # ------------------------------------------------------------------

    def get_accounts(self) -> list[AccountInfo]:
        self._require_auth()
        count = min(self._account_count, len(_MOCK_ACCOUNTS))
        return [AccountInfo(**copy.deepcopy(_MOCK_ACCOUNTS[i])) for i in range(count)]

    # ------------------------------------------------------------------
    # Transaction fetch
    # ------------------------------------------------------------------

    def fetch_transactions(
        self,
        account_id: str,
        date_from: str,
        date_to: str,
        *,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> tuple[list[NormalizedTransaction], str | None]:
        self._require_auth()

        # Use page_token as the page number (1-based); default to 1.
        if page_token is None:
            page_num = 1
        else:
            page_num = int(page_token)

        # Simulate max pages.
        if page_num > self._total_pages:
            return [], None

        effective_size = min(page_size, self._transactions_per_page)
        txns: list[NormalizedTransaction] = []

        for idx in range(effective_size):
            template = copy.deepcopy(_TRANSACTION_TEMPLATES[idx % len(_TRANSACTION_TEMPLATES)])
            raw = self._render_template(template, page_num, idx + 1)
            txns.append(self.normalize_transaction(raw))

        next_token = str(page_num + 1) if page_num < self._total_pages else None
        return txns, next_token

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    def normalize_transaction(self, raw: dict[str, Any]) -> NormalizedTransaction:
        required = {"external_id", "date", "amount", "currency", "description"}
        missing = required - raw.keys()
        if missing:
            raise NormalizationError(
                f"Missing required fields: {sorted(missing)}"
            )

        # Validate types at a basic level.
        if not isinstance(raw["amount"], (int, float)):
            raise NormalizationError(
                f"amount must be numeric, got {type(raw['amount']).__name__}"
            )

        return NormalizedTransaction(
            external_id=str(raw["external_id"]),
            date=str(raw["date"]),
            amount=float(raw["amount"]),
            currency=str(raw["currency"]),
            description=str(raw["description"]),
            reference_number=str(raw["reference_number"]) if raw.get("reference_number") is not None else None,
            bank_party_name=str(raw["bank_party_name"]) if raw.get("bank_party_name") is not None else None,
            bank_party_account_number=str(raw["bank_party_account_number"]) if raw.get("bank_party_account_number") is not None else None,
            bank_party_iban=str(raw["bank_party_iban"]) if raw.get("bank_party_iban") is not None else None,
            transaction_type=str(raw["transaction_type"]) if raw.get("transaction_type") is not None else None,
            included_fee=float(raw["included_fee"]) if raw.get("included_fee") is not None else None,
            excluded_fee=float(raw["excluded_fee"]) if raw.get("excluded_fee") is not None else None,
            provider_metadata=raw.get("provider_metadata", {}),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_auth(self) -> None:
        if not self._authenticated:
            raise AuthenticationError("Not authenticated. Call authenticate() first.")

    @staticmethod
    def _render_template(template: dict[str, Any], page: int, idx: int) -> dict[str, Any]:
        """Render a fixture template with *page* and *idx* placeholders."""
        result = {}
        for key, value in template.items():
            if isinstance(value, str):
                value = value.format(page=page, idx=idx, day=(idx % 28) + 1)
            result[key] = value
        return result


__all__ = [
    "MockProvider",
]
