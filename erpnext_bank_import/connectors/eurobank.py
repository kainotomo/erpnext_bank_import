"""Eurobank (Hellenic Bank) B2B API connector.

Implements ``BankConnector`` for the merged Eurobank Cyprus / Hellenic Bank
entity's B2B API (v2).

Authentication
--------------
Uses OAuth2 Authorization Code flow with **query-parameter** token requests
and **HTTP Basic Auth** — this differs from standard OAuth2 where parameters
are sent in the POST body.  The shared ``OAuth2Service`` supports both styles
via the ``token_request_style`` config field.

No JWT client assertion is needed (unlike Revolut).  The ``client_secret``
is sent via HTTP Basic Auth header during token exchange.

Endpoints used (sandbox)
------------------------
* ``GET /v2/b2b/accounts`` — account discovery
* ``POST /v2/oauth2/auth`` — authorization URL
* ``POST /v2/token/exchange`` — token code exchange
* ``POST /v2/token`` — token refresh

API base URLs
-------------
Sandbox services: ``https://sandbox-apis.hellenicbank.com``
Sandbox OAuth: ``https://sandbox-oauth.hellenicbank.com``
Production services: ``https://apisprod.hellenicbank.com``
Production OAuth: ``https://oauthprod.hellenicbank.com``

Usage
-----
.. code-block:: python

    from erpnext_bank_import.connectors import get_connector

    conn = get_connector("eurobank", config=config)
    accounts = conn.get_accounts()
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import frappe
import requests
from frappe import _

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConfigurationError,
	NetworkError,
	RateLimitError,
	ServerError,
)
from erpnext_bank_import.schema.transaction import NormalizedTransaction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_API_BASE = "https://sandbox-apis.hellenicbank.com"
_SANDBOX_OAUTH_BASE = "https://sandbox-oauth.hellenicbank.com"

_PROD_API_BASE = "https://apisprod.hellenicbank.com"
_PROD_OAUTH_BASE = "https://oauthprod.hellenicbank.com"

#: Default B2B scopes for account information (accounts + details + transactions).
_DEFAULT_SCOPES = [
	"v2.b2b.get.accounts",
	"v2.b2b.get.account.details",
	"v2.b2b.get.account.transactions",
]


class EurobankConnector(BankConnector):
	"""Connector for Eurobank (Hellenic Bank) B2B API v2.

	Configuration
	-------------
	Controlled via ``ConnectorConfig``:

	* ``extra["sandbox"]`` — set to ``True`` for sandbox endpoints
	  (default ``False``).
	* ``extra["token_request_style"]`` — set to ``"query"`` (Eurobank
	  sends token params as URL query params with Basic Auth).
	* ``extra["scope_separator"]`` — set to ``","`` (Eurobank uses
	  comma-separated scopes).

	See ``BankConnector`` ABC for inherited parameters.
	"""

	def __init__(self, config: ConnectorConfig | None = None, **kwargs: Any) -> None:
		super().__init__(config=config, **kwargs)
		# Lazy import to avoid circular dependency.
		from erpnext_bank_import.services.oauth import OAuth2Service

		self._oauth = OAuth2Service(self.config)
		self._session = requests.Session()
		self._session.headers.update({
			"Accept": "application/json",
			"x-client-id": self.config.client_id or "",
		})
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
	# Auth lifecycle
	# ------------------------------------------------------------------

	def authenticate(self) -> None:
		"""This method does not perform an HTTP call.

		For Eurobank, authentication is a multi-step process that
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
		return False

	def refresh_token(self) -> None:
		"""Refresh is handled per-account by ``OAuth2Service``.

		The import orchestration calls ``connector.is_authenticated()``
		and then ``connector.refresh_token()``.  Since Eurobank tokens
		are per connector, the actual refresh is delegated to
		``OAuth2Service.refresh_access_token()`` when needed.
		"""
		pass

	# ------------------------------------------------------------------
	# Account discovery
	# ------------------------------------------------------------------

	def get_accounts(self) -> list[AccountInfo]:
		"""Retrieve all accounts via ``GET /v2/b2b/accounts``.

		Returns:
		    A list of ``AccountInfo`` dataclass instances.

		Raises:
		    AuthenticationError: If the API returns 401.
		    ApiError: For other non-2xx responses.
		"""
		data = self._api_get("/v2/b2b/accounts")
		accounts: list[AccountInfo] = []

		# The response wraps accounts in payload.accounts[].
		payload = data.get("payload") or {}
		raw_accounts = payload.get("accounts") or []

		for raw in raw_accounts:
			status = raw.get("status", "").upper()
			if status != "ACTIVE":
				continue

			account = AccountInfo(
				account_id=str(raw.get("accountNumber", "")),
				account_name=str(raw.get("accountName", "")),
				currency=str(raw.get("currency", "")),
				iban=str(raw.get("iban")) if raw.get("iban") else None,
				account_number=str(raw.get("accountNumber")) if raw.get("accountNumber") else None,
				provider_metadata={
					"accountType": raw.get("accountType"),
					"accountTypeDescription": raw.get("accountTypeDescription"),
					"status": raw.get("status"),
					"balances": raw.get("balances"),
					"subscriberName": payload.get("subscriberName"),
				},
			)
			accounts.append(account)

		return accounts

	# ------------------------------------------------------------------
	# Transaction fetch (not yet implemented)
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
		"""Transaction fetching is not yet implemented for Eurobank.

		Raises:
		    NotImplementedError: Always — this will be implemented in a
		        follow-up PR.
		"""
		raise NotImplementedError(
			"Transaction fetching for Eurobank is not yet implemented. "
			"Use the auth + account discovery flow to obtain consent, then "
			"the GET /v2/b2b/accounts/{accountID}/transactions endpoint "
			"in a future update."
		)

	# ------------------------------------------------------------------
	# Normalisation (not yet implemented)
	# ------------------------------------------------------------------

	def normalize_transaction(self, raw: dict[str, Any]) -> NormalizedTransaction:
		"""Transaction normalisation is not yet implemented for Eurobank.

		Raises:
		    NotImplementedError: Always — this will be implemented in a
		        follow-up PR.
		"""
		raise NotImplementedError(
			"Transaction normalisation for Eurobank is not yet implemented."
		)

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _api_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
		"""Make a ``GET`` request to the Eurobank API.

		Args:
		    path: API path (e.g. ``"/v2/b2b/accounts"``).
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
			raise NetworkError(f"Network error calling Eurobank API ({url}): {exc}") from exc

		self._last_request_time = time.monotonic()

		if response.status_code == 200:
			return response.json()

		error_body = response.text

		if response.status_code == 401:
			raise AuthenticationError(f"Eurobank API authentication failed (HTTP 401): {error_body}")
		if response.status_code == 429:
			retry_after = None
			try:
				retry_after = float(response.headers.get("Retry-After", ""))
			except (ValueError, TypeError):
				pass
			raise RateLimitError(
				f"Eurobank API rate limit exceeded (HTTP 429): {error_body}",
				retry_after=retry_after,
			)
		if response.status_code >= 500:
			raise ServerError(
				f"Eurobank API server error (HTTP {response.status_code}): {error_body}",
				status_code=response.status_code,
				response_body=error_body,
			)

		raise ApiError(
			f"Eurobank API error (HTTP {response.status_code}): {error_body}",
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
				f"No bank account set for provider '{self.config.provider_name}'. "
				"Call set_current_bank_account() "
				"or use import_transactions() which handles this automatically."
			)

		try:
			return self._oauth.get_valid_access_token(self._current_bank_account)
		except Exception as exc:
			raise AuthenticationError(
				f"Cannot obtain valid access token for provider '{self.config.provider_name}', "
				f"bank account '{self._current_bank_account}': {exc}"
			) from exc

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
		"""Validate Eurobank-specific configuration.

		Raises:
		    ConfigurationError: If provider_name or api_base_url are
		        empty, or if OAuth2 fields are missing.
		"""
		super()._validate_config()

		if self.config.auth_method == "oauth2":
			if not self.config.client_id:
				raise ConfigurationError(
					f"client_id is required for Eurobank provider '{self.config.provider_name}'"
				)
			if not self.config.client_secret:
				raise ConfigurationError(
					f"client_secret is required for Eurobank provider '{self.config.provider_name}'"
				)
			if not self.config.authorize_url:
				raise ConfigurationError(
					f"authorize_url is required for Eurobank provider '{self.config.provider_name}'"
				)
			if not self.config.token_url:
				raise ConfigurationError(
					f"token_url is required for Eurobank provider '{self.config.provider_name}'"
				)
			if not self.config.redirect_uri:
				raise ConfigurationError(
					f"redirect_uri is required for Eurobank provider '{self.config.provider_name}'"
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
	"""Initiate the Eurobank OAuth2 consent flow.

	Generates a random ``state`` token and caches the mapping
	``state → connector_name`` in Frappe's cache with a 10-minute TTL.
	The callback uses this mapping to route the authorization code to
	the correct connector.

	Args:
	    connector_name: The ``Bank Connector`` record name.

	Returns:
	    The consent URL to which the user should be redirected.
	"""
	doc = frappe.get_doc("Bank Connector", connector_name)
	if not doc.enabled:
		frappe.throw(
			frappe._("Bank Connector '{0}' is disabled. Enable it first.").format(connector_name)
		)
	config = doc.get_connector_config()

	# Generate a random state and cache it (10-min TTL).
	state = str(uuid.uuid4())
	cache_key = f"eurobank_oauth_state:{state}"
	frappe.cache().setex(cache_key, 600, connector_name)

	from erpnext_bank_import.services.oauth import OAuth2Service

	oauth = OAuth2Service(config)
	return oauth.get_authorize_url(state=state)


@frappe.whitelist(allow_guest=True)
def oauth_callback(
	code: str | None = None,
	state: str | None = None,
	connector: str | None = None,
) -> dict:
	"""Handle the OAuth2 redirect callback from Eurobank.

	Exchanges the authorization code for tokens and persists them.
	Uses the ``state`` parameter to look up the correct connector.

	Args:
	    code: The authorization code from Eurobank.
	    state: The state parameter returned by Eurobank (used to
	        look up the connector from cache).
	    connector: Ignored (legacy parameter for future use).

	Returns:
	    A dict with a ``message`` key.
	"""
	if not code:
		frappe.throw(frappe._("Missing authorization code parameter."))

	if not state:
		frappe.throw(frappe._("Missing state parameter."))

	# Look up the connector from cache using state.
	cache_key = f"eurobank_oauth_state:{state}"
	connector_name = frappe.cache().get(cache_key)
	if not connector_name:
		frappe.throw(
			frappe._(
				"OAuth state expired or invalid. Please start the OAuth flow again."
			)
		)

	# Frappe's Redis cache may return bytes — decode to string.
	if isinstance(connector_name, bytes):
		connector_name = connector_name.decode("utf-8")

	# Clear the cached state immediately (one-time use).
	frappe.cache().delete(cache_key)

	try:
		doc = frappe.get_doc("Bank Connector", connector_name)
		config = doc.get_connector_config()

		from erpnext_bank_import.services.oauth import OAuth2Service

		oauth = OAuth2Service(config)
		oauth.exchange_code_for_tokens(code=code, bank_account=connector_name)

		return {
			"message": frappe._(
				"OAuth2 authorization successful for connector '{0}'. You can close this window."
			).format(connector_name)
		}
	except Exception as exc:
		frappe.log_error(
			message=f"Eurobank OAuth callback failed for state '{state}': {exc}",
			title="Eurobank OAuth callback error",
		)
		frappe.throw(
			frappe._(
				"Could not exchange authorization code. "
				"Check the Error Log for details."
			)
		)
