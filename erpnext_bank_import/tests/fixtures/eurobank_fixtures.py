"""Reusable mock Eurobank (Hellenic Bank) B2B API response fixtures.

Provides deterministic fixture data matching real Eurobank B2B API v2
response shapes for use in pytest-based tests.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Mock token response
# ---------------------------------------------------------------------------

MOCK_TOKEN_RESPONSE: dict[str, Any] = {
	"token_type": "Bearer",
	"access_token": "mock-access-token-00000000-0000-0000-0000-000000000000",
	"client_id": "mock-client-id-000000000000000000000000",
	"scope": [
		"v2.b2b.get.accounts",
		"v2.b2b.get.account.details",
		"v2.b2b.get.account.transactions",
	],
	"created_on": 1768294396503,
	"expires_at": 1768296208785,
	"refresh_token": "mock-refresh-token-00000000-0000-0000-0000-000000000000",
	"refresh_expires_at": 1783846396503,
}

# ---------------------------------------------------------------------------
# Mock account responses
# ---------------------------------------------------------------------------

MOCK_ACCOUNTS_RESPONSE: dict[str, Any] = {
	"payload": {
		"subscriberName": "TrustEdge Solutions",
		"numberOfRecords": 3,
		"accounts": [
			{
				"accountName": "TrustEdge Solutions",
				"accountType": "CURRENT_ACCOUNT",
				"accountTypeDescription": "CURRENT ACCOUNT",
				"iban": "CY59005000990000990145684237",
				"accountNumber": "0990145684237",
				"currency": "EUR",
				"balances": {
					"available": 7176789.38,
					"current": 7176789.38,
				},
				"status": "ACTIVE",
			},
			{
				"accountName": "TrustEdge Solutions",
				"accountType": "CURRENT_ACCOUNT",
				"accountTypeDescription": "FC CURRENT ACCOUNT",
				"iban": "CY19005000990001765925862102",
				"accountNumber": "1765925862102",
				"currency": "USD",
				"balances": {
					"available": 5032462.93,
					"current": 5032462.93,
				},
				"status": "ACTIVE",
			},
			{
				"accountName": "TrustEdge Solutions",
				"accountType": "CURRENT_ACCOUNT",
				"accountTypeDescription": "FC CURRENT ACCOUNT",
				"iban": "CY27005000990000916845862235",
				"accountNumber": "0916845862235",
				"currency": "GBP",
				"balances": {
					"available": 6744896.67,
					"current": 6744896.67,
				},
				"status": "ACTIVE",
			},
		],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 10,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_ACCOUNTS_RESPONSE_CLOSED_ACCOUNT: dict[str, Any] = {
	"payload": {
		"subscriberName": "TrustEdge Solutions",
		"numberOfRecords": 1,
		"accounts": [
			{
				"accountName": "Closed Account",
				"accountType": "CURRENT_ACCOUNT",
				"accountTypeDescription": "CURRENT ACCOUNT",
				"iban": "CY59005000990000990199999999",
				"accountNumber": "0990145699999",
				"currency": "EUR",
				"balances": {
					"available": 0.0,
					"current": 0.0,
				},
				"status": "CLOSED",
			},
		],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 10,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_EMPTY_ACCOUNTS_RESPONSE: dict[str, Any] = {
	"payload": {
		"subscriberName": "TrustEdge Solutions",
		"numberOfRecords": 0,
		"accounts": [],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 10,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

# ---------------------------------------------------------------------------
# Mock error responses
# ---------------------------------------------------------------------------

MOCK_ERROR_401: dict[str, Any] = {
	"payload": None,
	"errors": [
		{
			"message": None,
			"code": "TOKEN_EXPIRED",
			"params": [],
		}
	],
}

MOCK_ERROR_429: dict[str, Any] = {
	"payload": None,
	"errors": [
		{
			"message": None,
			"code": "TOO_MANY_REQUESTS",
			"params": [],
		}
	],
}

MOCK_ERROR_500: dict[str, Any] = {
	"payload": {
		"message": "The following internal errorCode occured.",
		"code": "INTERNAL_ERROR",
		"params": [],
	},
	"errors": None,
}

# ---------------------------------------------------------------------------
# Mock token error responses
# ---------------------------------------------------------------------------

MOCK_TOKEN_ERROR_INVALID_CODE: dict[str, Any] = {
	"error": "authorization_code_not_found",
	"error_description": None,
}

MOCK_TOKEN_ERROR_INVALID_CLIENT: dict[str, Any] = {
	"payload": None,
	"errors": [
		{
			"message": None,
			"code": "CLIENT_NOT_FOUND",
			"params": [],
		}
	],
}

# ---------------------------------------------------------------------------
# Mock transaction responses (B2B)
# ---------------------------------------------------------------------------

MOCK_TRANSACTIONS_RESPONSE: dict[str, Any] = {
	"payload": {
		"account": {
			"iban": "CY59005000990000990145684237",
			"accountNumber": "0990145684237",
			"currency": "EUR",
			"balances": [
				{
					"balanceType": "openingBooked",
					"balanceAmount": 7176789.38,
					"lastChangeDateTime": "10/01/2026 00:00",
				},
				{
					"balanceType": "closingBooked",
					"balanceAmount": 3640028.46,
					"lastChangeDateTime": "12/01/2026 00:00",
				},
			],
		},
		"numberOfRecords": 4,
		"transactions": [
			{
				"references": {
					"referenceId": "4be1d2a7c93f",
					"paymentOrderId": "SN58392017",
					"otherId": "HB905317462830",
				},
				"type": "TRANSFER",
				"description": "Port handling services",
				"submissionDate": "10/01/2026",
				"submissionTime": "10:45",
				"bookingDate": "10/01/2026",
				"valueDate": "10/01/2026",
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 72000.00,
				},
				"counterParty": {
					"name": "Mediterranean Port Services",
					"accountNumber": "FR7630006000011234567890189",
				},
				"balanceAfterTransaction": 3938028.46,
				"status": "COMPLETED",
			},
			{
				"references": {
					"referenceId": "c1a9e0b47f2d",
					"paymentOrderId": "TM10438572",
					"otherId": "HB905317462830",
				},
				"type": "TRANSFER",
				"description": "Supplier payment for fuel",
				"submissionDate": "11/01/2026",
				"submissionTime": "14:05",
				"bookingDate": "11/01/2026",
				"valueDate": "11/01/2026",
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 95000.00,
				},
				"counterParty": {
					"name": "Global Marine Fuels Ltd",
					"accountNumber": "12345678901",
				},
				"balanceAfterTransaction": 3843028.46,
				"status": "COMPLETED",
			},
			{
				"references": {
					"referenceId": "a9f3c1d4e7b2",
					"paymentOrderId": "SN58291034",
					"otherId": "HB430915768204",
				},
				"type": "TRANSFER",
				"description": "Bulk shipping invoice payment",
				"submissionDate": "12/01/2026",
				"submissionTime": "09:20",
				"bookingDate": "12/01/2026",
				"valueDate": "12/01/2026",
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 185000.00,
				},
				"counterParty": {
					"name": "Oceanic Freight Solutions",
					"accountNumber": "DE43876543210987654321",
				},
				"balanceAfterTransaction": 3640028.46,
				"status": "COMPLETED",
			},
			{
				"references": {
					"referenceId": "d4e5f6a7b8c9",
					"paymentOrderId": "SN73918462",
					"otherId": "HB482917305684",
				},
				"type": "TRANSFER",
				"description": "Client payment for services",
				"submissionDate": "15/01/2026",
				"submissionTime": "14:37",
				"bookingDate": "15/01/2026",
				"valueDate": "15/01/2026",
				"creditDebitIndicator": "CREDIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 25000.00,
				},
				"counterParty": {
					"name": "Atlas Logistics Ltd",
					"accountNumber": "GB29NWBK60161331926819",
				},
				"balanceAfterTransaction": 3665028.46,
				"status": "COMPLETED",
			},
		],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 100,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_TRANSACTIONS_RESPONSE_PAGE_2: dict[str, Any] = {
	"payload": {
		"account": {
			"iban": "CY59005000990000990145684237",
			"accountNumber": "0990145684237",
			"currency": "EUR",
		},
		"numberOfRecords": 2,
		"transactions": [
			{
				"references": {
					"referenceId": "e1f2g3h4i5j6",
				},
				"type": "TRANSFER",
				"description": "Second page transaction",
				"submissionDate": "16/01/2026",
				"submissionTime": "09:00",
				"bookingDate": "16/01/2026",
				"valueDate": "16/01/2026",
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 500.00,
				},
				"counterParty": {
					"name": "Test Vendor",
					"accountNumber": "12345",
				},
				"balanceAfterTransaction": 1000.00,
				"status": "COMPLETED",
			},
		],
		"pagination": {
			"currentPage": 1,
			"itemsPerPage": 100,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_TRANSACTIONS_EMPTY: dict[str, Any] = {
	"payload": {
		"account": {
			"iban": "CY59005000990000990145684237",
			"accountNumber": "0990145684237",
			"currency": "EUR",
		},
		"numberOfRecords": 0,
		"transactions": [],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 100,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_TRANSACTIONS_WITH_PENDING: dict[str, Any] = {
	"payload": {
		"account": {
			"iban": "CY59005000990000990145684237",
			"accountNumber": "0990145684237",
			"currency": "EUR",
		},
		"numberOfRecords": 2,
		"transactions": [
			{
				"references": {
					"referenceId": "txn-completed-001",
				},
				"type": "CARD",
				"description": "Grocery store",
				"submissionDate": "13/01/2026",
				"submissionTime": "12:18",
				"bookingDate": "13/01/2026",
				"valueDate": "13/01/2026",
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 47.90,
				},
				"balanceAfterTransaction": 1000.00,
				"status": "COMPLETED",
			},
			{
				"references": {
					"referenceId": "txn-pending-001",
				},
				"type": "CARD",
				"description": "Fuel station",
				"submissionDate": "13/01/2026",
				"submissionTime": "13:02",
				"bookingDate": None,
				"valueDate": None,
				"creditDebitIndicator": "DEBIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 62.35,
				},
				"status": "PENDING",
			},
		],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 10,
			"nextPage": None,
			"previousPage": None,
		},
	},
	"errors": None,
}

MOCK_TRANSACTIONS_MULTI_PAGE: dict[str, Any] = {
	"payload": {
		"account": {
			"iban": "CY59005000990000990145684237",
			"accountNumber": "0990145684237",
			"currency": "EUR",
		},
		"numberOfRecords": 100,
		"transactions": [
			{
				"references": {
					"referenceId": f"multi-page-txn-{i:03d}",
				},
				"type": "TRANSFER",
				"description": f"Transaction {i:03d}",
				"submissionDate": "10/01/2026",
				"submissionTime": "10:00",
				"bookingDate": "10/01/2026",
				"valueDate": "10/01/2026",
				"creditDebitIndicator": "DEBIT" if i % 2 == 0 else "CREDIT",
				"transactionAmount": {
					"currency": "EUR",
					"amount": 100.00 + i,
				},
				"counterParty": {
					"name": f"Counterparty {i:03d}",
					"accountNumber": f"ACC-{i:04d}",
				},
				"balanceAfterTransaction": 5000.00 - i * 100,
				"status": "COMPLETED",
			}
			for i in range(100)
		],
		"pagination": {
			"currentPage": 0,
			"itemsPerPage": 100,
			"nextPage": "https://sandbox-apis.hellenicbank.com/v2/b2b/accounts/0990145684237/transactions?page=1&limit=100",
			"previousPage": None,
		},
	},
	"errors": None,
}
