# erpnext_bank_import - connectors package
#
# This package defines the bank-agnostic connector interface that every
# bank provider must implement to integrate with the app.
#
# The central contract is ``BankConnector`` (see ``base.py``), an abstract
# base class that defines the lifecycle:
#
#   1. Authentication  — ``authenticate()`` / ``refresh_token()``
#      (use ``OAuth2Service`` from ``erpnext_bank_import.services`` for
#      OAuth2-based providers)
#   2. Account discovery — ``get_accounts()``
#   3. Transaction fetch — ``fetch_transactions()`` (paginated)
#   4. Normalization    — ``normalize_transaction()``
#
# Every connector produces a list of ``NormalizedTransaction`` dicts
# (defined in ``erpnext_bank_import.schema.transaction``) that are then
# mapped to the ERPNext ``Bank Transaction`` doctype via ``apply_mapping()``.
#
# Extension Points
# -----------------
# To add a new bank connector:
#
#   1. Create a new module under ``erpnext_bank_import.connectors``.
#   2. Subclass ``BankConnector`` and implement all abstract methods.
#   3. Return ``NormalizedTransaction`` from ``normalize_transaction()``.
#   4. Register the connector in the provider registry (see below).
#
# For a worked example, see ``MockProvider`` in ``mock_provider.py``.
#
# Shared services (auth, token lifecycle) live in
# ``erpnext_bank_import.services`` — see ``OAuth2Service`` in
# ``services/oauth.py``.
#
# Provider Registry
# ------------------
# Connectors are registered via a simple dict lookup.  Extend
# ``PROVIDER_REGISTRY`` when a new provider is added.

from __future__ import annotations

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.mock_provider import MockProvider

PROVIDER_REGISTRY: dict[str, type[BankConnector]] = {
	"mock": MockProvider,
	# "revolut": RevolutConnector,   # TODO: add in later issue
	# "bank_of_cyprus": ...,         # TODO: future milestone
	# "eurobank_cyprus": ...,        # TODO: future milestone
}
"""Mapping from provider slug to ``BankConnector`` subclass.

Add new providers here so that the orchestration layer can discover
them by name (see ``get_connector()``).
"""


def get_connector(provider: str, **kwargs) -> BankConnector:
	"""Factory: return an instance of the connector for *provider*.

	Args:
	    provider: Provider slug (e.g. ``"mock"``, ``"revolut"``).
	    **kwargs: Forwarded to the connector's constructor.

	Returns:
	    An initialised ``BankConnector`` instance.

	Raises:
	    KeyError: If *provider* is not registered.
	"""
	try:
		cls = PROVIDER_REGISTRY[provider]
	except KeyError:
		raise KeyError(
			f"Unknown bank provider {provider!r}. Available providers: {sorted(PROVIDER_REGISTRY)}"
		) from None
	return cls(**kwargs)


__all__ = [
	"PROVIDER_REGISTRY",
	"BankConnector",
	"MockProvider",
	"get_connector",
]
