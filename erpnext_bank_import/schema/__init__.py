# erpnext_bank_import - schema package
#
# This package defines the normalized data contract between bank API providers
# and the ERPNext Bank Transaction doctype. It is the canonical reference for
# what data flows into ERPNext and how it is mapped.
#
# Modules:
#   transaction    — NormalizedTransaction TypedDict, field map, and mapping helper
#   reconciliation — ERPNext reconciliation primitives reused by this app
