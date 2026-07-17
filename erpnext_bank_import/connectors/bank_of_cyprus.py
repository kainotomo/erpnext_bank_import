"""Bank of Cyprus — OAuth2 + Subscription-based PSD2 Connector.

BoC uses a subscription-based OAuth2 model that extends the standard
OAuth2 authorization code flow with a subscription lifecycle:

  1. Get TPP access token (client_credentials).
  2. Create a subscription (POST /v1/subscriptions).
  3. Redirect user to BoC for consent & account selection.
  4. Exchange authorization code for user token.
  5. Activate the subscription (PATCH /v1/subscriptions/{id}).
  6. Now account / transaction APIs can be called.

Every account API call requires both a ``Bearer`` token and a
``subscriptionId`` header.

Unlike Revolut, tokens are scoped **per connector** (not per bank
account).  A single subscription covers all accounts the user granted
access to.
"""

from __future__ import annotations

from typing import Any

import frappe
import requests

from erpnext_bank_import.connectors.base import BankConnector
from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import (
    AuthenticationError,
    ConfigurationError,
    TokenExpiredError,
)
from erpnext_bank_import.schema.transaction import NormalizedTransaction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: BoC uses DD/MM/YYYY for all dates.
_DATE_API_FMT = "%d/%m/%Y"
_DATE_ISO_FMT = "%Y-%m-%d"

#: Sandbox API base URL.
SANDBOX_API_BASE_URL = "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2"

#: Production API base URL (B2B).
PRODUCTION_API_BASE_URL = "https://apis.bankofcyprus.com/df-boc-org-prd/prod/psd2"

#: Default page size for transaction fetches.
_DEFAULT_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class BankOfCyprusConnector(BankConnector):
    """Connector for the Bank of Cyprus PSD2 API.

    This connector implements the ``BankConnector`` ABC for BoC's
    subscription-based OAuth2 model.

    Args:
        config: Connector configuration.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

        # Import lazily to avoid circular imports at module level.
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService
        from erpnext_bank_import.services.oauth import OAuth2Service

        self._oauth = OAuth2Service(config)
        self._boc = BocSubscriptionService(config)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

        # Track the current connector name for token lookups.
        self._connector_name: str | None = None

        # Cached subscription ID from token metadata.
        self._subscription_id: str | None = None

        # Rate-limit tracking.
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Auth lifecycle
    # ------------------------------------------------------------------

    def authenticate(self) -> None:
        """No-op — authentication is handled via the OAuth callback flow.
        
        See ``start_oauth_flow()`` and ``oauth_callback()`` module-level
        functions.
        """

    def is_authenticated(self) -> bool:
        """Check whether a valid access token exists.

        Returns:
            True if a non-expired token is stored for this connector.
        """
        # Try to load a valid access token to determine auth status.
        self._subscription_id = None
        if self._connector_name is None:
            return False
        try:
            self._get_access_token()
            return True
        except TokenExpiredError:
            return False
        except Exception:
            return False

    def refresh_token(self) -> None:
        """Refresh the user access token.

        Note: BoC does not provide refresh tokens in its token response.
        If ``OAuth2Service`` raises ``TokenExpiredError`` (no refresh
        token available), we propagate it so the orchestrator knows
        re-authorization is needed.

        Raises:
            TokenExpiredError: If no refresh token is available or
                the refresh attempt fails.
        """
        if self._connector_name is None:
            return
        self._oauth.refresh_access_token(self._connector_name)
        # Re-load subscription ID from refreshed token metadata.
        self._load_subscription_id()

    # ------------------------------------------------------------------
    # Account discovery
    # ------------------------------------------------------------------

    def get_accounts(self) -> list[AccountInfo]:
        """Discover bank accounts via the BoC Accounts API.

        Returns only the accounts the user authorised during the
        subscription flow.

        Returns:
            A list of ``AccountInfo`` dataclass instances.

        Raises:
            AuthenticationError: If the access token is invalid.
        """
        token = self._get_access_token()
        if self._subscription_id is None:
            raise AuthenticationError(
                "No active subscription. Please authorize the connector first."
            )

        raw_accounts = self._boc.get_accounts(token, self._subscription_id)
        return [self._to_account_info(acc) for acc in raw_accounts]

    # ------------------------------------------------------------------
    # Transaction fetching
    # ------------------------------------------------------------------

    def fetch_transactions(
        self,
        account_id: str,
        date_from: str,
        date_to: str,
        *,
        page_size: int = _DEFAULT_PAGE_SIZE,
        page_token: str | None = None,
    ) -> tuple[list[NormalizedTransaction], str | None]:
        """Fetch a page of transactions for an account.

        BoC uses a ``maxCount`` parameter (no native cursor).  When
        the returned transaction count reaches ``page_size``, the last
        transaction's ``postingDate`` is used as the cursor for the
        next page (re-queried as the new ``date_to``).

        Args:
            account_id: BoC account ID.
            date_from: ISO start date (YYYY-MM-DD).
            date_to: ISO end date (YYYY-MM-DD).
            page_size: Max transactions per page.
            page_token: If provided, the posting date to use as the
                end of the query window (acts as cursor).

        Returns:
            A ``(transactions, next_page_token)`` tuple.  ``next_token``
            is ``None`` when there are no more pages.
        """
        token = self._get_access_token()
        if self._subscription_id is None:
            raise AuthenticationError(
                "No active subscription. Please authorize the connector first."
            )

        # Convert ISO dates to BoC DD/MM/YYYY format.
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

        if page_token:
            # page_token is a posting date in DD/MM/YYYY from the
            # previous page — use it as the new date_to.
            boc_date_to = page_token
            boc_date_from = BocSubscriptionService.date_to_api(date_from)
        else:
            boc_date_from = BocSubscriptionService.date_to_api(date_from)
            boc_date_to = BocSubscriptionService.date_to_api(date_to)

        statement = self._boc.get_account_statement(
            token,
            self._subscription_id,
            account_id,
            boc_date_from,
            boc_date_to,
            max_count=page_size,
        )

        raw_transactions = statement.get("transaction", [])
        normalized = []
        for raw in raw_transactions:
            try:
                normalized.append(self.normalize_transaction(raw))
            except Exception as exc:
                frappe.log_error(
                    message=f"BoC transaction normalization failed for {raw.get('id')}: {exc}",
                    title="BoC Normalization Error",
                )
                continue

        # Determine next page token: if we got a full page, use the
        # last transaction's postingDate as the cursor.
        next_token: str | None = None
        if len(raw_transactions) >= page_size and raw_transactions:
            next_token = raw_transactions[-1].get("postingDate")

        return normalized, next_token

    def normalize_transaction(self, raw: dict[str, Any]) -> NormalizedTransaction:
        """Convert a raw BoC transaction dict to ``NormalizedTransaction``.

        BoC uses ``dcInd`` to indicate direction:
        - ``"DEBIT"``  → amount is negative (money out).
        - ``"CREDIT"`` → amount is positive (money in).

        Args:
            raw: A BoC transaction dict from the statement API.

        Returns:
            A ``NormalizedTransaction`` dict.

        Raises:
            NormalizationError: If required fields are missing.
        """
        from erpnext_bank_import.services.boc_subscription import BocSubscriptionService
        from erpnext_bank_import.connectors.exceptions import NormalizationError

        # Validate required fields.
        txn_id = raw.get("id")
        amount_container = raw.get("transactionAmount", {})
        amount = amount_container.get("amount")
        currency = amount_container.get("currency")
        posting_date = raw.get("postingDate")

        if not txn_id or amount is None or not currency or not posting_date:
            missing = []
            if not txn_id:
                missing.append("id")
            if amount is None:
                missing.append("transactionAmount.amount")
            if not currency:
                missing.append("transactionAmount.currency")
            if not posting_date:
                missing.append("postingDate")
            raise NormalizationError(
                f"BoC transaction missing required fields: {', '.join(missing)}"
            )

        # Determine sign from dcInd.
        dc_ind = raw.get("dcInd", "DEBIT")
        signed_amount = float(amount)
        if dc_ind == "DEBIT":
            signed_amount = -signed_amount

        # Convert date from DD/MM/YYYY to ISO.
        iso_date = BocSubscriptionService.date_from_api(posting_date)

        # Build NormalizedTransaction.
        return NormalizedTransaction(
            external_id=str(txn_id),
            date=iso_date,
            amount=signed_amount,
            currency=str(currency),
            description=str(raw.get("description", "")),
            reference_number=str(raw.get("id")),  # Fallback: use transaction ID
            transaction_type=dc_ind,
            provider_metadata=raw,
            # Counterparty fields — may be populated in production but
            # not in sandbox test data.
            bank_party_name=None,
            bank_party_account_number=None,
            bank_party_iban=None,
            included_fee=None,
            excluded_fee=None,
        )

    # ------------------------------------------------------------------
    # Bank account context management
    # ------------------------------------------------------------------

    def set_current_bank_account(self, bank_account: str) -> None:
        """Set the current bank account context (connector name).

        Unlike Revolut (which has per-account tokens), BoC uses a
        single subscription per connector.  The ``bank_account``
        parameter is used as the connector name for token lookups
        in ``Bank Connector Token``.

        Args:
            bank_account: The connector name (used as token key).
        """
        self._connector_name = bank_account
        self._load_subscription_id()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Get a valid access token for this connector.

        Returns:
            A valid access token string.

        Raises:
            TokenExpiredError: If no valid token is stored.
        """
        if self._connector_name is None:
            raise TokenExpiredError(
                "Bank account context not set. Call set_current_bank_account() first."
            )
        return self._oauth.get_valid_access_token(self._connector_name)

    def _load_subscription_id(self) -> None:
        """Load the subscription ID from the stored token metadata."""
        if self._connector_name is None:
            self._subscription_id = None
            return

        try:
            from erpnext_bank_import.services.oauth import OAuth2Service

            token_data = self._oauth._load_tokens(self._connector_name)
            if token_data:
                metadata = token_data.get("provider_metadata") or {}
                if isinstance(metadata, str):
                    import json

                    metadata = json.loads(metadata)
                self._subscription_id = metadata.get("subscription_id")
            else:
                self._subscription_id = None
        except Exception:
            self._subscription_id = None

    def _validate_config(self) -> None:
        """Validate BoC-specific configuration.

        Extends the base validation to require ``client_id`` and
        ``client_secret`` for OAuth2 auth method.
        """
        super()._validate_config()

        if self.config.auth_method == "oauth2":
            if not self.config.client_id:
                raise ConfigurationError(
                    "client_id is required for Bank of Cyprus OAuth2 authentication."
                )
            if not self.config.client_secret:
                raise ConfigurationError(
                    "client_secret is required for Bank of Cyprus OAuth2 authentication."
                )

    def _to_account_info(self, raw: dict[str, Any]) -> AccountInfo:
        """Convert a raw BoC account dict to ``AccountInfo``.

        Args:
            raw: A BoC account dict from the accounts API.

        Returns:
            An ``AccountInfo`` dataclass instance.
        """
        return AccountInfo(
            account_id=str(raw.get("accountId", "")),
            account_name=str(raw.get("accountName", raw.get("accountAlias", ""))),
            currency=str(raw.get("currency", "EUR")),
            iban=str(raw.get("IBAN", "")),
            account_number=str(raw.get("accountId", "")),
            provider_metadata=raw,
        )


# ---------------------------------------------------------------------------
# Frappe whitelisted endpoints for OAuth flow
# ---------------------------------------------------------------------------


@frappe.whitelist()
def start_oauth_flow(connector_name: str) -> str:
    """Initiate the BoC OAuth2 + subscription flow.

    1. Loads the connector config.
    2. Gets a TPP (client_credentials) access token.
    3. Creates a subscription (PENDING status).
    4. Temporarily stores the subscription ID so the callback can
       retrieve it.
    5. Builds and returns the BoC authorisation URL for user consent.

    Args:
        connector_name: The ``Bank Connector`` record name.

    Returns:
        The BoC authorisation URL to which the user's browser
        should be redirected.
    """
    doc = frappe.get_doc("Bank Connector", connector_name)
    if not doc.enabled:
        frappe.throw(
            frappe._("Bank Connector '{0}' is disabled. Enable it first.").format(connector_name)
        )

    config = doc.get_connector_config()

    from erpnext_bank_import.services.boc_subscription import BocSubscriptionService

    boc = BocSubscriptionService(config)

    # Step 1: Get TPP access token.
    tpp_token = boc.get_tpp_access_token()

    # Step 2: Create subscription.
    sub_data = boc.create_subscription(tpp_token)
    subscription_id = sub_data["subscriptionId"]

    # Step 3: Store subscription ID in cache so the callback can find it.
    # Expires in 10 minutes — plenty of time for user to complete consent.
    frappe.cache().set_value(
        f"boc_pending:{connector_name}",
        subscription_id,
        expires_in_sec=600,
    )

    # Step 4: Build authorisation URL (no state — BoC doesn't support it).
    auth_url = boc.build_authorize_url(
        subscription_id=subscription_id,
    )

    return auth_url


@frappe.whitelist(allow_guest=True)
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> dict:
    """Handle the OAuth2 redirect callback from Bank of Cyprus.

    Completes the subscription flow:
    1. Retrieves the pending subscription ID from cache.
    2. Exchanges the authorisation code for a user access token.
    3. Gets the subscription details (selected accounts).
    4. Activates the subscription.
    5. Stores the subscription ID in token metadata.

    Args:
        code: The authorisation code from BoC.
        state: Ignored (BoC does not support OAuth state).
        error: Error message from BoC if the user denied consent.

    Returns:
        A dict with a ``message`` key on success.
    """
    if error:
        frappe.throw(
            frappe._("Bank of Cyprus authorisation was declined or failed: {0}").format(error)
        )

    if not code:
        frappe.throw(frappe._("Missing authorisation code parameter."))

    # Since BoC does not support OAuth state, we iterate all enabled
    # BoC connectors to find the one with a matching pending subscription.
    from erpnext_bank_import.connectors import get_all_enabled_connectors

    last_error: Exception | None = None
    for name in get_all_enabled_connectors():
        try:
            doc = frappe.get_doc("Bank Connector", name)
            config = doc.get_connector_config()
            if config.provider_name != "bank_of_cyprus":
                continue

            # Try this connector.
            _complete_subscription_flow(name, config, code)
            return {
                "message": frappe._(
                    "Bank of Cyprus authorisation successful for connector '{0}'. "
                    "You can close this window."
                ).format(name),
            }
        except Exception as exc:
            last_error = exc
            frappe.log_error(
                message=f"BoC OAuth callback failed for connector '{name}': {exc}",
                title="BoC OAuth callback error",
            )
            continue

    if last_error:
        raise last_error  # type: ignore[misc]

    frappe.throw(
        frappe._(
            "Could not find a pending Bank of Cyprus authorisation. "
            "Please start the authorisation flow again."
        )
    )


def _complete_subscription_flow(
    connector_name: str,
    config: ConnectorConfig,
    code: str,
) -> None:
    """Complete the BoC subscription authorisation flow.

    Called by ``oauth_callback()`` for each enabled BoC connector.
    """
    from erpnext_bank_import.services.boc_subscription import BocSubscriptionService
    from erpnext_bank_import.services.oauth import OAuth2Service

    boc = BocSubscriptionService(config)

    # Retrieve the pending subscription ID from cache.
    subscription_id = frappe.cache().get_value(f"boc_pending:{connector_name}")
    if not subscription_id:
        raise ValueError(
            f"No pending subscription found for connector '{connector_name}'. "
            "Start the authorisation flow again."
        )

    # Exchange authorisation code for user token.
    # BoC requires scope=UserOAuth2Security for code exchange.
    exchange_config = ConnectorConfig(
        provider_name=config.provider_name,
        api_base_url=config.api_base_url,
        auth_method=config.auth_method,
        client_id=config.client_id,
        client_secret=config.client_secret,
        authorize_url=config.authorize_url,
        token_url=config.token_url,
        scopes=["UserOAuth2Security"],
        redirect_uri=config.redirect_uri,
        extra=config.extra,
    )
    oauth = OAuth2Service(exchange_config)
    oauth.exchange_code_for_tokens(code=code, bank_account=connector_name)

    # Get a fresh user token.
    user_token = oauth.get_valid_access_token(connector_name)

    # Get subscription details (selected accounts).
    sub_details = boc.get_subscription_details(user_token, subscription_id)

    # Extract selected accounts.
    selected_accounts = sub_details.get("selectedAccounts", [])

    # Activate the subscription.
    boc.activate_subscription(user_token, subscription_id, selected_accounts)

    # Store subscription_id in token metadata.
    _store_subscription_id_in_metadata(connector_name, subscription_id)

    # Clean up cached pending subscription.
    frappe.cache().delete_value(f"boc_pending:{connector_name}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_boc_connector() -> str | None:
    """Find the first enabled Bank of Cyprus connector.

    Fallback for callbacks that don't receive a state parameter.

    Returns:
        The connector name, or ``None`` if no enabled BoC connector
        is found.
    """
    from erpnext_bank_import.connectors import get_all_enabled_connectors

    for name in get_all_enabled_connectors():
        try:
            doc = frappe.get_doc("Bank Connector", name)
            config = doc.get_connector_config()
            if config.provider_name == "bank_of_cyprus":
                return name
        except Exception:
            continue
    return None


def _store_subscription_id_in_metadata(
    connector_name: str,
    subscription_id: str,
) -> None:
    """Store the subscription ID in the token record's provider_metadata.

    Args:
        connector_name: The connector name (used as token key).
        subscription_id: The subscription ID to store.
    """
    from datetime import UTC, datetime

    try:
        token_name = frappe.db.get_value(
            "Bank Connector Token",
            {"provider_name": "bank_of_cyprus", "bank_account": connector_name},
            "name",
        )
        if token_name:
            doc = frappe.get_doc("Bank Connector Token", token_name)
            existing_metadata = doc.provider_metadata or {}
            if isinstance(existing_metadata, str):
                import json

                existing_metadata = json.loads(existing_metadata)
            existing_metadata["subscription_id"] = subscription_id
            existing_metadata["subscription_created_at"] = datetime.now(UTC).isoformat()
            doc.db_set("provider_metadata", existing_metadata)
            frappe.db.commit()
    except Exception as exc:
        frappe.log_error(
            message=f"Failed to store subscription ID in token metadata: {exc}",
            title="BoC Subscription ID Storage",
        )
