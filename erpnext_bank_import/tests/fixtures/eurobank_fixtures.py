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
	"access_token": "6e3f5528-3bd2-4459-89a4-4830878f63a8",
	"client_id": "1b7b4fe5290f44528bd2e97fbcc22660",
	"scope": [
		"v2.b2b.get.accounts",
		"v2.b2b.get.account.details",
		"v2.b2b.get.account.transactions",
	],
	"created_on": 1768294396503,
	"expires_at": 1768296208785,
	"refresh_token": "f6a1df97-ed65-41ad-a971-dd3ae4a38c9e",
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
