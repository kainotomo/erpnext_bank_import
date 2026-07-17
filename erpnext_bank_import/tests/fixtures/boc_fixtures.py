"""Reusable mock Bank of Cyprus API response fixtures.

Provides deterministic fixture data matching real BoC PSD2/B2B API
response shapes for use in pytest-based tests.

Date format: DD/MM/YYYY (per BoC API convention).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Mock account responses
# ---------------------------------------------------------------------------

MOCK_BOC_ACCOUNTS: list[dict[str, Any]] = [
    {
        "bankId": "12345671",
        "accountId": "351012345671",
        "accountAlias": "ANDREAS",
        "accountType": "CURRENT",
        "accountName": "ANDREAS MICHAEL",
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "infoTimeStamp": "1511779237",
        "interestRate": 0,
        "maturityDate": "19/11/2018",
        "lastPaymentDate": "19/11/2017",
        "nextPaymentDate": "19/12/2017",
        "remainingInstallments": 10,
        "balances": [
            {"amount": 1000.0, "balanceType": "CLBD"},
        ],
    },
    {
        "bankId": "12345672",
        "accountId": "351092345672",
        "accountAlias": "BUSINESS",
        "accountType": "CURRENT",
        "accountName": "ALPHA BUSINESS LTD",
        "IBAN": "CY11002003510000000012345672",
        "currency": "EUR",
        "infoTimeStamp": "1511779238",
        "interestRate": 0.5,
        "maturityDate": "19/11/2025",
        "lastPaymentDate": "19/11/2024",
        "nextPaymentDate": "19/12/2024",
        "remainingInstallments": 5,
        "balances": [
            {"amount": 50000.0, "balanceType": "CLBD"},
        ],
    },
    {
        "bankId": "12345673",
        "accountId": "351012345673",
        "accountAlias": "SAVINGS",
        "accountType": "SAVINGS",
        "accountName": "ANDREAS SAVINGS",
        "IBAN": "CY11002003510000000012345673",
        "currency": "EUR",
        "infoTimeStamp": "1511779239",
        "interestRate": 1.2,
        "maturityDate": "19/11/2026",
        "lastPaymentDate": "19/11/2024",
        "nextPaymentDate": "19/12/2024",
        "remainingInstallments": 0,
        "balances": [
            {"amount": 25000.0, "balanceType": "CLBD"},
        ],
    },
]

# ---------------------------------------------------------------------------
# Mock subscription responses
# ---------------------------------------------------------------------------

MOCK_BOC_SUBSCRIPTION_CREATED: dict[str, Any] = {
    "duration": {"startDate": "17/07/2026", "endDate": "13/01/2027"},
    "subscriptionId": "Subid000001-1725429256148",
    "status": "PENDING",
    "description": "SUBSCRIPTION",
    "selectedAccounts": [],
    "accounts": {
        "transactionHistory": True,
        "balance": True,
        "details": True,
        "checkFundsAvailability": True,
    },
    "payments": {"limit": 99999999, "currency": "EUR", "amount": 999999999},
}

MOCK_BOC_SUBSCRIPTION_DETAILS: dict[str, Any] = {
    "subscriptionId": "Subid000001-1725429256148",
    "status": "PENDING",
    "description": "SUBSCRIPTION",
    "selectedAccounts": [
        {"accountId": "351012345671"},
        {"accountId": "351092345672"},
        {"accountId": "351012345673"},
    ],
    "accounts": {
        "transactionHistory": True,
        "balance": True,
        "details": True,
        "checkFundsAvailability": True,
    },
    "payments": {"limit": 50, "currency": "EUR", "amount": 50},
    "expirationDate": "13/01/2027",
}

MOCK_BOC_SUBSCRIPTION_ACTIVATED: dict[str, Any] = {
    "subscriptionId": "Subid000001-1725429256148",
    "status": "ACTV",
    "description": "SUBSCRIPTION",
    "selectedAccounts": [
        {"accountId": "351012345671"},
        {"accountId": "351092345672"},
        {"accountId": "351012345673"},
    ],
    "accounts": {
        "transactionHistory": True,
        "balance": True,
        "details": True,
        "checkFundsAvailability": True,
    },
    "payments": {"limit": 50, "currency": "EUR", "amount": 50},
    "expirationDate": "13/01/2027",
}

# ---------------------------------------------------------------------------
# Mock OAuth token responses
# ---------------------------------------------------------------------------

MOCK_BOC_TOKEN_TPP: dict[str, Any] = {
    "token_type": "bearer",
    "access_token": "tpp-access-token-abc123",
    "expires_in": 3600,
    "consented_on": 1725429256,
    "scope": "TPPOAuth2Security",
}

MOCK_BOC_TOKEN_USER: dict[str, Any] = {
    "token_type": "bearer",
    "access_token": "user-access-token-xyz789",
    "expires_in": 3600,
    "consented_on": 1725429256,
    "scope": "UserOAuth2Security",
}

# ---------------------------------------------------------------------------
# Mock statement (transaction) responses
# ---------------------------------------------------------------------------


def _date_for_index(day: int, month_offset: int = 0) -> str:
    """Return a valid DD/MM/YYYY date for a 1-based day index.

    Spreads across months to handle day indices > 31.
    Month 0 = July 2026, month 1 = June 2026, etc.
    """
    months = [(7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31)]
    if month_offset:
        months = [(6, 30), (5, 31), (4, 30), (3, 31), (2, 28)]
    cumulative = 0
    for month, days_in_month in months:
        if day <= cumulative + days_in_month:
            return f"{day - cumulative:02d}/{month:02d}/2026"
        cumulative += days_in_month
    return "31/12/2026"


MOCK_BOC_STATEMENT: dict[str, Any] = {
    "account": {
        "bankId": "12345671",
        "accountId": "351012345671",
        "accountAlias": "ANDREAS",
        "accountType": "CURRENT",
        "accountName": "ANDREAS MICHAEL",
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "infoTimeStamp": "1511779237",
    },
    "transaction": [
        {
            "id": "663c9d26de9162079842ce01",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 30.0, "currency": "EUR"},
            "description": "SWIFT Transfer",
            "postingDate": "09/05/2024",
            "valueDate": "09/05/2024",
        },
        {
            "id": "663c9d26de9162079842ce02",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 1500.0, "currency": "EUR"},
            "description": "Salary Payment",
            "postingDate": "08/05/2024",
            "valueDate": "08/05/2024",
        },
        {
            "id": "663c9d26de9162079842ce03",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 45.50, "currency": "EUR"},
            "description": "POS Purchase - Alpha Supermarket",
            "postingDate": "07/05/2024",
            "valueDate": "07/05/2024",
        },
        {
            "id": "663c9d26de9162079842ce04",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 12.99, "currency": "EUR"},
            "description": "ATM Withdrawal",
            "postingDate": "06/05/2024",
            "valueDate": "06/05/2024",
        },
        {
            "id": "663c9d26de9162079842ce05",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 200.0, "currency": "EUR"},
            "description": "Transfer from Savings",
            "postingDate": "05/05/2024",
            "valueDate": "06/05/2024",
        },
    ],
}

MOCK_BOC_STATEMENT_EMPTY: dict[str, Any] = {
    "account": {
        "bankId": "12345671",
        "accountId": "351012345671",
        "accountAlias": "ANDREAS",
        "accountType": "CURRENT",
        "accountName": "ANDREAS MICHAEL",
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "infoTimeStamp": "1511779237",
    },
    "transaction": [],
}

MOCK_BOC_STATEMENT_SINGLE_PAGE: dict[str, Any] = {
    "account": {
        "bankId": "12345671",
        "accountId": "351012345671",
        "accountAlias": "ANDREAS",
        "accountType": "CURRENT",
        "accountName": "ANDREAS MICHAEL",
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "infoTimeStamp": "1511779237",
    },
    "transaction": [
        {
            "id": f"663c9d26de9162079842ce{i:02d}",
            "dcInd": "DEBIT" if i % 2 == 0 else "CREDIT",
            "transactionAmount": {"amount": float(i * 10), "currency": "EUR"},
            "description": f"Transaction {i}",
            # Spread across months to get valid dates for all 100 transactions.
            # Days 1-31 in July, 32-61 in August, 62-92 in September, 93-100 in October.
            "postingDate": _date_for_index(i),
            "valueDate": _date_for_index(i),
        }
        for i in range(1, 101)  # 100 transactions = full page with page_size=100
    ],
}

MOCK_BOC_STATEMENT_SECOND_PAGE: dict[str, Any] = {
    "account": {
        "bankId": "12345671",
        "accountId": "351012345671",
        "accountAlias": "ANDREAS",
        "accountType": "CURRENT",
        "accountName": "ANDREAS MICHAEL",
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "infoTimeStamp": "1511779237",
    },
    "transaction": [
        {
            "id": f"663c9d26de9162079842ce{i:02d}",
            "dcInd": "DEBIT" if i % 2 == 0 else "CREDIT",
            "transactionAmount": {"amount": float(i * 10), "currency": "EUR"},
            "description": f"Transaction {i}",
            # Days 1-31 in June (i=101→day 1, i=131→day 31), then 1-19 in May
            "postingDate": _date_for_index(i - 100, month_offset=1),
            "valueDate": _date_for_index(i - 100, month_offset=1),
        }
        for i in range(101, 151)  # 50 transactions = partial page
    ],
}

# ---------------------------------------------------------------------------
# Mock error responses
# ---------------------------------------------------------------------------

MOCK_BOC_ERROR_401: dict[str, Any] = {
    "error": "Unauthorized",
    "error_description": "Invalid or expired access token",
}

MOCK_BOC_ERROR_429: dict[str, Any] = {
    "error": "Too Many Requests",
    "error_description": "Rate limit exceeded",
}

MOCK_BOC_ERROR_500: dict[str, Any] = {
    "error": "Internal Server Error",
    "error_description": "An unexpected error occurred",
}


def make_statement_page(
    transactions: list[dict[str, Any]],
    account_id: str = "351012345671",
) -> dict[str, Any]:
    """Build a BoC statement response with the given transactions.

    Args:
        transactions: A list of BoC transaction dicts.
        account_id: The BoC account ID to embed in the statement.

    Returns:
        A full statement dict matching BoC's GET /accounts/{id}/statement shape.
    """
    return {
        "account": {
            "bankId": "12345671",
            "accountId": account_id,
            "accountAlias": "ANDREAS",
            "accountType": "CURRENT",
            "accountName": "ANDREAS MICHAEL",
            "IBAN": "CY11002003510000000012345671",
            "currency": "EUR",
            "infoTimeStamp": "1511779237",
        },
        "transaction": transactions,
    }
