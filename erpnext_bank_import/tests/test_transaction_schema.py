"""Tests for the normalised transaction schema and field-mapping contract."""

from __future__ import annotations

from typing import get_type_hints

import pytest

from erpnext_bank_import.schema.reconciliation import assert_no_prohibited_fields
from erpnext_bank_import.schema.transaction import (
    PROHIBITED_FIELDS,
    TRANSACTION_FIELD_MAP,
    NormalizedTransaction,
    apply_mapping,
)


class TestNormalizedTransaction:
    """Verifies the TypedDict shape and required fields."""

    def test_all_typeddict_fields_are_known(self):
        """Every NormalizedTransaction key must appear in the field map,
        amount and provider_metadata are the only expected exceptions."""
        type_hints = get_type_hints(NormalizedTransaction)
        typed_keys = set(type_hints.keys())

        mapped_keys = set(TRANSACTION_FIELD_MAP.keys())
        exempted = {"amount", "provider_metadata"}

        unmapped = typed_keys - mapped_keys - exempted
        assert not unmapped, f"Fields missing from TRANSACTION_FIELD_MAP: {unmapped}"

    def test_field_map_keys_are_subset_of_typeddict(self):
        """Every key in TRANSACTION_FIELD_MAP must exist in the TypedDict."""
        type_hints = get_type_hints(NormalizedTransaction)
        typed_keys = set(type_hints.keys())

        extra = set(TRANSACTION_FIELD_MAP.keys()) - typed_keys
        assert not extra, f"TRANSACTION_FIELD_MAP keys not in NormalizedTransaction: {extra}"

    def test_prohibited_fields_are_not_in_field_map(self):
        """No prohibited field should appear in TRANSACTION_FIELD_MAP."""
        overlap = PROHIBITED_FIELDS & set(TRANSACTION_FIELD_MAP.values())
        assert not overlap, f"Prohibited fields found in TRANSACTION_FIELD_MAP: {overlap}"


class TestApplyMapping:
    """Verifies the mapping helper transforms correctly."""

    def test_deposit_amount_maps_correctly(self):
        """Positive amount becomes deposit."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-001",
            "date": "2026-06-01",
            "amount": 1500.00,
            "currency": "EUR",
            "description": "Invoice payment",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {},
        }

        result = apply_mapping(normalised)
        assert result["deposit"] == 1500.00
        assert "withdrawal" not in result

    def test_withdrawal_amount_maps_correctly(self):
        """Negative amount becomes positive withdrawal."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-002",
            "date": "2026-06-02",
            "amount": -250.00,
            "currency": "EUR",
            "description": "Card purchase",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {},
        }

        result = apply_mapping(normalised)
        assert result["withdrawal"] == 250.00
        assert "deposit" not in result

    def test_zero_amount_becomes_deposit(self):
        """Zero amount maps to deposit (non-negative)."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-003",
            "date": "2026-06-03",
            "amount": 0.00,
            "currency": "USD",
            "description": "Zero-amount adjustment",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {},
        }

        result = apply_mapping(normalised)
        assert result["deposit"] == 0.00
        assert "withdrawal" not in result

    def test_optional_fields_are_included_if_provided(self):
        """Optional fields appear in the result when the value is not None."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-004",
            "date": "2026-06-04",
            "amount": 100.00,
            "currency": "GBP",
            "description": "Refund",
            "reference_number": "REF-123",
            "bank_party_name": "Acme Ltd",
            "bank_party_account_number": "12345678",
            "bank_party_iban": "GB12ABCD1234567890",
            "transaction_type": "TRANSFER",
            "included_fee": 2.50,
            "excluded_fee": None,
            "provider_metadata": {"merchant_category": "online"},
        }

        result = apply_mapping(normalised)
        assert result["reference_number"] == "REF-123"
        assert result["bank_party_name"] == "Acme Ltd"
        assert result["bank_party_account_number"] == "12345678"
        assert result["bank_party_iban"] == "GB12ABCD1234567890"
        assert result["transaction_type"] == "TRANSFER"
        assert result["included_fee"] == 2.50
        # excluded_fee is None → omitted; provider_metadata is never mapped

    def test_excluded_fee_none_is_omitted(self):
        """None optional fields should not appear in the result."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-005",
            "date": "2026-06-05",
            "amount": 50.00,
            "currency": "EUR",
            "description": "Simple",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {},
        }

        result = apply_mapping(normalised)
        assert "excluded_fee" not in result
        assert "reference_number" not in result

    def test_provider_metadata_is_never_mapped(self):
        """provider_metadata must never appear in the mapped result."""
        normalised: NormalizedTransaction = {
            "external_id": "txn-006",
            "date": "2026-06-06",
            "amount": 200.00,
            "currency": "EUR",
            "description": "With metadata",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {"internal_code": "ABC"},
        }

        result = apply_mapping(normalised)
        assert "provider_metadata" not in result


class TestProhibitedFields:
    """Verifies prohibited-field guardrails."""

    def test_prohibited_field_raises(self):
        """assert_no_prohibited_fields should raise when a prohibited field is present."""
        data = {
            "bank_account": "BA-001",
            "date": "2026-06-01",
            "deposit": 100.00,
            "status": "Reconciled",  # prohibited
        }
        with pytest.raises(AssertionError, match="status"):
            assert_no_prohibited_fields(data)

    def test_no_prohibited_fields_passes(self):
        """assert_no_prohibited_fields should not raise for clean data."""
        data = {
            "bank_account": "BA-001",
            "date": "2026-06-01",
            "deposit": 100.00,
        }
        assert_no_prohibited_fields(data)  # no raise

    def test_all_distinct_prohibited_field_names(self):
        """All entries in PROHIBITED_FIELDS must be lowercase strings."""
        for field in PROHIBITED_FIELDS:
            assert isinstance(field, str), f"{field} is not a string"
            assert field.islower(), f"{field} is not lowercase"
