"""Data models for bank connector configuration and account metadata.

These are plain Python dataclasses, not Frappe DocTypes.  They define
the typed contract between the orchestration layer and connector implementations.

Frappe-specific persistence (e.g. a ``Bank Connector Settings`` Single DocType)
will be built in a later issue and mapped to these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectorConfig:
    """Configuration parameters for a bank connector instance.

    Subclasses may extend this with provider-specific fields.
    """

    provider_name: str
    """Unique slug identifying the provider (e.g. ``"revolut"``, ``"mock"``)."""

    api_base_url: str
    """Base URL for the bank's REST API (e.g. ``"https://api.revolut.com"``)."""

    auth_method: str = "oauth2"
    """Authentication mechanism: ``"oauth2"``, ``"api_key"``, or ``"basic"``."""

    client_id: str | None = None
    """OAuth2 client identifier (``None`` for API-key-based auth)."""

    token_url: str | None = None
    """OAuth2 token endpoint relative to ``api_base_url``, or absolute URL."""

    rate_limit_rps: float | None = None
    """Maximum requests per second allowed by the bank API, if known."""

    timeout_seconds: float = 30.0
    """HTTP request timeout in seconds."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Provider-specific configuration key-value pairs.

    Real connectors use this for things like API version, sandbox mode,
    or custom endpoint paths.
    """


@dataclass
class AccountInfo:
    """A bank account discovered via ``BankConnector.get_accounts()``."""

    account_id: str
    """Provider-specific account identifier."""

    account_name: str
    """Human-readable account name / label."""

    currency: str
    """ISO-4217 currency code (e.g. ``"EUR"``, ``"USD"``)."""

    iban: str | None = None
    """International Bank Account Number, if available."""

    account_number: str | None = None
    """Local account number, if available."""

    provider_metadata: dict[str, Any] = field(default_factory=dict)
    """Opaque bag for provider-specific account metadata.

    This data is **not** used by the app but is preserved for debugging
    or custom reports.
    """


__all__ = [
    "ConnectorConfig",
    "AccountInfo",
]
