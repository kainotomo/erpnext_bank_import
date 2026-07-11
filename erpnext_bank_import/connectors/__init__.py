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

import frappe

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import ConfigurationError
from erpnext_bank_import.connectors.mock_provider import MockProvider
from erpnext_bank_import.connectors.revolut import RevolutConnector

PROVIDER_REGISTRY: dict[str, type[BankConnector]] = {
	"mock": MockProvider,
	"revolut": RevolutConnector,
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


# ------------------------------------------------------------------
# Bank Connector Doctype integration
# ------------------------------------------------------------------


def get_connector_config(connector_name: str) -> ConnectorConfig:
	"""Load a ``ConnectorConfig`` from a ``Bank Connector`` DocType record.

	Args:
	    connector_name: The ``connector_name`` field value of the
	        ``Bank Connector`` record.

	Returns:
	    A fully populated ``ConnectorConfig`` dataclass instance.

	Raises:
	    ConfigurationError: If the record does not exist or is disabled.
	"""
	if not frappe.db.exists("Bank Connector", connector_name):
		raise ConfigurationError(f"Bank Connector '{connector_name}' does not exist.")

	doc = frappe.get_doc("Bank Connector", connector_name)

	if not doc.enabled:
		raise ConfigurationError(f"Bank Connector '{connector_name}' is disabled.")

	return doc.get_connector_config()


def get_all_enabled_connectors() -> list[str]:
	"""Return the ``connector_name`` of every enabled ``Bank Connector`` record.

	Returns:
	    A list of connector names suitable for passing to
	    ``get_connector_config()``.
	"""
	records = frappe.db.get_all(
		"Bank Connector",
		filters={"enabled": 1},
		fields=["connector_name"],
		order_by="connector_name",
	)
	return [r["connector_name"] for r in records]


def get_connector_config_by_provider_company(provider: str, company: str) -> list[ConnectorConfig]:
	"""Load ``ConnectorConfig`` for all enabled connectors matching
	*provider* and *company*.

	Args:
	    provider: Provider slug (e.g. ``"revolut"``).
	    company: Company name.

	Returns:
	    A list of ``ConnectorConfig`` instances (one per matching record).
	"""
	records = frappe.db.get_all(
		"Bank Connector",
		filters={
			"provider_name": provider,
			"company": company,
			"enabled": 1,
		},
		fields=["connector_name"],
		order_by="connector_name",
	)
	result: list[ConnectorConfig] = []
	for r in records:
		doc = frappe.get_doc("Bank Connector", r["connector_name"])
		result.append(doc.get_connector_config())
	return result


__all__ = [
	"PROVIDER_REGISTRY",
	"BankConnector",
	"ConnectorConfig",
	"MockProvider",
	"RevolutConnector",
	"get_all_enabled_connectors",
	"get_connector",
	"get_connector_config",
	"get_connector_config_by_provider_company",
]
