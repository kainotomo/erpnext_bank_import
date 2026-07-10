"""Reconciliation reuse documentation.

This module documents the ERPNext-native reconciliation primitives that
``erpnext_bank_import`` **reuses**.  No custom reconciliation engine is
introduced by this app.

Principles
----------
#. The canonical transaction record is the ``Bank Transaction`` doctype.
#. Status transitions (Pending → Settled → Unreconciled → Reconciled)
   are driven entirely by ERPNext's own lifecycle.
#. Matching a bank transaction to a payment/voucher is performed through
   ``reconcile_vouchers()`` or the Bank Reconciliation Tool.
#. Auto-classification is handled by ``Bank Transaction Rule``, not by
   app code.
#. This app writes **only** the fields listed in
   ``erpnext_bank_import.schema.transaction.TRANSACTION_FIELD_MAP``.
"""

# ---------------------------------------------------------------------------
# Reused ERPNext primitives
# ---------------------------------------------------------------------------

# ``Bank Transaction`` status flow
# ---------------------------------
# The Bank Transaction doctype manages its own status via ``set_status()``:
#
#   1. Pending       — draft transaction before submission
#   2. Settled       — submitted transaction waiting for reconciliation
#   3. Unreconciled  — rule evaluation did not yield a match
#   4. Reconciled    - fully matched against vouchers (unallocated_amount == 0)
#   5. Cancelled     — cancelled transaction
#
# This app submits the Bank Transaction (status → Settled) and lets
# ERPNext handle the rest.

# ``Bank Transaction.allocate_payment_entries()``
# ------------------------------------------------
# Allocates matched voucher amounts against a bank transaction and
# updates ``allocated_amount`` / ``unallocated_amount``.

# ``Bank Transaction.add_payment_entries()``
# -------------------------------------------
# Links one or more vouchers (Payment Entry, Journal Entry) to a bank
# transaction via the ``payment_entries`` child table.

# ``reconcile_vouchers()``
# -------------------------
# Whitelisted server-side function
# (``erpnext.accounts.doctype.bank_transaction.bank_transaction.reconcile_vouchers``)
# that performs the full reconciliation workflow: validates vouchers,
# calls ``allocate_payment_entries``, and updates clearance dates.

# ``Bank Transaction Rule``
# --------------------------
# ERPNext-native auto-classification engine.  Rules match on
# description patterns, amount ranges, and transaction type, then
# automatically set party/voucher mappings or create vouchers.

# ``Bank Reconciliation Tool``
# -----------------------------
# ERPNext's UI for manual reconciliation.  Allows users to search for
# matching vouchers and link them to a bank transaction.

# ``get_doctypes_for_bank_reconciliation()``
# -------------------------------------------
# Frappe hook that returns doctypes registered for bank reconciliation.
# Other apps can extend this via ``bank_reconciliation_doctypes`` hook.

# ---------------------------------------------------------------------------
# What this app commits to NOT do
# ---------------------------------------------------------------------------

# - This app will NOT reimplement status tracking logic.
# - This app will NOT write to prohibited fields (see
#   ``transaction.PROHIBITED_FIELDS``).
# - This app will NOT introduce a custom reconciliation UI.
# - This app will NOT bypass ``reconcile_vouchers()``.
# - This app will NOT create ``Bank Transaction Rule`` instances
#   automatically (rules are configured by the user via the Banking UI).


def assert_no_prohibited_fields(bank_transaction_dict: dict) -> None:
    """Assert that no prohibited fields are present in *data*.

    Intended for use in tests and pre-import validation.

    Args:
        bank_transaction_dict: A dict intended for
            ``frappe.get_doc({"doctype": "Bank Transaction", **data})``.

    Raises:
        AssertionError: If any prohibited field is present.
    """
    from erpnext_bank_import.schema.transaction import PROHIBITED_FIELDS

    present = PROHIBITED_FIELDS & bank_transaction_dict.keys()
    if present:
        raise AssertionError(
            f"Prohibited fields must not be set by the import layer: {sorted(present)}"
        )
