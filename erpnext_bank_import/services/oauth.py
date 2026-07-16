"""Shared OAuth2 token lifecycle service.

This module provides the provider-agnostic OAuth2 token lifecycle that
bank connectors (e.g. Revolut) use to manage authentication:

* Building authorization URLs
* Exchanging authorization codes for tokens
* Refreshing expired access tokens
* Revoking tokens
* Persisting tokens via the ``Bank Connector Token`` DocType

Usage
-----
.. code-block:: python

    from erpnext_bank_import.services import OAuth2Service, OAuthProviderConfig
    from erpnext_bank_import.connectors.config import ConnectorConfig

    config = ConnectorConfig(
        provider_name="revolut",
        api_base_url="https://api.revolut.com",
        client_id="abc123",
        client_secret="secret",
        authorize_url="/auth/authorize",
        token_url="/auth/token",
        revoke_url="/auth/revoke",
        scopes=["transactions:read"],
        redirect_uri="https://my-erpnext.example.com/callback",
    )
    svc = OAuth2Service(config)

    # Step 1: Redirect user to authorize URL
    auth_url = svc.get_authorize_url(state="random-state-123")

    # Step 2: Exchange code for tokens
    svc.exchange_code_for_tokens(code="auth-code-xyz", bank_account="BA-001")

    # Step 3: Use valid token (auto-refresh if expired)
    token = svc.get_valid_access_token(bank_account="BA-001")
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin

import frappe
import jwt
import requests
from frappe.utils import get_datetime, now_datetime

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
	ConfigurationError,
	OAuthHandshakeError,
	TokenExpiredError,
	TokenRevokedError,
)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class OAuthToken:
	"""Represents an OAuth2 access and refresh token pair."""

	access_token: str
	"""The access token used to authenticate API requests."""

	refresh_token: str | None = None
	"""The refresh token used to obtain a new access token when expired."""

	token_type: str = "Bearer"
	"""Token type (e.g. ``"Bearer"``)."""

	expires_at: datetime | None = None
	"""Timestamp when the access token expires (UTC)."""

	scope: str | None = None
	"""Space-separated list of granted scopes."""

	provider_metadata: dict[str, Any] = field(default_factory=dict)
	"""Opaque bag for provider-specific token metadata."""


@dataclass
class OAuthProviderConfig:
	"""Extracts and validates OAuth2-specific configuration from ``ConnectorConfig``.

	This is a typed view over a subset of ``ConnectorConfig`` fields —
	it does not introduce new configuration, only validates that the
	existing config has what the OAuth service needs.
	"""

	authorize_url: str
	"""Full OAuth2 authorization endpoint URL."""

	token_url: str
	"""Full OAuth2 token endpoint URL."""

	client_id: str
	"""OAuth2 client identifier."""

	client_secret: str | None = None
	"""OAuth2 client secret."""

	revoke_url: str | None = None
	"""Full OAuth2 token revocation endpoint URL."""

	scopes: list[str] = field(default_factory=list)
	"""OAuth2 scopes requested during authorization."""

	redirect_uri: str | None = None
	"""OAuth2 redirect URI for the authorization code flow."""

	token_safety_buffer_seconds: int = 60
	"""Seconds before hard expiry to treat a token as expired."""

	jwt_private_key: str | None = None
	"""PEM-encoded RSA private key for JWT client assertion (RFC 7523).

    When set, ``OAuth2Service._token_request()`` builds a signed JWT
    assertion and sends it as ``client_assertion`` instead of using
    ``client_secret``.
    """

	jwt_issuer: str | None = None
	"""Value of the ``iss`` claim in the JWT client assertion.

    Defaults to ``client_id`` if not set.
    """

	@classmethod
	def from_connector_config(cls, config: ConnectorConfig) -> OAuthProviderConfig:
		"""Build an ``OAuthProviderConfig`` from a ``ConnectorConfig``.

		Raises:
		    ConfigurationError: If required OAuth2 fields are missing.
		"""
		if not config.client_id:
			raise ConfigurationError(f"client_id is required for OAuth2 provider '{config.provider_name}'")

		authorize_url = config.authorize_url or ""
		token_url = config.token_url or ""

		# Resolve relative URLs against api_base_url.
		if authorize_url and not authorize_url.startswith("http"):
			authorize_url = urljoin(config.api_base_url.rstrip("/") + "/", authorize_url.lstrip("/"))
		if token_url and not token_url.startswith("http"):
			token_url = urljoin(config.api_base_url.rstrip("/") + "/", token_url.lstrip("/"))

		if not authorize_url:
			raise ConfigurationError(
				f"authorize_url is required for OAuth2 provider '{config.provider_name}'"
			)
		if not token_url:
			raise ConfigurationError(f"token_url is required for OAuth2 provider '{config.provider_name}'")

		revoke_url = config.revoke_url
		if revoke_url and not revoke_url.startswith("http"):
			revoke_url = urljoin(config.api_base_url.rstrip("/") + "/", revoke_url.lstrip("/"))

		return cls(
			authorize_url=authorize_url,
			token_url=token_url,
			client_id=config.client_id,
			client_secret=config.client_secret,
			revoke_url=revoke_url,
			scopes=config.scopes,
			redirect_uri=config.redirect_uri,
			token_safety_buffer_seconds=config.token_safety_buffer_seconds,
			jwt_private_key=config.jwt_private_key,
			jwt_issuer=config.jwt_issuer,
		)


# ---------------------------------------------------------------------------
# OAuth2 service
# ---------------------------------------------------------------------------


class OAuth2Service:
	"""Provider-agnostic OAuth2 token lifecycle service.

	Handles the full OAuth2 authorization code flow:
	authorize → callback (code exchange) → refresh → revoke.

	Tokens are persisted to the ``Bank Connector Token`` DocType
	using encrypted ``Password`` fields for secure storage.

	Args:
	    config: Connector configuration with OAuth2 fields populated.
	"""

	def __init__(self, config: ConnectorConfig) -> None:
		self._provider_config = OAuthProviderConfig.from_connector_config(config)
		self._provider_name = config.provider_name

	# ------------------------------------------------------------------
	# Authorization URL
	# ------------------------------------------------------------------

	def get_authorize_url(self, state: str) -> str:
		"""Build the full OAuth2 authorization URL.

		Args:
		    state: A unique, unpredictable state value used to prevent
		        CSRF attacks.

		Returns:
		    The full authorization URL to which the user's browser
		    should be redirected.
		"""
		params: dict[str, str] = {
			"response_type": "code",
			"client_id": self._provider_config.client_id,
			"state": state,
		}
		if self._provider_config.scopes:
			params["scope"] = " ".join(self._provider_config.scopes)
		if self._provider_config.redirect_uri:
			params["redirect_uri"] = self._provider_config.redirect_uri

		return f"{self._provider_config.authorize_url}?{urlencode(params)}"

	# ------------------------------------------------------------------
	# Code exchange
	# ------------------------------------------------------------------

	def exchange_code_for_tokens(self, code: str, bank_account: str) -> OAuthToken:
		"""Exchange an authorization code for access and refresh tokens.

		The resulting tokens are persisted to the ``Bank Connector Token``
		DocType.

		Args:
		    code: The authorization code received from the OAuth2 callback.
		    bank_account: The bank account identifier to associate with
		        the token.

		Returns:
		    The ``OAuthToken`` containing the new access and refresh tokens.

		Raises:
		    OAuthHandshakeError: If the bank API rejects the code.
		"""
		data: dict[str, str | None] = {
			"grant_type": "authorization_code",
			"code": code,
			"client_id": self._provider_config.client_id,
			"client_secret": self._provider_config.client_secret or "",
			"redirect_uri": self._provider_config.redirect_uri or "",
		}
		response_data = self._token_request(data)
		token = self._parse_token_response(response_data)
		self._persist_tokens(bank_account, token)
		return token

	# ------------------------------------------------------------------
	# Token access (with auto-refresh)
	# ------------------------------------------------------------------

	def get_valid_access_token(self, bank_account: str) -> str:
		"""Return a valid access token, refreshing if necessary.

		If the stored access token is still valid (within the safety
		buffer), it is returned directly.  Otherwise the service
		attempts a refresh using the stored refresh token.

		Args:
		    bank_account: The bank account identifier whose token
		        should be returned.

		Returns:
		    A valid access token string.

		Raises:
		    TokenExpiredError: If the token is expired and no refresh
		        token is available or the refresh attempt fails.
		"""
		token_data = self._load_tokens(bank_account)
		if token_data is None:
			raise TokenExpiredError(
				f"No stored tokens for provider '{self._provider_name}', "
				f"bank account '{bank_account}'. Call exchange_code_for_tokens() first."
			)

		expires_at = token_data.get("expires_at")
		if expires_at and not self._is_expired(_parse_dt(expires_at)):
			return token_data["access_token"]

		# Token is expired or about to expire — try refresh.
		if token_data.get("refresh_token"):
			try:
				token = self.refresh_access_token(bank_account)
				return token.access_token
			except (TokenRevokedError, TokenExpiredError):
				raise
			except Exception as exc:
				frappe.logger().error(
					"OAuth token refresh failed for provider %s, account %s: %s",
					self._provider_name,
					bank_account,
					exc,
				)
				raise TokenExpiredError(
					f"Token refresh failed for '{self._provider_name}' / '{bank_account}'"
				) from exc

		raise TokenExpiredError(
			f"Access token expired and no refresh token available "
			f"for provider '{self._provider_name}', bank account '{bank_account}'."
		)

	# ------------------------------------------------------------------
	# Token refresh
	# ------------------------------------------------------------------

	def refresh_access_token(self, bank_account: str) -> OAuthToken:
		"""Refresh an expired access token using the stored refresh token.

		Args:
		    bank_account: The bank account identifier whose token
		        should be refreshed.

		Returns:
		    The new ``OAuthToken`` with updated access and (optionally)
		    refresh tokens.

		Raises:
		    TokenRevokedError: If the refresh token is revoked or
		        invalid (400/401 response).
		"""
		token_data = self._load_tokens(bank_account)
		if token_data is None or not token_data.get("refresh_token"):
			raise TokenExpiredError(
				f"No refresh token available for provider '{self._provider_name}', "
				f"bank account '{bank_account}'."
			)

		data: dict[str, str | None] = {
			"grant_type": "refresh_token",
			"refresh_token": token_data["refresh_token"],
			"client_id": self._provider_config.client_id,
			"client_secret": self._provider_config.client_secret or "",
		}

		response_data = self._token_request(data)
		token = self._parse_token_response(response_data)
		self._persist_tokens(bank_account, token)
		return token

	# ------------------------------------------------------------------
	# Token revocation
	# ------------------------------------------------------------------

	def revoke_tokens(self, bank_account: str) -> None:
		"""Revoke the stored access and refresh tokens.

		If a ``revoke_url`` is configured, the service POSTs the
		tokens to the revocation endpoint.  Regardless of the result,
		stored token records are cleared.

		Args:
		    bank_account: The bank account whose tokens should be
		        revoked and cleared.
		"""
		if self._provider_config.revoke_url:
			token_data = self._load_tokens(bank_account)
			if token_data:
				try:
					requests.post(
						self._provider_config.revoke_url,
						data={
							"token": token_data.get("access_token", ""),
							"token_type_hint": "access_token",
							"client_id": self._provider_config.client_id,
						},
						timeout=10,
					)
				except requests.RequestException:
					frappe.logger().warning(
						"Token revocation request failed for provider %s, account %s — "
						"clearing stored tokens anyway",
						self._provider_name,
						bank_account,
					)

		self._clear_tokens(bank_account)

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	@staticmethod
	def _is_expired(expires_at: datetime) -> bool:
		"""Check whether a token is expired, accounting for the safety buffer.

		A token is considered expired if ``expires_at`` is in the past
		or less than ``token_safety_buffer_seconds`` away.

		Args:
		    expires_at: The token's expiry timestamp (UTC).

		Returns:
		    True if the token should be considered expired.
		"""
		# Both operands should be offset-naive (Frappe convention).
		# If expires_at happens to be aware, strip timezone for comparison.
		if expires_at.tzinfo is not None:
			expires_at = expires_at.replace(tzinfo=None)
		return expires_at <= now_datetime()

	def _build_client_assertion_jwt(self) -> str:
		"""Build and RS256-sign a JWT client assertion (RFC 7523).

		Per Revolut's specification:
		- Header: ``{"alg": "RS256", "typ": "JWT"}``
		- Payload: ``{"iss": "<issuer>", "sub": "<client_id>",
		  "aud": "https://revolut.com", "exp": <now+300>,
		  "iat": <now>, "jti": "<uuid>"}``

		A fresh JWT is generated on every call — the expiry is kept
		short (5 minutes) per Revolut's best practice.

		Returns:
		    The signed JWT string.

		Raises:
		    ConfigurationError: If the private key is missing.
		"""
		cfg = self._provider_config
		if not cfg.jwt_private_key:
			raise ConfigurationError(
				f"jwt_private_key is required for JWT client assertion (provider '{self._provider_name}')"
			)

		now = int(datetime.now().timestamp())
		issuer = cfg.jwt_issuer or cfg.client_id

		payload: dict[str, Any] = {
			"iss": issuer,
			"sub": cfg.client_id,
			"aud": "https://revolut.com",
			"exp": now + 300,  # 5-minute expiry
			"iat": now,
			"jti": str(uuid.uuid4()),
		}

		return jwt.encode(payload, key=cfg.jwt_private_key, algorithm="RS256")

	def _token_request(self, data: dict[str, str | None]) -> dict[str, Any]:
		"""Make a POST request to the token endpoint and handle responses.

		If the provider config contains a ``jwt_private_key``, a JWT
		client assertion (RFC 7523) is generated fresh for each call
		and sent as ``client_assertion`` instead of ``client_secret``.

		Args:
		    data: The form-encoded body to send to the token endpoint.

		Returns:
		    The parsed JSON response dict.

		Raises:
		    OAuthHandshakeError: If the endpoint returns a non-2xx
		        status for an authorization code exchange.
		    TokenRevokedError: If the endpoint returns a 400/401
		        during a refresh token grant.
		"""
		# If JWT client assertion is configured, build a fresh JWT and
		# replace client_secret with the assertion.
		if self._provider_config.jwt_private_key:
			data = dict(data)
			data.pop("client_secret", None)
			data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
			data["client_assertion"] = self._build_client_assertion_jwt()

		grant_type = data.get("grant_type", "")

		try:
			response = requests.post(
				self._provider_config.token_url,
				data={k: v for k, v in data.items() if v is not None},
				timeout=self._provider_config.token_safety_buffer_seconds,
			)
		except requests.RequestException as exc:
			frappe.logger().error(
				"OAuth token request failed for provider %s (grant_type=%s, url=%s): %s",
				self._provider_name,
				grant_type,
				self._provider_config.token_url,
				exc,
			)
			raise OAuthHandshakeError(
				f"Token request failed for '{self._provider_name}' "
				f"(grant_type={grant_type}, endpoint={self._provider_config.token_url}): {exc}"
			) from exc

		if response.status_code == 200:
			return response.json()

		# 400/401 during refresh typically means the refresh token is revoked.
		if grant_type == "refresh_token" and response.status_code in (400, 401):
			frappe.logger().error(
				"OAuth refresh token revoked for provider %s (status=%s, url=%s): %s",
				self._provider_name,
				response.status_code,
				self._provider_config.token_url,
				response.text,
			)
			raise TokenRevokedError(
				f"Refresh token revoked for '{self._provider_name}' "
				f"(endpoint={self._provider_config.token_url}, "
				f"HTTP {response.status_code}): {response.text}"
			)

		# Any other error during exchange / refresh.
		frappe.logger().error(
			"OAuth token request failed for provider %s (grant_type=%s, status=%s, url=%s): %s",
			self._provider_name,
			grant_type,
			response.status_code,
			self._provider_config.token_url,
			response.text,
		)
		raise OAuthHandshakeError(
			f"Token request failed for '{self._provider_name}' "
			f"(grant_type={grant_type}, endpoint={self._provider_config.token_url}, "
			f"HTTP {response.status_code}): {response.text}"
		)

	@staticmethod
	def _parse_token_response(data: dict[str, Any]) -> OAuthToken:
		"""Parse a token endpoint JSON response into an ``OAuthToken``.

		Args:
		    data: The parsed JSON response from the token endpoint.

		Returns:
		    An ``OAuthToken`` instance.
		"""
		expires_at: datetime | None = None
		expires_in = data.get("expires_in")
		if expires_in is not None:
			expires_at = datetime.now().replace(microsecond=0) + __import__("datetime").timedelta(
				seconds=int(expires_in)
			)

		return OAuthToken(
			access_token=str(data["access_token"]),
			refresh_token=str(data["refresh_token"]) if data.get("refresh_token") else None,
			token_type=str(data.get("token_type", "Bearer")),
			expires_at=expires_at,
			scope=str(data.get("scope", "")),
			provider_metadata={k: v for k, v in data.items() if k not in _OAUTH_STANDARD_KEYS},
		)

	# ------------------------------------------------------------------
	# Persistence (via Bank Connector Token DocType)
	# ------------------------------------------------------------------

	def _persist_tokens(self, bank_account: str, token: OAuthToken) -> None:
		"""Persist an ``OAuthToken`` to the ``Bank Connector Token`` DocType.

		Args:
		    bank_account: The bank account to associate with the token.
		    token: The ``OAuthToken`` to persist.
		"""
		existing_name = frappe.db.exists(
			"Bank Connector Token",
			{"provider_name": self._provider_name, "bank_account": bank_account},
		)

		if existing_name:
			doc = frappe.get_doc("Bank Connector Token", existing_name)
		else:
			doc = frappe.new_doc("Bank Connector Token")
			doc.provider_name = self._provider_name
			doc.bank_account = bank_account

		doc.access_token = token.access_token
		if token.refresh_token:
			doc.refresh_token = token.refresh_token
		doc.token_type = token.token_type
		if token.expires_at:
			doc.expires_at = token.expires_at.strftime("%Y-%m-%d %H:%M:%S")
		doc.scope = token.scope or ""
		doc.provider_metadata = token.provider_metadata

		try:
			doc.save(ignore_permissions=True)
			frappe.db.commit()  # Ensure the save is committed immediately
		except Exception as exc:
			frappe.log_error(
				message=f"Failed to save token for {bank_account}: {exc}",
				title="Token persistence error",
			)
			raise

		doc.access_token = token.access_token
		if token.refresh_token:
			doc.refresh_token = token.refresh_token
		doc.token_type = token.token_type
		if token.expires_at:
			doc.expires_at = token.expires_at.strftime("%Y-%m-%d %H:%M:%S")
		doc.scope = token.scope or ""
		doc.provider_metadata = token.provider_metadata
		doc.save(ignore_permissions=True)

	def _load_tokens(self, bank_account: str) -> dict[str, Any] | None:
		"""Load stored token data for a bank account.

		Args:
		    bank_account: The bank account identifier.

		Returns:
		    A dict with token fields, or ``None`` if no record exists.
		"""
		doc_name = self._doc_name(bank_account)

		if not frappe.db.exists("Bank Connector Token", doc_name):
			return None

		doc = frappe.get_doc("Bank Connector Token", doc_name)
		return {
			"access_token": doc.get_password("access_token"),
			"refresh_token": doc.get_password("refresh_token") if doc.refresh_token else None,
			"token_type": doc.token_type,
			"expires_at": str(doc.expires_at) if doc.expires_at else None,
			"scope": doc.scope,
		}

	def _clear_tokens(self, bank_account: str) -> None:
		"""Delete the ``Bank Connector Token`` record for a bank account.

		Args:
		    bank_account: The bank account whose token record should
		        be cleared.
		"""
		doc_name = self._doc_name(bank_account)
		if frappe.db.exists("Bank Connector Token", doc_name):
			frappe.get_doc("Bank Connector Token", doc_name).delete(ignore_permissions=True)

	def _doc_name(self, bank_account: str) -> str:
		"""Build the DocType name from provider name and bank account."""
		return f"{self._provider_name}-{bank_account}"


def _parse_dt(value: str) -> datetime:
	"""Parse a datetime string returned from the DocType."""
	# Accept both ISO-8601 and Frappe's "YYYY-MM-DD HH:MM:SS" format.
	value = value.strip().replace(" ", "T")
	try:
		return datetime.fromisoformat(value)
	except ValueError:
		# Fallback: try parsing with Frappe's utility for timestamps with
		# microseconds, timezone offsets, etc.
		return get_datetime(value)


# Standard OAuth2 token response keys that are not stored as provider_metadata.
_OAUTH_STANDARD_KEYS = frozenset(
	{
		"access_token",
		"refresh_token",
		"token_type",
		"expires_in",
		"scope",
	}
)

__all__ = [
	"OAuth2Service",
	"OAuthProviderConfig",
	"OAuthToken",
]
