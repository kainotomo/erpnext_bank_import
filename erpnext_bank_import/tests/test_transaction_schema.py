"""Tests for the normalised transaction schema and field-mapping contract."""

from __future__ import annotations

import unittest
from typing import get_type_hints

from erpnext_bank_import.schema.reconciliation import assert_no_prohibited_fields
from erpnext_bank_import.schema.transaction import (
    PROHIBITED_FIELDS,
    TRANSACTION_FIELD_MAP,
    NormalizedTransaction,
    apply_mapping,
)


class TestNormalizedTransaction(unittest.TestCase):
    """Verifies the TypedDict shape and required fields."""

    def test_all_typeddict_fields_are_known(self):
        """Every NormalizedTransaction key must appear in the field map,
        amount and provider_metadata are the only expected exceptions."""
        type_hints = get_type_hints(NormalizedTransaction)
        typed_keys = set(type_hints.keys())

        mapped_keys = set(TRANSACTION_FIELD_MAP.keys())
        exempted = {"amount", "provider_metadata"}

        unmapped = typed_keys - mapped_keys - exempted
        self.assertFalse(unmapped, f"Fields missing from TRANSACTION_FIELD_MAP: {unmapped}")

    def test_field_map_keys_are_subset_of_typeddict(self):
        """Every key in TRANSACTION_FIELD_MAP must exist in the TypedDict."""
        type_hints = get_type_hints(NormalizedTransaction)
        typed_keys = set(type_hints.keys())

        extra = set(TRANSACTION_FIELD_MAP.keys()) - typed_keys
        self.assertFalse(extra, f"TRANSACTION_FIELD_MAP keys not in NormalizedTransaction: {extra}")

    def test_prohibited_fields_are_not_in_field_map(self):
        """No prohibited field should appear in TRANSACTION_FIELD_MAP."""
        overlap = PROHIBITED_FIELDS & set(TRANSACTION_FIELD_MAP.values())
        self.assertFalse(overlap, f"Prohibited fields found in TRANSACTION_FIELD_MAP: {overlap}")


class TestApplyMapping(unittest.TestCase):
    """Verifies the mapping helper transforms correctly."""

    def _make_normalised(self, **overrides) -> NormalizedTransaction:
        """Build a NormalizedTransaction with defaults that can be overridden."""
        defaults: NormalizedTransaction = {
            "external_id": "txn-test",
            "date": "2026-06-01",
            "amount": 100.00,
            "currency": "EUR",
            "description": "Test transaction",
            "reference_number": None,
            "bank_party_name": None,
            "bank_party_account_number": None,
            "bank_party_iban": None,
            "transaction_type": None,
            "included_fee": None,
            "excluded_fee": None,
            "provider_metadata": {},
        }
        defaults.update(overrides)
        return defaults

    def test_deposit_amount_maps_correctly(self):
        """Positive amount becomes deposit."""
        normalised = self._make_normalised(
            external_id="txn-001", amount=1500.00, description="Invoice payment"
        )
        result = apply_mapping(normalised)
        self.assertEqual(result["deposit"], 1500.00)
        self.assertNotIn("withdrawal", result)

    def test_withdrawal_amount_maps_correctly(self):
        """Negative amount becomes positive withdrawal."""
        normalised = self._make_normalised(
            external_id="txn-002", amount=-250.00, description="Card purchase"
        )
        result = apply_mapping(normalised)
        self.assertEqual(result["withdrawal"], 250.00)
        self.assertNotIn("deposit", result)

    def test_zero_amount_becomes_deposit(self):
        """Zero amount maps to deposit (non-negative)."""
        normalised = self._make_normalised(
            external_id="txn-003", amount=0.00, currency="USD",
            description="Zero-amount adjustment",
        )
        result = apply_mapping(normalised)
        self.assertEqual(result["deposit"], 0.00)
        self.assertNotIn("withdrawal", result)

    def test_optional_fields_are_included_if_provided(self):
        """Optional fields appear in the result when the value is not None."""
        normalised = self._make_normalised(
            external_id="txn-004",
            amount=100.00,
            currency="GBP",
            description="Refund",
            reference_number="REF-123",
            bank_party_name="Acme Ltd",
            bank_party_account_number="12345678",
            bank_party_iban="GB12ABCD1234567890",
            transaction_type="TRANSFER",
            included_fee=2.50,
            excluded_fee=None,
            provider_metadata={"merchant_category": "online"},
        )
        result = apply_mapping(normalised)
        self.assertEqual(result["reference_number"], "REF-123")
        self.assertEqual(result["bank_party_name"], "Acme Ltd")
        self.assertEqual(result["bank_party_account_number"], "12345678")
        self.assertEqual(result["bank_party_iban"], "GB12ABCD1234567890")
        self.assertEqual(result["transaction_type"], "TRANSFER")
        self.assertEqual(result["included_fee"], 2.50)
        # excluded_fee is None → omitted; provider_metadata is never mapped

    def test_excluded_fee_none_is_omitted(self):
        """None optional fields should not appear in the result."""
        normalised = self._make_normalised(
            external_id="txn-005", amount=50.00, description="Simple",
        )
        result = apply_mapping(normalised)
        self.assertNotIn("excluded_fee", result)
        self.assertNotIn("reference_number", result)

    def test_provider_metadata_is_never_mapped(self):
        """provider_metadata must never appear in the mapped result."""
        normalised = self._make_normalised(
            external_id="txn-006",
            amount=200.00,
            description="With metadata",
            provider_metadata={"internal_code": "ABC"},
        )
        result = apply_mapping(normalised)
        self.assertNotIn("provider_metadata", result)


class TestProhibitedFields(unittest.TestCase):
    """Verifies prohibited-field guardrails."""

    def test_prohibited_field_raises(self):
        """assert_no_prohibited_fields should raise when a prohibited field is present."""
        data = {
            "bank_account": "BA-001",
            "date": "2026-06-01",
            "deposit": 100.00,
            "status": "Reconciled",  # prohibited
        }
        with self.assertRaises(AssertionError) as ctx:
            assert_no_prohibited_fields(data)
        self.assertIn("status", str(ctx.exception))

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
            self.assertIsInstance(field, str, f"{field} is not a string")
            self.assertTrue(field.islower(), f"{field} is not lowercase")
