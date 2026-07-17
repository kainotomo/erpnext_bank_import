"""Bank of Cyprus subscription lifecycle service.

BoC uses a subscription-based OAuth2 model that is more complex than
standard OAuth2.  Before any account data can be accessed, a
subscription must be created, authorised by the user, and activated.

Flow:
  1. Get TPP access token (``client_credentials`` grant).
  2. Create a subscription (``POST /v1/subscriptions``).
  3. Redirect user to BoC for consent & account selection.
  4. Exchange authorisation code for a user access token.
  5. Get subscription details (selected accounts).
  6. Activate the subscription (``PATCH /v1/subscriptions/{id}``).
  7. Now account / transaction APIs can be called.

Every account API call requires both a ``Bearer`` token and a
``subscriptionId`` header.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from urllib.parse import urlencode, urljoin

import frappe
import requests

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
    ApiError,
    AuthenticationError,
    ConfigurationError,
    ServerError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Date format used by BoC API (DD/MM/YYYY).
DATE_API_FMT = "%d/%m/%Y"

#: Date format used internally (ISO 8601).
DATE_ISO_FMT = "%Y-%m-%d"

#: Default max count for account statement requests.
DEFAULT_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Subscription service
# ---------------------------------------------------------------------------


class BocSubscriptionService:
    """Manages the BoC subscription lifecycle and API calls.

    Args:
        config: The connector configuration containing API base URL,
            client credentials, and OAuth endpoints.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config

        # Resolve base URLs.
        base = config.api_base_url.rstrip("/")

        # OAuth token endpoint.
        token_url_rel = config.token_url or "/oauth2/token"
        if not token_url_rel.startswith("http"):
            self._token_url = urljoin(base + "/", token_url_rel.lstrip("/"))
        else:
            self._token_url = token_url_rel

        # OAuth authorize endpoint.
        auth_url_rel = config.authorize_url or "/oauth2/authorize"
        if not auth_url_rel.startswith("http"):
            self._authorize_url = urljoin(base + "/", auth_url_rel.lstrip("/"))
        else:
            self._authorize_url = auth_url_rel

        # Subscription endpoints.
        self._subscription_url = f"{base}/v1/subscriptions"
        self._accounts_url = f"{base}/v1/accounts"

        self._timeout = config.timeout_seconds or 30
        self._client_id = config.client_id or ""
        self._client_secret = config.client_secret or ""
        self._redirect_uri = config.redirect_uri or ""

    # ------------------------------------------------------------------
    # Public API — subscription lifecycle
    # ------------------------------------------------------------------

    def get_tpp_access_token(self) -> str:
        """Obtain a TPP (client_credentials) access token.

        This token is used exclusively for subscription management
        (create, get details, activate).

        Returns:
            The access token string.

        Raises:
            AuthenticationError: If the credentials are rejected.
            ApiError: For any other error response.
        """
        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": "TPPOAuth2Security",
        }
        response_data = self._token_request(data)
        return str(response_data["access_token"])

    def create_subscription(self, tpp_token: str) -> dict[str, Any]:
        """Create a new subscription.

        The subscription starts in ``PENDING`` status and must be
        authorised by the user before activation.

        Args:
            tpp_token: A valid TPP access token.

        Returns:
            The subscription creation response dict containing
            ``subscriptionId`` and ``status``.
        """
        headers = self._build_headers(tpp_token)
        payload: dict[str, Any] = {
            "accounts": {
                "transactionHistory": True,
                "balance": True,
                "details": True,
                "checkFundsAvailability": True,
            },
            "payments": {
                "limit": 99999999,
                "currency": "EUR",
                "amount": 999999999,
            },
        }

        try:
            response = requests.post(
                self._subscription_url,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(
                f"Subscription creation request failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 201:
            return response.json()

        self._handle_error(response, "create subscription")
        return {}  # unreachable

    def build_authorize_url(
        self,
        subscription_id: str,
        state: str | None = None,
    ) -> str:
        """Build the OAuth2 authorisation URL for user consent.

        The user's browser is redirected to this URL to log into
        1Bank, select accounts, and grant consent.

        Note: BoC does NOT support the standard OAuth2 ``state``
        parameter — passing it results in a ``badstate`` error.
        The connector name is instead passed via a Frappe cache
        mapping (see ``start_oauth_flow``).

        Args:
            subscription_id: The subscription ID to authorise.
            state: Optional CSRF state value (ignored by BoC).

        Returns:
            The full authorisation URL.
        """
        params: dict[str, str] = {
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": "UserOAuth2Security",
            "client_id": self._client_id,
            "subscriptionid": subscription_id,
        }
        return f"{self._authorize_url}?{urlencode(params)}"

    def get_subscription_details(
        self,
        user_token: str,
        subscription_id: str,
    ) -> dict[str, Any]:
        """Get subscription details including selected accounts.

        Args:
            user_token: A valid user access token.
            subscription_id: The subscription ID.

        Returns:
            The subscription details dict.
        """
        url = f"{self._subscription_url}/{subscription_id}"
        headers = self._build_headers(user_token)

        try:
            response = requests.get(url, headers=headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ApiError(
                f"Get subscription details failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 200:
            return response.json()

        self._handle_error(response, "get subscription details")
        return {}  # unreachable

    def activate_subscription(
        self,
        user_token: str,
        subscription_id: str,
        selected_accounts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Activate a subscription after user consent.

        Args:
            user_token: A valid user access token.
            subscription_id: The subscription ID to activate.
            selected_accounts: The list of ``{"accountId": "..."}``
                dicts returned by ``get_subscription_details()``.

        Returns:
            The activated subscription dict with ``status: "ACTV"``.
        """
        url = f"{self._subscription_url}/{subscription_id}"
        # Note: BoC docs show "INPROGRESS" as the PATCH status value
        # which then responds with "ACTV".
        payload: dict[str, Any] = {
            "subscriptionId": subscription_id,
            "status": "INPROGRESS",
            "description": "SUBSCRIPTION",
            "selectedAccounts": selected_accounts,
        }
        headers = self._build_headers(user_token)

        try:
            response = requests.patch(url, json=payload, headers=headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ApiError(
                f"Activate subscription failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 200:
            return response.json()

        self._handle_error(response, "activate subscription")
        return {}  # unreachable

    # ------------------------------------------------------------------
    # Public API — account data
    # ------------------------------------------------------------------

    def get_accounts(
        self,
        user_token: str,
        subscription_id: str,
    ) -> list[dict[str, Any]]:
        """Retrieve all bank accounts accessible under the subscription.

        Args:
            user_token: A valid user access token.
            subscription_id: The active subscription ID.

        Returns:
            A list of account dicts.
        """
        headers = self._build_headers(user_token, subscription_id)

        try:
            response = requests.get(
                self._accounts_url,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(
                f"Get accounts request failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 200:
            return response.json()

        self._handle_error(response, "get accounts")
        return []  # unreachable

    def get_account_statement(
        self,
        user_token: str,
        subscription_id: str,
        account_id: str,
        date_from: str,
        date_to: str,
        max_count: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Fetch a statement (transaction history) for an account.

        Args:
            user_token: A valid user access token.
            subscription_id: The active subscription ID.
            account_id: The BoC account ID.
            date_from: Start date in DD/MM/YYYY format.
            date_to: End date in DD/MM/YYYY format.
            max_count: Maximum number of transactions to return.

        Returns:
            The statement response dict with ``account`` and
            ``transaction`` keys.
        """
        url = f"{self._accounts_url}/{account_id}/statement"
        headers = self._build_headers(user_token, subscription_id)
        params: dict[str, str | int] = {
            "startDate": date_from,
            "endDate": date_to,
            "maxCount": max_count,
        }

        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(
                f"Get account statement failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 200:
            return response.json()

        self._handle_error(response, "get account statement")
        return {}  # unreachable

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_headers(
        self,
        access_token: str,
        subscription_id: str | None = None,
    ) -> dict[str, str]:
        """Build standard BoC API request headers.

        Args:
            access_token: Bearer token.
            subscription_id: Optional subscription ID (required for
                account APIs).

        Returns:
            Header dict.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "timeStamp": str(int(time.time() * 1000)),
            "journeyId": str(uuid.uuid4()),
        }
        if subscription_id:
            headers["subscriptionId"] = subscription_id
        return headers

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        """POST to the OAuth2 token endpoint.

        Args:
            data: Form-encoded body parameters.

        Returns:
            The parsed JSON response.

        Raises:
            AuthenticationError: On 401 (bad credentials).
            ApiError: On other non-2xx responses.
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            response = requests.post(
                self._token_url,
                data=data,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(
                f"Token request failed: {exc}",
                status_code=0,
                response_body=str(exc),
            ) from exc

        if response.status_code == 200:
            return response.json()

        self._handle_error(response, "token request")
        return {}  # unreachable

    def _handle_error(self, response: requests.Response, context: str) -> None:
        """Map HTTP error responses to appropriate exceptions.

        Args:
            response: The failed HTTP response.
            context: A human-readable description of the operation.

        Raises:
            AuthenticationError: For 401 responses.
            ApiError: For 4xx responses.
            ServerError: For 5xx responses.
        """
        status = response.status_code
        body = response.text

        try:
            body_json = response.json()
            error_msg = body_json.get("error_description", body_json.get("error", body))
        except (ValueError, AttributeError):
            error_msg = body

        if status == 401:
            raise AuthenticationError(
                f"BoC API authentication failed during {context}: {error_msg}"
            )
        if 400 <= status < 500:
            raise ApiError(
                f"BoC API error during {context}: {error_msg}",
                status_code=status,
                response_body=body,
            )
        if status >= 500:
            raise ServerError(
                f"BoC server error during {context}: {error_msg}",
                status_code=status,
                response_body=body,
            )

        raise ApiError(
            f"BoC API unexpected response ({status}) during {context}: {error_msg}",
            status_code=status,
            response_body=body,
        )

    # ------------------------------------------------------------------
    # Static helpers — date conversion
    # ------------------------------------------------------------------

    @staticmethod
    def date_to_api(iso_date: str) -> str:
        """Convert ISO date (YYYY-MM-DD) to BoC format (DD/MM/YYYY).

        Args:
            iso_date: Date string in ISO-8601 format.

        Returns:
            Date string in DD/MM/YYYY format.
        """
        from datetime import datetime

        dt = datetime.strptime(iso_date, DATE_ISO_FMT)
        return dt.strftime(DATE_API_FMT)

    @staticmethod
    def date_from_api(dmy_date: str) -> str:
        """Convert BoC date (DD/MM/YYYY) to ISO format (YYYY-MM-DD).

        Args:
            dmy_date: Date string in DD/MM/YYYY format.

        Returns:
            Date string in ISO-8601 format.
        """
        from datetime import datetime

        dt = datetime.strptime(dmy_date, DATE_API_FMT)
        return dt.strftime(DATE_ISO_FMT)
