"""Bank-agnostic connector interface.

Every bank connector must subclass ``BankConnector`` and implement all
abstract methods.  The contract covers four lifecycle phases:

#. **Authentication** — ``authenticate()``, ``refresh_token()``, ``is_authenticated()``
#. **Account discovery** — ``get_accounts()``
#. **Transaction fetch** — ``fetch_transactions()`` (paginated)
#. **Normalisation** — ``normalize_transaction()``

A concrete convenience method ``fetch_all_transactions()`` handles
automatic pagination and is provided by the base class.

Usage
-----
.. code-block:: python

    from erpnext_bank_import.connectors import get_connector

    conn = get_connector("mock")
    conn.authenticate()
    accounts = conn.get_accounts()
    txns = conn.fetch_all_transactions(
        account_id=accounts[0].account_id,
        date_from="2026-01-01",
        date_to="2026-06-30",
    )
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from erpnext_bank_import.connectors.config import AccountInfo, ConnectorConfig
from erpnext_bank_import.connectors.exceptions import ConfigurationError
from erpnext_bank_import.schema.transaction import NormalizedTransaction


class BankConnector(ABC):
	"""Abstract base class for all bank API connectors.

	Subclasses must provide a concrete implementation of every abstract
	method.  The base class provides a constructor that stores
	``config`` and validates it.

	For OAuth2-based providers, the recommended way to implement the
	auth lifecycle methods is to delegate to
	``erpnext_bank_import.services.OAuth2Service``:

	.. code-block:: python

	    from erpnext_bank_import.services import OAuth2Service


	    class RevolutConnector(BankConnector):
	        def __init__(self, *args, **kwargs):
	            super().__init__(*args, **kwargs)
	            self._oauth = OAuth2Service(self.config)

	        def authenticate(self):
	            ...  # redirect to authorize URL via self._oauth.get_authorize_url()
	            # then handle callback via self._oauth.exchange_code_for_tokens()

	        def refresh_token(self):
	            self._oauth.refresh_access_token(bank_account=...)

	        def is_authenticated(self):
	            try:
	                self._oauth.get_valid_access_token(bank_account=...)
	                return True
	            except ...:
	                return False
	"""

	def __init__(self, config: ConnectorConfig | None = None, **kwargs: Any) -> None:
		"""Initialise the connector.

		Args:
		    config: Connector configuration.  If ``None``, a default
		        configuration is built from ``**kwargs`` (useful for
		        testing and the mock provider).
		    **kwargs: When *config* is ``None``, these are passed as
		        keyword arguments to the ``ConnectorConfig`` constructor.
		"""
		if config is not None:
			self.config = config
		else:
			self.config = ConnectorConfig(**kwargs)

		self._validate_config()

	# ------------------------------------------------------------------
	# Auth lifecycle
	# ------------------------------------------------------------------

	@abstractmethod
	def authenticate(self) -> None:
		"""Perform the authentication handshake with the bank API.

		This method should obtain and store any tokens, certificates,
		or session state needed for subsequent API calls.

		For OAuth2 providers, delegate to
		``OAuth2Service.exchange_code_for_tokens()`` after the user
		has been redirected through the authorization URL.

		Raises:
		    AuthenticationError: If the credentials are invalid or the
		        handshake is rejected by the bank.
		"""

	@abstractmethod
	def is_authenticated(self) -> bool:
		"""Return ``True`` if the connector has a valid authenticated session.

		This method should check token expiry (if applicable) without
		making an HTTP call, returning ``False`` if re-authentication
		is needed.

		For OAuth2 providers, delegate to
		``OAuth2Service.get_valid_access_token()`` and return
		``True`` if a valid token exists.
		"""

	@abstractmethod
	def refresh_token(self) -> None:
		"""Refresh the authentication token (e.g. OAuth2 refresh flow).

		For OAuth2 providers, delegate to
		``OAuth2Service.refresh_access_token()``.

		Raises:
		    AuthenticationError: If the refresh fails (e.g. refresh
		        token expired or revoked).
		"""

	# ------------------------------------------------------------------
	# Account discovery
	# ------------------------------------------------------------------

	@abstractmethod
	def get_accounts(self) -> list[AccountInfo]:
		"""Retrieve all bank accounts accessible to the authenticated session.

		Returns:
		    A list of ``AccountInfo`` dataclass instances.  May be
		    empty (e.g. no accounts linked to the credentials).

		Raises:
		    AuthenticationError: If the session is not authenticated.
		    ApiError: If the bank API returns an error.
		"""

	# ------------------------------------------------------------------
	# Transaction fetch
	# ------------------------------------------------------------------

	@abstractmethod
	def fetch_transactions(
		self,
		account_id: str,
		date_from: str,
		date_to: str,
		*,
		page_size: int = 100,
		page_token: str | None = None,
	) -> tuple[list[NormalizedTransaction], str | None]:
		"""Fetch a single page of transactions for the given account.

		This method returns both the list of transactions and a
		``next_page_token`` for pagination.  When there are no more
		pages, ``next_page_token`` is ``None``.

		Args:
		    account_id: The provider-specific account identifier
		        (from ``get_accounts()``).
		    date_from: Start date in ``YYYY-MM-DD`` format (inclusive).
		    date_to: End date in ``YYYY-MM-DD`` format (inclusive).
		    page_size: Maximum number of transactions per page.
		    page_token: Opaque cursor for the next page.  ``None``
		        fetches the first page.

		Returns:
		    A tuple ``(transactions, next_page_token)`` where
		    *transactions* is a list of ``NormalizedTransaction``
		    dicts and *next_page_token* is ``None`` when all pages
		    have been consumed.

		Raises:
		    AuthenticationError: If the session is not authenticated.
		    RateLimitError: If the bank API rate limit is exceeded.
		    ApiError: If the bank API returns an error.
		"""

	def fetch_all_transactions(
		self,
		account_id: str,
		date_from: str,
		date_to: str,
		*,
		page_size: int = 100,
	) -> list[NormalizedTransaction]:
		"""Fetch **all** transactions for the given account, handling pagination.

		This is a convenience method that calls ``fetch_transactions()``
		repeatedly until all pages are consumed.

		Args:
		    account_id: Provider-specific account identifier.
		    date_from: Start date in ``YYYY-MM-DD`` format (inclusive).
		    date_to: End date in ``YYYY-MM-DD`` format (inclusive).
		    page_size: Maximum transactions per page.

		Returns:
		    A flat list of all ``NormalizedTransaction`` dicts across
		    all pages.
		"""
		all_txns: list[NormalizedTransaction] = []
		page_token: str | None = None

		while True:
			page, page_token = self.fetch_transactions(
				account_id=account_id,
				date_from=date_from,
				date_to=date_to,
				page_size=page_size,
				page_token=page_token,
			)
			all_txns.extend(page)
			if page_token is None:
				break

		return all_txns

	# ------------------------------------------------------------------
	# Normalisation
	# ------------------------------------------------------------------

	@abstractmethod
	def normalize_transaction(self, raw: dict[str, Any]) -> NormalizedTransaction:
		"""Convert a raw bank-API transaction dict into the canonical form.

		Subclasses must validate that all required fields are present
		and raise ``NormalizationError`` if they are not.

		Args:
		    raw: The raw transaction dict as returned by the bank API.

		Returns:
		    A ``NormalizedTransaction`` dict.

		Raises:
		    NormalizationError: If the raw data is missing required
		        fields or contains values with incompatible types.
		"""

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _validate_config(self) -> None:
		"""Validate the connector configuration.

		Subclasses may override to add provider-specific validation.

		Raises:
		    ConfigurationError: If ``provider_name`` or ``api_base_url``
		        are empty.
		"""
		if not self.config.provider_name:
			raise ConfigurationError("provider_name must not be empty")
		if not self.config.api_base_url:
			raise ConfigurationError("api_base_url must not be empty")


__all__ = [
	"BankConnector",
]
