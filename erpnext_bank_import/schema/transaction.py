"""Normalized transaction schema and ERPNext Bank Transaction field mapping.

This module defines the canonical internal representation of a bank
transaction after normalisation from a provider-specific API format.
All bank connectors MUST produce a dict matching ``NormalizedTransaction``
before any ERPNext write occurs.

The mapping contract is documented in ``TRANSACTION_FIELD_MAP`` and the
prohibited fields in ``PROHIBITED_FIELDS``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class NormalizedTransaction(TypedDict):
    """Canonical internal representation of a single bank transaction.

    Every bank connector normalises its API response into this shape.
    Fields are deliberately provider-agnostic; provider-specific data
    that has no ERPNext equivalent should be placed in ``provider_metadata``.
    """

    # -- Required fields ------------------------------------------------
    external_id: str
    """Unique transaction identifier from the source bank.
    Used for idempotency and auditability. Maps to ``transaction_id``."""

    date: str
    """Transaction date in ISO-8601 format (``YYYY-MM-DD``)."""

    amount: float
    """Signed transaction amount.

    * Positive (>= 0) → deposit
    * Negative (< 0)  → withdrawal (absolute value stored)
    """

    currency: str
    """ISO-4217 currency code, e.g. ``EUR``, ``USD``."""

    description: str
    """Transaction narrative / description from the bank statement."""

    # -- Optional fields ------------------------------------------------
    reference_number: str | None
    """Bank or cheque reference number, if available."""

    bank_party_name: str | None
    """Counterparty name as it appears on the bank statement."""

    bank_party_account_number: str | None
    """Counterparty account number as it appears on the bank statement."""

    bank_party_iban: str | None
    """Counterparty IBAN as it appears on the bank statement."""

    transaction_type: str | None
    """Provider-specific transaction type label (e.g. ``TRANSFER``, ``CARD_PAYMENT``)."""

    included_fee: float | None
    """Fee amount already baked into the transaction amount."""

    excluded_fee: float | None
    """Fee amount charged separately (not included in the transaction amount)."""

    provider_metadata: dict[str, Any]
    """Opaque bag for provider-specific data that has no ERPNext field.

    This data is **not** written to ERPNext but is preserved for
    debugging, audit trails, or future custom reports.
    """


# ---------------------------------------------------------------------------
# Field-mapping contract
# ---------------------------------------------------------------------------

TRANSACTION_FIELD_MAP: dict[str, str] = {
    # NormalizedTransaction key → Bank Transaction doctype field
    "external_id": "transaction_id",
    "date": "date",
    "currency": "currency",
    "description": "description",
    "reference_number": "reference_number",
    "bank_party_name": "bank_party_name",
    "bank_party_account_number": "bank_party_account_number",
    "bank_party_iban": "bank_party_iban",
    "transaction_type": "transaction_type",
    "included_fee": "included_fee",
    "excluded_fee": "excluded_fee",
}
"""Maps every writable ``NormalizedTransaction`` key to its ERPNext
``Bank Transaction`` field name.

``amount`` is intentionally absent because it is split into
``deposit`` / ``withdrawal`` via a sign check (see ``apply_mapping``).

``provider_metadata`` is intentionally absent because it has no ERPNext
field counterpart.
"""

# Fields that this app MUST NEVER write directly. They are managed
# entirely by ERPNext's own reconciliation and submission logic.
PROHIBITED_FIELDS: frozenset[str] = frozenset({
    "status",
    "allocated_amount",
    "unallocated_amount",
    "party_type",
    "party",
    "payment_entries",
    "matched_transaction_rule",
    "is_rule_evaluated",
    "naming_series",
    "amended_from",
})
"""Set of ``Bank Transaction`` fields that app code must never set.

These fields are managed by ERPNext's reconciliation engine, rule
evaluator, or submission lifecycle.
"""


# ---------------------------------------------------------------------------
# Mapping helper
# ---------------------------------------------------------------------------

def apply_mapping(normalised: NormalizedTransaction) -> dict[str, Any]:
    """Convert a ``NormalizedTransaction`` into an ERPNext Bank Transaction dict.

    The returned dict contains only fields that the import layer is
    allowed to write, with ``amount`` decomposed into ``deposit``
    (positive) or ``withdrawal`` (absolute of negative).

    Args:
        normalised: A transaction dict conforming to ``NormalizedTransaction``.

    Returns:
        A dict suitable for ``frappe.get_doc({"doctype": "Bank Transaction", **result})``.
    """
    result: dict[str, Any] = {}

    for norm_key, bt_field in TRANSACTION_FIELD_MAP.items():
        value = normalised.get(norm_key)
        if value is not None:
            result[bt_field] = value

    # Decompose signed amount into deposit / withdrawal.
    amount = normalised.get("amount", 0.0)
    if amount >= 0:
        result["deposit"] = amount
    else:
        result["withdrawal"] = abs(amount)

    return result
