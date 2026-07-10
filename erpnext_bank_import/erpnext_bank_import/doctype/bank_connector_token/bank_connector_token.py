"""Controller for the Bank Connector Token DocType.

This DocType stores OAuth2 access and refresh tokens securely using
Frappe's encrypted ``Password`` fields.  Each record is uniquely
identified by the combination of ``provider_name`` and ``bank_account``.

Tokens should never be accessed directly via ``frappe.db.get_value()``
since Password fields are transparently encrypted/decrypted only through
the ``get_password()`` method.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BankConnectorToken(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_token: DF.Password | None
		bank_account: DF.Data
		expires_at: DF.Datetime | None
		provider_metadata: DF.JSON | None
		provider_name: DF.Data
		refresh_token: DF.Password | None
		scope: DF.SmallText | None
		token_type: DF.Data | None
	# end: auto-generated types

	def validate(self) -> None:
		"""Validate that provider_name and bank_account are set."""
		if not self.provider_name:
			frappe.throw("Provider Name is required.")
		if not self.bank_account:
			frappe.throw("Bank Account is required.")

	def before_insert(self) -> None:
		"""Ensure uniqueness of (provider_name, bank_account) pairs."""
		if frappe.db.exists(
			"Bank Connector Token",
			{"provider_name": self.provider_name, "bank_account": self.bank_account},
		):
			frappe.throw(
				f"A token record already exists for provider '{self.provider_name}' "
				f"and bank account '{self.bank_account}'."
			)


__all__ = [
	"BankConnectorToken",
]
