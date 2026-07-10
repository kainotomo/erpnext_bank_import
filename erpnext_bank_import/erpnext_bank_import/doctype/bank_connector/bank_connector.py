"""Controller for the Bank Connector DocType.

Each record represents one bank connector instance — a set of API
credentials and account mappings for a specific provider and company.
Multiple records can coexist for multi-company setups, sandbox +
production environments, and different providers (Revolut, Bank of
Cyprus, etc.).

The key method is ``get_connector_config()`` which converts this
Frappe DocType into a ``ConnectorConfig`` dataclass for consumption
by the connector layer.
"""

from __future__ import annotations

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_bank_import.connectors.config import ConnectorConfig


class BankConnector(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext_bank_import.erpnext_bank_import.doctype.bank_connector_account_mapping.bank_connector_account_mapping import (
			BankConnectorAccountMapping,
		)

		account_mappings: DF.Table[BankConnectorAccountMapping]
		api_base_url: DF.Data
		auth_method: DF.Literal["oauth2", "api_key", "basic"]
		authorize_url: DF.Data | None
		client_id: DF.Data | None
		client_secret: DF.Password | None
		company: DF.Link
		connector_name: DF.Data
		enabled: DF.Check
		jwt_issuer: DF.Data | None
		jwt_private_key: DF.Password | None
		provider_name: DF.Literal["revolut", "mock"]
		rate_limit_rps: DF.Float | None
		redirect_uri: DF.Data | None
		revoke_url: DF.Data | None
		scopes: DF.SmallText | None
		token_safety_buffer_seconds: DF.Int | None
		token_url: DF.Data | None
		timeout_seconds: DF.Int | None
	# end: auto-generated types

	# ------------------------------------------------------------------
	# Validation
	# ------------------------------------------------------------------

	def validate(self) -> None:
		"""Validate connector configuration before saving.

		Checks performed:
		- Required fields are filled (connector_name, provider_name,
		  company, api_base_url).
		- URL fields have a valid scheme (http/https).
		- OAuth2-specific fields are present when auth_method is oauth2.
		- At least one enabled account mapping exists when the connector
		  is enabled.
		- No duplicate provider_account_id values within mappings.
		- No duplicate bank_account links within mappings.
		"""
		self._validate_required()
		self._validate_urls()
		self._validate_oauth_fields()
		self._validate_account_mappings()

	def before_save(self) -> None:
		"""Normalise URLs before persisting."""
		self.api_base_url = self.api_base_url.rstrip("/")
		if self.authorize_url:
			self.authorize_url = self.authorize_url.rstrip("/")
		if self.token_url:
			self.token_url = self.token_url.rstrip("/")
		if self.revoke_url:
			self.revoke_url = self.revoke_url.rstrip("/")

	# ------------------------------------------------------------------
	# Public helpers
	# ------------------------------------------------------------------

	def get_connector_config(self) -> ConnectorConfig:
		"""Convert this DocType record into a ``ConnectorConfig`` dataclass.

		Reads the ``client_secret`` via ``get_password()`` (Frappe's
		auto-decrypt mechanism for Password fields).

		Returns:
		    A fully populated ``ConnectorConfig`` instance.
		"""
		scopes_list: list[str] = []
		if self.scopes:
			scopes_list = [s.strip() for s in self.scopes.split() if s.strip()]

		return ConnectorConfig(
			provider_name=self.provider_name,
			api_base_url=self.api_base_url,
			auth_method=self.auth_method,
			client_id=self._none_if_blank(self.client_id),
			client_secret=self.get_password("client_secret") if self.client_secret else None,
			authorize_url=self._none_if_blank(self.authorize_url),
			token_url=self._none_if_blank(self.token_url),
			revoke_url=self._none_if_blank(self.revoke_url),
			scopes=scopes_list,
			redirect_uri=self._none_if_blank(self.redirect_uri),
			rate_limit_rps=self.rate_limit_rps,
			timeout_seconds=self.timeout_seconds or 30.0,
			token_safety_buffer_seconds=self.token_safety_buffer_seconds or 60,
			jwt_private_key=self.get_password("jwt_private_key") if self.jwt_private_key else None,
			jwt_issuer=self._none_if_blank(self.jwt_issuer),
		)

	@staticmethod
	def _none_if_blank(value: str | None) -> str | None:
		"""Return ``None`` if *value* is ``None`` or an empty/whitespace string."""
		if value is None or not value.strip():
			return None
		return value

	def get_enabled_mappings(self) -> list[dict[str, str]]:
		"""Return only the enabled account mappings.

		Returns:
		    A list of dicts with keys ``provider_account_id`` and
		    ``bank_account``.
		"""
		return [
			{
				"provider_account_id": m.provider_account_id,
				"bank_account": m.bank_account,
			}
			for m in self.account_mappings
			if m.is_enabled
		]

	# ------------------------------------------------------------------
	# Import action
	# ------------------------------------------------------------------

	@frappe.whitelist()
	def import_transactions_action(self) -> None:
		"""Trigger an import for this connector via the UI action button.

		Reads ``date_from`` / ``date_to`` from the form.  If both are
		blank the import runs in incremental mode (uses
		``last_synced_at`` per account mapping).  Otherwise it is a
		backfill for the explicit date window.
		"""
		from erpnext_bank_import.services.import_service import import_transactions

		date_from = self.date_from
		date_to = self.date_to

		summary = import_transactions(
			connector_name=self.connector_name,
			date_from=date_from,
			date_to=date_to,
		)

		parts: list[str] = []
		for r in summary["results"]:
			if r["error"]:
				parts.append(
					_("{account}: {created} created, {skipped} skipped — error: {err}").format(
						account=r["account_id"], created=r["created"], skipped=r["skipped"], err=r["error"]
					)
				)
			else:
				parts.append(
					_("{account}: {created} created, {skipped} skipped").format(
						account=r["account_id"], created=r["created"], skipped=r["skipped"]
					)
				)

		msg = _("Import complete for {name}").format(name=self.connector_name)
		if parts:
			msg += "\n" + "\n".join(parts)

		frappe.msgprint(
			msg, title=_("Import Results"), indicator="green" if summary["status"] == "success" else "orange"
		)

	# ------------------------------------------------------------------
	# Internal validators
	# ------------------------------------------------------------------

	def _validate_required(self) -> None:
		if not self.connector_name:
			frappe.throw(frappe._("Connector Name is required."))
		if not self.provider_name:
			frappe.throw(frappe._("Provider Name is required."))
		if not self.company:
			frappe.throw(frappe._("Company is required."))
		if not self.api_base_url:
			frappe.throw(frappe._("API Base URL is required."))

	def _validate_urls(self) -> None:
		url_fields = {
			"API Base URL": self.api_base_url,
			"Authorize URL": self.authorize_url,
			"Token URL": self.token_url,
			"Revoke URL": self.revoke_url,
		}
		for label, value in url_fields.items():
			if value:
				self._assert_valid_url(value, label)

	@staticmethod
	def _assert_valid_url(url: str, label: str) -> None:
		parsed = urlparse(url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(frappe._("{0} must be a valid URL starting with http:// or https://.").format(label))

	def _validate_oauth_fields(self) -> None:
		if self.auth_method != "oauth2":
			return
		missing = []
		if not self.client_id:
			missing.append("Client ID")
		if not self.client_secret:
			missing.append("Client Secret")
		if not self.authorize_url:
			missing.append("Authorize URL")
		if not self.token_url:
			missing.append("Token URL")
		if missing:
			frappe.throw(
				frappe._(
					"The following OAuth2 fields are required when Authentication Method is 'oauth2': {0}"
				).format(", ".join(missing))
			)

	def _validate_account_mappings(self) -> None:
		if not self.enabled:
			return

		# At least one enabled mapping when connector is enabled.
		enabled_mappings = [m for m in self.account_mappings if m.is_enabled]
		if not enabled_mappings:
			frappe.throw(
				frappe._("At least one enabled Account Mapping is required when the connector is enabled.")
			)

		# No duplicate provider_account_id within the same connector.
		seen_ids: set[str] = set()
		dup_ids: list[str] = []
		for m in self.account_mappings:
			pid = m.provider_account_id.strip().lower()
			if pid in seen_ids:
				dup_ids.append(m.provider_account_id)
			seen_ids.add(pid)
		if dup_ids:
			frappe.throw(
				frappe._("Duplicate Provider Account IDs are not allowed: {0}").format(", ".join(dup_ids))
			)

		# No duplicate bank_account within the same connector.
		seen_bank: set[str] = set()
		dup_banks: list[str] = []
		for m in self.account_mappings:
			if m.bank_account:
				bac = m.bank_account.strip().lower()
				if bac in seen_bank:
					dup_banks.append(m.bank_account)
				seen_bank.add(bac)
		if dup_banks:
			frappe.throw(
				frappe._(
					"Duplicate Bank Account links are not allowed within the same connector: {0}"
				).format(", ".join(dup_banks))
			)


__all__ = [
	"BankConnector",
]
