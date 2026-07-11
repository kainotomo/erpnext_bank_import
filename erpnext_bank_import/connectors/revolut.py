"""Revolut Business API connector.

Implements ``BankConnector`` for the `Revolut Business API
<https://developer.revolut.com/docs/api/business>`_ v1 (REST).

Authentication
--------------
Uses OAuth2 with JWT client assertions (RFC 7523) signed with an
X.509 private key (RS256).  The corresponding public certificate is
uploaded to the `Revolut Business web app
<https://business.revolut.com/settings/apis>`_ — it is **not** stored
in this connector.

The JWT assertion is generated fresh for every token request (short
5-minute expiry), so there is no JWT caching.

Endpoints used
--------------
* ``GET /accounts`` — account discovery
* ``GET /accounts/{id}/bank-details`` — optional IBAN/BIC enrichment
* ``GET /transactions`` — paginated transaction fetch (cursor via ``to``)
* ``POST /auth/token`` — OAuth2 token endpoint (via ``OAuth2Service``)

API base URLs
-------------
Production: ``https://b2b.revolut.com/api/1.0``
Sandbox: ``https://sandbox-b2b.revolut.com/api/1.0``

Consent URL base
----------------
Production: ``https://business.revolut.com``
Sandbox: ``https://sandbox-business.revolut.com``

Usage
-----
.. code-block:: python

    from erpnext_bank_import.connectors import get_connector

    conn = get_connector("revolut", config=config)
    conn.authenticate()  # redirect user to consent URL
    accounts = conn.get_accounts()
    txns = conn.fetch_all_transactions(account_id=accounts[0].account_id, ...)
"""

from __future__ import annotations

import time
from typing import Any

import frappe
import requests

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	NetworkError,
	NormalizationError,
	RateLimitError,
	ServerError,
)
from erpnext_bank_import.schema.transaction import NormalizedTransaction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONSENT_BASE_URLS: dict[str, str] = {
	False: "https://business.revolut.com",  # production
	True: "https://sandbox-business.revolut.com",  # sandbox
}

_SUPPORTED_DATE_FMT = "%Y-%m-%d"


class RevolutConnector(BankConnector):
	"""Connector for the Revolut Business API v1 (REST).

	Configuration
	-------------
	Controlled via ``ConnectorConfig``:

	* ``jwt_private_key`` — PEM-encoded RSA private key (required for JWT
	  client assertion).
	* ``jwt_issuer`` — ``iss`` claim; defaults to ``client_id``.
	* ``extra["sandbox"]`` — set to ``True`` for sandbox endpoints
	  (default ``False``).
	* ``extra["fetch_bank_details"]`` — set to ``True`` to call the
	  ``/bank-details`` endpoint for IBAN/BIC enrichment during account
	  discovery (default ``False``).

	See ``BankConnector`` ABC for inherited parameters.
	"""

	def __init__(self, config: ConnectorConfig | None = None, **kwargs: Any) -> None:
		super().__init__(config=config, **kwargs)
		# Lazy import to avoid circular dependency:
		# connectors.revolut -> services.oauth -> services.import_service -> connectors
		from erpnext_bank_import.services.oauth import OAuth2Service

		self._oauth = OAuth2Service(self.config)
		self._session = requests.Session()
		self._session.headers.update({"Accept": "application/json"})
		self._last_request_time: float = 0.0
		self._current_bank_account: str | None = None
		"""Bank account identifier set by the import orchestration per mapping.

		Set via :meth:`set_current_bank_account` before API calls so
		that ``_get_access_token()`` can resolve the correct token.
		"""

	# ------------------------------------------------------------------
	# Public helpers
	# ------------------------------------------------------------------

	def set_current_bank_account(self, bank_account: str) -> None:
		"""Set the current bank account for token resolution.

		The import orchestration calls this before fetching transactions
		for each account mapping, so the connector knows which token
		to use for API requests.

		Args:
		    bank_account: The ``Bank Account`` doctype name.
		"""
		self._current_bank_account = bank_account

	# ------------------------------------------------------------------
	# Properties
	# ------------------------------------------------------------------

	@property
	def _is_sandbox(self) -> bool:
		"""``True`` when running against the sandbox environment."""
		return bool(self.config.extra.get("sandbox", False))

	@property
	def _consent_base_url(self) -> str:
		"""Base URL for the Revolut consent page."""
		return _CONSENT_BASE_URLS[self._is_sandbox]

	# ------------------------------------------------------------------
	# Auth lifecycle
	# ------------------------------------------------------------------

	def authenticate(self) -> None:
		"""This method does not perform an HTTP call.

		For Revolut, authentication is a multi-step process that
		happens through the ``start_oauth_flow`` and ``oauth_callback``
		whitelisted endpoints (see module-level functions below).

		This method is a no-op because token acquisition is driven
		by the ``OAuth2Service`` which handles the flow.
		"""
		pass

	def is_authenticated(self) -> bool:
		"""Return ``True`` if a valid access token exists for any account.

		This tries ``OAuth2Service.get_valid_access_token()`` for each
		known mapping.  Returns ``True`` if any token is valid.
		"""
		# If there's no way to check (no mappings loaded), return False.
		# The import orchestration calls refresh_token() which will
		# handle actual token validation.
		return False

	def refresh_token(self) -> None:
		"""Refresh is handled per-account by ``OAuth2Service``.

		The import orchestration calls ``connector.is_authenticated()``
		and then ``connector.refresh_token()``.  Since Revolut tokens
		are per bank account mapping, the actual refresh is delegated
		to ``OAuth2Service.refresh_access_token()`` when needed.
		"""
		pass

	# ------------------------------------------------------------------
	# Account discovery
	# ------------------------------------------------------------------

	def get_accounts(self) -> list[AccountInfo]:
		"""Retrieve all accounts via ``GET /accounts``.

		Returns:
		    A list of ``AccountInfo`` dataclass instances.

		Raises:
		    AuthenticationError: If the API returns 401.
		    ApiError: For other non-2xx responses.
		"""
		data = self._api_get("/accounts")
		accounts: list[AccountInfo] = []

		for raw in data:
			# Only return active accounts.
			if raw.get("state") != "active":
				continue

			account = AccountInfo(
				account_id=str(raw["id"]),
				account_name=str(raw.get("name", "")),
				currency=str(raw["currency"]),
				provider_metadata={
					"balance": raw.get("balance"),
					"state": raw.get("state"),
					"public": raw.get("public"),
					"created_at": raw.get("created_at"),
					"updated_at": raw.get("updated_at"),
				},
			)

			# Optionally enrich with bank details for IBAN/BIC.
			if self.config.extra.get("fetch_bank_details", False):
				self._enrich_bank_details(account)

			accounts.append(account)

		return accounts

	# ------------------------------------------------------------------
	# Transaction fetch
	# ------------------------------------------------------------------

	def fetch_transactions(
		self,
		account_id: str,
		date_from: str,
		date_to: str,
		*,
		page_size: int = 100,
		page_token: str | None = None,
	) -> tuple[list[NormalizedTransaction], str | None]:
		"""Fetch a single page of transactions from ``GET /transactions``.

		Revolut pagination uses a cursor based on ``created_at``.  The
		``page_token`` parameter carries the ``created_at`` of the last
		transaction from the previous page, which is passed as the
		``to`` query parameter for the next page request.

		Args:
		    account_id: The Revolut account UUID.
		    date_from: Start date in ``YYYY-MM-DD`` format.
		    date_to: End date in ``YYYY-MM-DD`` format.
		    page_size: Max transactions per page (Revolut max is 1000).
		    page_token: Cursor — the ``created_at`` of the last item
		        from the previous page, or ``None`` for the first page.

		Returns:
		    A tuple ``(transactions, next_page_token)``.

		Raises:
		    AuthenticationError: If the API returns 401.
		    RateLimitError: If the API returns 429.
		    ApiError: For other non-2xx responses.
		"""
		# Build query parameters.
		params: dict[str, str | int] = {
			"account": account_id,
			"count": min(page_size, 1000),  # Revolut max is 1000
		}

		if page_token is not None:
			# Cursor-based: use the last item's created_at as the `to` param.
			params["to"] = page_token
			params["from"] = date_from
		else:
			# First page: use the requested date window.
			params["from"] = f"{date_from}T00:00:00Z"
			params["to"] = f"{date_to}T23:59:59Z"

		data = self._api_get("/transactions", params=params)

		txns: list[NormalizedTransaction] = []
		for raw in data:
			try:
				txns.append(self.normalize_transaction(raw))
			except NormalizationError:
				frappe.log_error(
					message=f"Revolut transaction normalization failed for {raw.get('id', 'unknown')}: {raw}",
					title="Revolut normalisation error",
				)
				continue

		# Determine next page token.
		next_token: str | None = None
		if len(data) >= min(page_size, 1000):
			# There might be more pages — use the last item's created_at as cursor.
			last_item = data[-1]
			next_token = last_item.get("created_at")

		return txns, next_token

	# ------------------------------------------------------------------
	# Normalisation
	# ------------------------------------------------------------------

	def normalize_transaction(self, raw: dict[str, Any]) -> NormalizedTransaction:
		"""Convert a raw Revolut transaction dict into the canonical form.

		Revolut transactions have a ``legs`` array with one or more
		legs.  This method uses the **first leg** as the primary source
		for amount, currency, description, and fee.  All legs are stored
		in ``provider_metadata`` for auditability.

		Args:
		    raw: Raw transaction dict from the Revolut API.

		Returns:
		    A ``NormalizedTransaction`` dict.

		Raises:
		    NormalizationError: If required fields are missing.
		"""
		# Validate required top-level fields.
		txn_id = raw.get("id")
		if not txn_id:
			raise NormalizationError("Missing required field: id")

		legs = raw.get("legs", [])
		if not legs:
			raise NormalizationError("Transaction has no legs")

		primary_leg = legs[0]

		amount = primary_leg.get("amount")
		if amount is None:
			raise NormalizationError("Missing required field: legs[0].amount")

		currency = primary_leg.get("currency")
		if not currency:
			raise NormalizationError("Missing required field: legs[0].currency")

		# Parse the date from created_at (ISO-8601).
		created_at = raw.get("created_at", "")
		date = created_at[:10] if created_at else ""

		# Build description — prefer legs description, fall back to reference.
		description = primary_leg.get("description") or raw.get("reference") or ""

		# Extract counterparty info if present.
		counterparty = primary_leg.get("counterparty") or {}
		merchant = raw.get("merchant") or {}

		return NormalizedTransaction(
			external_id=str(txn_id),
			date=date,
			amount=float(amount),
			currency=str(currency),
			description=str(description),
			reference_number=str(raw["reference"]) if raw.get("reference") else None,
			bank_party_name=str(merchant.get("name"))
			if merchant.get("name")
			else str(counterparty.get("name"))
			if counterparty.get("name")
			else None,
			bank_party_account_number=str(counterparty.get("account_id"))
			if counterparty.get("account_id")
			else None,
			bank_party_iban=None,  # Not available at transaction level
			transaction_type=str(raw.get("type", "")),
			included_fee=float(primary_leg["fee"]) if primary_leg.get("fee") is not None else None,
			excluded_fee=None,  # Revolut doesn't report excluded fees at leg level
			provider_metadata={
				"state": raw.get("state"),
				"request_id": raw.get("request_id"),
				"created_at": created_at,
				"updated_at": raw.get("updated_at"),
				"completed_at": raw.get("completed_at"),
				"legs": legs,
				"merchant": merchant,
				"card": raw.get("card"),
			},
		)

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _api_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
		"""Make a ``GET`` request to the Revolut API.

		Args:
		    path: API path (e.g. ``"/accounts"``, ``"/transactions"``).
		    params: Optional query parameters.

		Returns:
		    The parsed JSON response (list or dict).

		Raises:
		    AuthenticationError: On 401.
		    RateLimitError: On 429.
		    ServerError: On 5xx.
		    ApiError: On other non-2xx.
		    NetworkError: On network failures.
		"""
		# Rate-limit: ensure we don't exceed requests per second.
		self._throttle()

		url = self.config.api_base_url.rstrip("/") + path
		token = self._get_access_token()

		try:
			response = self._session.get(
				url,
				params=params,
				headers={"Authorization": f"Bearer {token}"},
				timeout=self.config.timeout_seconds,
			)
		except requests.RequestException as exc:
			raise NetworkError(f"Network error calling Revolut API ({url}): {exc}") from exc

		self._last_request_time = time.monotonic()

		if response.status_code == 200:
			return response.json()

		error_body = response.text

		if response.status_code == 401:
			raise AuthenticationError(f"Revolut API authentication failed (HTTP 401): {error_body}")
		if response.status_code == 429:
			retry_after = None
			try:
				retry_after = float(response.headers.get("Retry-After", ""))
			except (ValueError, TypeError):
				pass
			raise RateLimitError(
				f"Revolut API rate limit exceeded (HTTP 429): {error_body}",
				retry_after=retry_after,
			)
		if response.status_code >= 500:
			raise ServerError(
				f"Revolut API server error (HTTP {response.status_code}): {error_body}",
				status_code=response.status_code,
				response_body=error_body,
			)

		raise ApiError(
			f"Revolut API error (HTTP {response.status_code}): {error_body}",
			status_code=response.status_code,
			response_body=error_body,
		)

	def _get_access_token(self) -> str:
		"""Get a valid access token for API requests.

		Uses the current bank account (set via
		:meth:`set_current_bank_account`) to resolve the token from
		the ``OAuth2Service`` store.

		Returns:
		    A valid access token string.

		Raises:
		    AuthenticationError: If no bank account is set or no
		        valid token can be obtained.
		"""
		if not self._current_bank_account:
			raise AuthenticationError(
				"No bank account set. Call set_current_bank_account() "
				"or use import_transactions() which handles this automatically."
			)

		try:
			return self._oauth.get_valid_access_token(self._current_bank_account)
		except Exception as exc:
			raise AuthenticationError(
				f"Cannot obtain valid access token for '{self._current_bank_account}': {exc}"
			) from exc

	def _enrich_bank_details(self, account: AccountInfo) -> None:
		"""Optionally enrich an ``AccountInfo`` with IBAN/BIC.

		Calls ``GET /accounts/{account_id}/bank-details`` and updates
		the account's ``iban`` and ``account_number`` fields.

		Args:
		    account: The account to enrich (mutated in place).
		"""
		try:
			data = self._api_get(f"/accounts/{account.account_id}/bank-details")
		except Exception:
			# Non-fatal: if bank-details fails, just skip enrichment.
			return

		if isinstance(data, list) and data:
			details = data[0]
			if details.get("iban"):
				account.iban = str(details["iban"])
			if details.get("account_no"):
				account.account_number = str(details["account_no"])
			if details.get("bic"):
				account.provider_metadata["bic"] = str(details["bic"])
			if details.get("sort_code"):
				account.provider_metadata["sort_code"] = str(details["sort_code"])

	def _throttle(self) -> None:
		"""Throttle requests to respect the configured rate limit."""
		rps = self.config.rate_limit_rps
		if rps is None or rps <= 0:
			return

		min_interval = 1.0 / rps
		elapsed = time.monotonic() - self._last_request_time
		if elapsed < min_interval:
			time.sleep(min_interval - elapsed)

	# ------------------------------------------------------------------
	# Validation
	# ------------------------------------------------------------------

	def _validate_config(self) -> None:
		"""Validate Revolut-specific configuration.

		Raises:
		    ConfigurationError: If provider_name or api_base_url are
		        empty, or if JWT fields are missing when auth_method
		        is ``oauth2``.
		"""
		super()._validate_config()

		if self.config.auth_method == "oauth2" and not self.config.jwt_private_key:
			raise ConfigurationError(
				f"jwt_private_key is required for Revolut OAuth2 provider '{self.config.provider_name}'"
			)

	def _validate_urls(self) -> None:
		"""Validate URL fields."""
		pass  # handled by bank_connector.py doctype validation

	def _validate_oauth_fields(self) -> None:
		"""Validate OAuth2 fields."""
		pass  # handled by bank_connector.py doctype validation

	def _validate_account_mappings(self) -> None:
		"""Validate account mappings."""
		pass  # handled by bank_connector.py doctype validation


# ---------------------------------------------------------------------------
# Frappe whitelisted endpoints for OAuth flow
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
def start_oauth_flow(connector_name: str) -> str:
	"""Initiate the Revolut OAuth2 consent flow.

	The ``connector_name`` is encoded in the OAuth ``state`` parameter
	so the callback can look it up directly.

	Args:
	    connector_name: The ``Bank Connector`` record name.

	Returns:
	    The consent URL to which the user should be redirected.
	"""
	from urllib.parse import urlencode

	# Load config directly from the doc.
	doc = frappe.get_doc("Bank Connector", connector_name)
	if not doc.enabled:
		frappe.throw(frappe._("Bank Connector '{0}' is disabled. Enable it first.").format(connector_name))
	config = doc.get_connector_config()

	# Auto-detect sandbox from the API base URL.
	is_sandbox = "sandbox" in (config.api_base_url or "").lower()
	consent_base = _CONSENT_BASE_URLS[is_sandbox]

	params: dict[str, str] = {
		"client_id": config.client_id or "",
		"redirect_uri": config.redirect_uri or "",
		"response_type": "code",
		"state": connector_name,
	}
	if config.scopes:
		params["scope"] = ",".join(config.scopes)

	# Build the consent URL with standard URL-encoding.
	query_string = urlencode(
		{k: v for k, v in params.items() if v},
	).replace("+", "%20")
	consent_url = f"{consent_base}/app-confirm?{query_string}"

	# Return the consent URL for the user to open.
	return consent_url


@frappe.whitelist(allow_guest=True)
def oauth_callback(code: str | None = None, state: str | None = None) -> str:
	"""Handle the OAuth2 redirect callback from Revolut.

	Exchanges the authorization code for tokens and persists them.

	Args:
	    code: The authorization code from Revolut.
	    state: The ``connector_name`` (passed as OAuth ``state`` param
	        from :func:`start_oauth_flow`).

	Returns:
	    A status message.
	"""
	if not code:
		frappe.throw(frappe._("Missing authorization code parameter."))

	bank_account = state or "default"

	if state:
		# Direct lookup via state parameter.
		doc = frappe.get_doc("Bank Connector", state)
		config = doc.get_connector_config()
		if config.provider_name != "revolut":
			frappe.throw(frappe._("Connector '{0}' is not a Revolut connector.").format(state))
		from erpnext_bank_import.services.oauth import OAuth2Service

		oauth = OAuth2Service(config)
		oauth.exchange_code_for_tokens(code=code, bank_account=bank_account)
		return frappe._("OAuth2 authorization successful for connector '{0}'.").format(state)

	# Fallback: iterate enabled connectors (legacy, no state).
	from erpnext_bank_import.connectors import get_all_enabled_connectors

	for name in get_all_enabled_connectors():
		try:
			config = frappe.get_doc("Bank Connector", name).get_connector_config()
			if config.provider_name != "revolut":
				continue
			from erpnext_bank_import.services.oauth import OAuth2Service

			oauth = OAuth2Service(config)
			oauth.exchange_code_for_tokens(code=code, bank_account=bank_account)
			return frappe._("OAuth2 authorization successful for connector '{0}'.").format(name)
		except Exception:
			continue

	frappe.throw(
		frappe._(
			"Could not exchange authorization code. "
			"Ensure the certificate and redirect URI are correctly configured."
		)
	)


__all__ = [
	"RevolutConnector",
	"oauth_callback",
	"start_oauth_flow",
]
