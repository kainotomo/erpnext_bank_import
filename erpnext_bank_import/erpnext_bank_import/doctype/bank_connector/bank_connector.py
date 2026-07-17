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
		jwt_private_key: DF.Code | None
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
			jwt_private_key=self.jwt_private_key,
			jwt_issuer=self._none_if_blank(self.jwt_issuer),
			extra={"sandbox": bool(self.sandbox)},
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

		Enqueues a background job (following the same pattern as ERPNext's
		``BankStatementImport.start_import()``) so the UI does not hang
		for large imports.  Uses ``enqueue_import`` for dedup — if an
		import for this connector is already queued, it is not duplicated.

		Reads ``date_from`` / ``date_to`` from the form.  If both are
		blank the import runs in incremental mode (uses
		``last_synced_at`` per account mapping).  Otherwise it is a
		backfill for the explicit date window.
		"""
		from erpnext_bank_import.services.import_service import enqueue_import

		date_from = self.date_from
		date_to = self.date_to

		enqueue_import(
			connector_name=self.connector_name,
			date_from=date_from,
			date_to=date_to,
			trigger="Manual",
		)

	@frappe.whitelist()
	def check_import_health_action(self) -> None:
		"""Show a health summary for this connector.

		Queries Frappe's built-in ``Scheduled Job Log`` and our
		``Bank Import Run Log`` to surface recent run status,
		success/failure counts, and last error details — all in a
		readable message dialog.
		"""
		from erpnext_bank_import.services.import_service import get_import_health

		health = get_import_health(self.connector_name, hours=48)

		lines: list[str] = []
		last_run = health["last_import_run"]
		if last_run:
			lines.append(
				f"Last import: {last_run.get('status', 'N/A')} "
				f"({last_run.get('total_created', 0)} created, "
				f"{last_run.get('total_skipped', 0)} skipped)"
			)
			if last_run.get("error_summary"):
				lines.append(f"Last error: {last_run['error_summary']}")
		else:
			lines.append("No import runs recorded yet.")

		error_count = health["recent_error_count"]
		lines.append(f"Errors in last 48h: {error_count}")

		scheduler_count = len(health["recent_scheduler_runs"])
		lines.append(f"Scheduled job runs (last 48h): {scheduler_count}")

		frappe.msgprint(
			"\n".join(lines),
			title=frappe._("Import Health: {0}").format(self.connector_name),
			indicator="green" if error_count == 0 else "orange",
		)

	@frappe.whitelist()
	def fetch_accounts_action(self) -> None:
		"""Discover bank accounts from the provider API and populate
		the account mappings table.

		For each account returned by the provider, this method creates
		a row in the ``account_mappings`` child table with the
		``provider_account_id`` and ``provider_account_name`` pre-filled.
		The user then only needs to select the ERPNext ``Bank Account``
		to link it.

		When called as a Server Action (module-level function), delegates
		to :func:`fetch_accounts_action_server` which resolves the
		document from ``frappe.form_dict``.
		"""
		_fetch_accounts_for_doc(self)

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
		# Revolut uses JWT client assertion — client_secret is never needed
		if self.provider_name != "revolut":
			if not self.client_secret and not self.jwt_private_key:
				missing.append("Client Secret")
		# client_id is required for OAuth2, but allow saving without it
		# when setting up a Revolut connector (user will fill it after
		# generating a certificate and getting it from Revolut).
		if not self.client_id and self.provider_name != "revolut":
			missing.append("Client ID")
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


def _fetch_accounts_for_doc(doc: "BankConnector") -> None:
	"""Shared implementation: discover accounts and populate mappings.

	Args:
	    doc: A ``Bank Connector`` document instance.
	"""
	from erpnext_bank_import.connectors import get_connector
	from erpnext_bank_import.connectors.exceptions import AuthenticationError

	config = doc.get_connector_config()
	try:
		connector = get_connector(doc.provider_name, config=config)
	except Exception as e:
		frappe.throw(frappe._("Failed to initialise connector: {0}").format(e))

	try:
		if hasattr(connector, "authenticate"):
			connector.authenticate()
		if hasattr(connector, "set_current_bank_account"):
			connector.set_current_bank_account(doc.connector_name)
		accounts = connector.get_accounts()
	except AuthenticationError:
		frappe.throw(
			frappe._(
				"Authentication failed. Please complete the OAuth2 flow "
				"first using the consent URL from start_oauth_flow."
			)
		)
	except Exception as e:
		frappe.throw(frappe._("Failed to fetch accounts: {0}").format(e))

	if not accounts:
		frappe.msgprint(
			frappe._("No active accounts found for this connector."),
			title=frappe._("Account Discovery"),
		)
		return

	existing_ids = {m.provider_account_id for m in doc.account_mappings if m.provider_account_id}

	added = 0
	for acc in accounts:
		if acc.account_id in existing_ids:
			continue
		doc.append(
			"account_mappings",
			{
				"provider_account_id": acc.account_id,
				"provider_account_name": acc.account_name,
				"currency": acc.currency,
				"is_enabled": 1,
			},
		)
		added += 1

	if added:
		doc.save(ignore_permissions=True)
		frappe.msgprint(
			frappe._("{0} account(s) added. Please select the Bank Account for each.").format(added),
			title=frappe._("Account Discovery"),
			indicator="green",
		)
	else:
		frappe.msgprint(
			frappe._("All discovered accounts are already mapped."),
			title=frappe._("Account Discovery"),
		)


@frappe.whitelist()
def import_transactions_action(doc: str | None = None, **kwargs) -> None:
	"""Server Action entry point for the Import Transactions button."""
	doc_data = _resolve_doc(doc)
	doc_obj = frappe.get_doc("Bank Connector", doc_data.get("connector_name"))
	doc_obj.import_transactions_action()


@frappe.whitelist()
def check_import_health_action(doc: str | None = None, **kwargs) -> None:
	"""Server Action entry point for the Check Import Health button."""
	doc_data = _resolve_doc(doc)
	doc_obj = frappe.get_doc("Bank Connector", doc_data.get("connector_name"))
	doc_obj.check_import_health_action()


@frappe.whitelist()
def fetch_accounts_action(doc: str | None = None, **kwargs) -> None:
	"""Server Action entry point for the Fetch Accounts button."""
	doc_data = _resolve_doc(doc)
	doc_obj = frappe.get_doc("Bank Connector", doc_data.get("connector_name"))
	_fetch_accounts_for_doc(doc_obj)


def _resolve_doc(doc: str | None) -> dict:
	"""Resolve the doc parameter — it may be a JSON string or None.

	Frappe v16 Server Actions send the full document as a JSON-encoded
	string under the ``doc`` key in the request.
	"""
	if doc and isinstance(doc, str):
		import json
		return json.loads(doc)
	return frappe.form_dict or {}


__all__ = [
	"BankConnector",
	"check_import_health_action",
	"fetch_accounts_action",
	"import_transactions_action",
]
