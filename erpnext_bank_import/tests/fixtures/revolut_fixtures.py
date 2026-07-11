"""Reusable mock Revolut API response fixtures.

Provides deterministic fixture data matching real Revolut Business API
v1 response shapes for use in pytest-based tests.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Mock account responses
# ---------------------------------------------------------------------------

MOCK_ACCOUNTS: list[dict[str, Any]] = [
	{
		"id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
		"name": "Current GBP Account",
		"balance": 3171.89,
		"currency": "GBP",
		"state": "active",
		"public": False,
		"created_at": "2022-08-05T14:29:22.215785Z",
		"updated_at": "2022-08-05T14:29:22.215785Z",
	},
	{
		"id": "bssc67d3-5afd-42c2-bece-3d28nlmo894d",
		"name": "International EUR Account",
		"balance": 411561.89,
		"currency": "EUR",
		"state": "active",
		"public": True,
		"created_at": "2022-08-25T14:29:22.215785Z",
		"updated_at": "2022-08-30T14:29:22.215785Z",
	},
	{
		"id": "inactive-acc-001",
		"name": "Closed Account",
		"balance": 0.0,
		"currency": "USD",
		"state": "closed",
		"public": False,
		"created_at": "2022-01-01T00:00:00Z",
		"updated_at": "2022-06-01T00:00:00Z",
	},
]

# ---------------------------------------------------------------------------
# Mock bank-details responses
# ---------------------------------------------------------------------------

MOCK_BANK_DETAILS: dict[str, list[dict[str, Any]]] = {
	"b7ec67d3-5af1-42c8-bece-3d28nlmo894d": [
		{
			"iban": "GB66REVO00996995908888",
			"bic": "REVOGB21",
			"account_no": "12345678",
			"sort_code": "54-01-05",
			"beneficiary": "Current GBP Account",
			"bank_country": "GB",
			"schemes": ["swift"],
		}
	],
	"bssc67d3-5afd-42c2-bece-3d28nlmo894d": [
		{
			"iban": "IE12REVO00996995909999",
			"bic": "REVOIE22",
			"account_no": "87654321",
			"beneficiary": "International EUR Account",
			"bank_country": "IE",
			"schemes": ["sepa"],
		}
	],
}

# ---------------------------------------------------------------------------
# Mock transaction responses
# ---------------------------------------------------------------------------

MOCK_TRANSACTIONS_TRANSFER: dict[str, Any] = {
	"id": "630f9890-95e3-add1-be4a-95f126988221",
	"type": "transfer",
	"state": "completed",
	"request_id": "invoice00912345",
	"created_at": "2024-08-31T17:21:20.364171Z",
	"updated_at": "2024-08-31T17:21:20.364171Z",
	"completed_at": "2024-08-31T17:21:20.364171Z",
	"reference": "invoice00912345",
	"legs": [
		{
			"leg_id": "630f9890-95e3-add1-0000-95f1269f0000",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"counterparty": {
				"name": "Acme Corp",
				"account_id": "e0af9f24-504c-4c5d-bd1d-07edf9f49876",
			},
			"amount": 1500.00,
			"currency": "GBP",
			"description": "To Acme Corp",
			"balance": 4671.89,
		}
	],
}

MOCK_TRANSACTIONS_CARD_PAYMENT: dict[str, Any] = {
	"id": "640c2b97-aaaa-1234-aaaa-c47a165c2e7e",
	"type": "card_payment",
	"state": "completed",
	"request_id": "REVP:8988a9a0-aaaa-1234-aaaa-5bcc66d938bf",
	"created_at": "2024-03-11T07:19:51.302559Z",
	"updated_at": "2024-03-12T02:13:36.842322Z",
	"completed_at": "2024-03-12T02:13:36.836595Z",
	"merchant": {
		"name": "Supermarket Ltd",
		"city": "London",
		"category_code": "5411",
		"country": "GBR",
	},
	"reference": "CARD-12345",
	"legs": [
		{
			"leg_id": "640c2b97-aaaa-1234-aaaa-c47a165c2e7e",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"amount": -47.80,
			"fee": 0.66,
			"currency": "GBP",
			"description": "Supermarket Ltd 1234",
			"balance": 3624.09,
		}
	],
	"card": {
		"id": "2b1a31bc-4795-4f95-b939-ae9cf911dc6e",
		"card_number": "516760******1234",
		"first_name": "John",
		"last_name": "Smith",
	},
}


MOCK_TRANSACTIONS_ATM: dict[str, Any] = {
	"id": "750d4e1f-1234-5678-90ab-cdef12345678",
	"type": "atm",
	"state": "completed",
	"created_at": "2024-06-15T09:30:00.000000Z",
	"updated_at": "2024-06-15T09:30:00.000000Z",
	"completed_at": "2024-06-15T09:30:00.000000Z",
	"legs": [
		{
			"leg_id": "750d4e1f-1234-5678-90ab-cdef12345678",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"amount": -200.00,
			"fee": 3.50,
			"currency": "GBP",
			"description": "ATM withdrawal London",
			"balance": 3424.09,
		}
	],
}

MOCK_TRANSACTIONS_FEE: dict[str, Any] = {
	"id": "860f5a2b-2345-6789-0abc-def123456789",
	"type": "fee",
	"state": "completed",
	"created_at": "2024-07-01T00:00:00.000000Z",
	"updated_at": "2024-07-01T00:00:00.000000Z",
	"completed_at": "2024-07-01T00:00:00.000000Z",
	"reference": "Monthly fee",
	"legs": [
		{
			"leg_id": "860f5a2b-2345-6789-0abc-def123456789",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"amount": -15.99,
			"currency": "GBP",
			"description": "Monthly account fee",
			"balance": 3408.10,
		}
	],
}

MOCK_TRANSACTIONS_EXCHANGE: dict[str, Any] = {
	"id": "970f6b3c-3456-7890-abcd-ef0123456789",
	"type": "exchange",
	"state": "completed",
	"created_at": "2024-07-15T12:08:07.833414Z",
	"updated_at": "2024-07-15T12:08:07.833414Z",
	"completed_at": "2024-07-15T12:08:07.833705Z",
	"reference": "Currency exchange",
	"legs": [
		{
			"leg_id": "970f6b3c-3456-7890-abcd-ef0123456789",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"amount": -1000.00,
			"currency": "GBP",
			"description": "Exchanged to EUR",
			"balance": 2408.10,
		},
		{
			"leg_id": "970f6b3c-3456-7890-abcd-ef012345678a",
			"account_id": "bssc67d3-5afd-42c2-bece-3d28nlmo894d",
			"amount": 1165.50,
			"fee": 2.50,
			"currency": "EUR",
			"description": "Exchanged from GBP",
			"balance": 412727.39,
		},
	],
}

MOCK_TRANSACTIONS_INCOMING: dict[str, Any] = {
	"id": "a81f7c4d-4567-890a-bcde-f01234567890",
	"type": "transfer",
	"state": "completed",
	"created_at": "2024-08-01T10:00:00.000000Z",
	"updated_at": "2024-08-01T10:00:00.000000Z",
	"completed_at": "2024-08-01T10:00:00.000000Z",
	"reference": "Salary Aug",
	"legs": [
		{
			"leg_id": "a81f7c4d-4567-890a-bcde-f01234567890",
			"account_id": "bssc67d3-5afd-42c2-bece-3d28nlmo894d",
			"counterparty": {
				"name": "Employer Inc",
				"account_id": "counterparty-acc-001",
			},
			"amount": 3200.00,
			"currency": "EUR",
			"description": "Monthly salary",
			"balance": 415927.39,
		}
	],
}

MOCK_TRANSACTIONS_WITHOUT_COUNTERPARTY: dict[str, Any] = {
	"id": "b92f8d5e-5678-901a-bcde-f12345678901",
	"type": "transfer",
	"state": "completed",
	"created_at": "2024-08-15T14:00:00.000000Z",
	"updated_at": "2024-08-15T14:00:00.000000Z",
	"completed_at": "2024-08-15T14:00:00.000000Z",
	"legs": [
		{
			"leg_id": "b92f8d5e-5678-901a-bcde-f12345678901",
			"account_id": "b7ec67d3-5af1-42c8-bece-3d28nlmo894d",
			"amount": 500.00,
			"currency": "GBP",
			"description": "Internal transfer",
			"balance": 2908.10,
		}
	],
}

# ---------------------------------------------------------------------------
# Paginated response helpers
# ---------------------------------------------------------------------------


def make_transaction_page(
	transactions: list[dict[str, Any]],
	count: int = 100,
) -> tuple[list[dict[str, Any]], str | None]:
	"""Build a paginated transaction response.

	Args:
	    transactions: Full list of transactions.
	    count: Page size.

	Returns:
	    A tuple ``(page_items, next_cursor)``.
	"""
	if not transactions:
		return [], None

	page = transactions[:count]
	next_cursor: str | None = None
	if len(transactions) > count:
		next_cursor = page[-1].get("created_at")

	return page, next_cursor


# ---------------------------------------------------------------------------
# Mock error responses
# ---------------------------------------------------------------------------

MOCK_ERROR_401: dict[str, str] = {
	"message": "The request should be authorized.",
}

MOCK_ERROR_429: dict[str, str] = {
	"message": "Too many requests.",
}

MOCK_ERROR_500: dict[str, str] = {
	"message": "Internal Server Error.",
}


__all__ = [
	"MOCK_ACCOUNTS",
	"MOCK_BANK_DETAILS",
	"MOCK_ERROR_401",
	"MOCK_ERROR_429",
	"MOCK_ERROR_500",
	"MOCK_TRANSACTIONS_ATM",
	"MOCK_TRANSACTIONS_CARD_PAYMENT",
	"MOCK_TRANSACTIONS_EXCHANGE",
	"MOCK_TRANSACTIONS_FEE",
	"MOCK_TRANSACTIONS_INCOMING",
	"MOCK_TRANSACTIONS_TRANSFER",
	"MOCK_TRANSACTIONS_WITHOUT_COUNTERPARTY",
	"make_transaction_page",
]
