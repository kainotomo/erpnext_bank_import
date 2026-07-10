"""Reusable OAuth test data, token builders, and service factories.

Provides deterministic ``OAuthToken`` dicts for various expiry states
and a factory function for constructing ``OAuth2Service`` instances
with minimal boilerplate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from erpnext_bank_import.connectors.config import ConnectorConfig
from erpnext_bank_import.services.oauth import OAuth2Service

# ---------------------------------------------------------------------------
# Token dict factories
# ---------------------------------------------------------------------------

#: A valid, non-expired OAuthToken (expires 1 hour from now).
VALID_TOKEN_DICT: dict[str, Any] = {
    "access_token": "valid-access-token-abc",
    "refresh_token": "valid-refresh-token-xyz",
    "token_type": "Bearer",
    "expires_at": datetime.now(UTC) + timedelta(hours=1),
    "scope": "transactions:read",
    "provider_metadata": {},
}

#: An expired OAuthToken (expired 1 hour ago).
EXPIRED_TOKEN_DICT: dict[str, Any] = {
    "access_token": "expired-access-token",
    "refresh_token": "stale-refresh-token",
    "token_type": "Bearer",
    "expires_at": datetime.now(UTC) - timedelta(hours=1),
    "scope": "transactions:read",
    "provider_metadata": {},
}

#: An OAuthToken expiring within the safety buffer (30 seconds from now).
EXPIRING_SOON_TOKEN_DICT: dict[str, Any] = {
    "access_token": "expiring-soon-access-token",
    "refresh_token": "fresh-refresh-token",
    "token_type": "Bearer",
    "expires_at": datetime.now(UTC) + timedelta(seconds=30),
    "scope": "transactions:read",
    "provider_metadata": {},
}

#: An expired token with no refresh token available.
TOKEN_WITHOUT_REFRESH_DICT: dict[str, Any] = {
    "access_token": "no-refresh-access-token",
    "refresh_token": None,
    "token_type": "Bearer",
    "expires_at": datetime.now(UTC) - timedelta(hours=1),
    "scope": "transactions:read",
    "provider_metadata": {},
}

#: A token expiring far in the future (well outside safety buffer).
FAR_FUTURE_TOKEN_DICT: dict[str, Any] = {
    "access_token": "far-future-access-token",
    "refresh_token": "far-future-refresh-token",
    "token_type": "Bearer",
    "expires_at": datetime.now(UTC) + timedelta(days=30),
    "scope": "transactions:read",
    "provider_metadata": {},
}


def make_oauth_service(**overrides: Any) -> OAuth2Service:
    """Build an ``OAuth2Service`` with minimal configuration.

    Defaults to a valid OAuth2 setup.  Override any ``ConnectorConfig``
    field via keyword arguments.

    Examples
    --------
    >>> svc = make_oauth_service()
    >>> svc = make_oauth_service(client_id="my-cid", client_secret="my-secret")
    >>> svc = make_oauth_service(authorize_url=None)  # triggers validation error
    """
    defaults: dict[str, Any] = {
        "provider_name": "test",
        "api_base_url": "https://api.example.com",
        "client_id": "test-cid",
        "client_secret": "test-secret",
        "authorize_url": "/auth/authorize",
        "token_url": "/auth/token",
    }
    defaults.update(overrides)
    return OAuth2Service(ConnectorConfig(**defaults))


# ---------------------------------------------------------------------------
# Token persistence mock helpers
# ---------------------------------------------------------------------------


class _MockFrappeDoc:
    """Minimal mock for a Frappe document used in token persistence."""

    def __init__(self, **kwargs: Any) -> None:
        self._data = dict(kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._saved = False
        self._deleted = False

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return None

    def save(self, **kwargs: Any) -> None:
        self._saved = True

    def delete(self, **kwargs: Any) -> None:
        self._deleted = True

    def get_password(self, fieldname: str, *args: Any, **kwargs: Any) -> str:
        return self._data.get(fieldname, "")


def mock_token_persistence(
    exists: bool = False, doc_kwargs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Context manager that patches ``frappe`` DB calls for token storage.

    Returns a dict with references to the patches so callers can stop
    them when done.

    Args:
        exists: Whether ``frappe.db.exists`` should return ``True``.
        doc_kwargs: Attributes for the mock document.

    Returns:
        A dict with ``{"patches": ..., "m_frappe": ..., "mock_doc": ...}``.
    """
    if doc_kwargs is None:
        doc_kwargs = {}

    frappe_patcher = patch("erpnext_bank_import.services.oauth.frappe", autospec=False)
    m_frappe = frappe_patcher.start()

    m_frappe.db.exists.return_value = exists
    mock_doc = _MockFrappeDoc(**doc_kwargs) if doc_kwargs else _MockFrappeDoc()
    m_frappe.get_doc.return_value = mock_doc
    m_frappe.new_doc.return_value = _MockFrappeDoc(**doc_kwargs) if doc_kwargs else _MockFrappeDoc()

    return {
        "frappe_patcher": frappe_patcher,
        "m_frappe": m_frappe,
        "mock_doc": mock_doc,
    }


def stop_token_persistence(mocks: dict[str, Any]) -> None:
    """Stop patches started by ``mock_token_persistence``."""
    mocks["frappe_patcher"].stop()


__all__ = [
    "EXPIRED_TOKEN_DICT",
    "EXPIRING_SOON_TOKEN_DICT",
    "FAR_FUTURE_TOKEN_DICT",
    "TOKEN_WITHOUT_REFRESH_DICT",
    "VALID_TOKEN_DICT",
    "make_oauth_service",
    "mock_token_persistence",
    "stop_token_persistence",
]
