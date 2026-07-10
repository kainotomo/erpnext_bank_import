"""Controller for the Bank Connector Account Mapping child table.

Each row maps a bank-API account (identified by ``provider_account_id``)
to an ERPNext ``Bank Account`` record.  ``company`` is automatically
fetched from the linked Bank Account.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BankConnectorAccountMapping(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		bank_account: DF.Link
		company: DF.Link | None
		currency: DF.Link | None
		is_enabled: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		provider_account_id: DF.Data
		provider_account_name: DF.Data | None
	# end: auto-generated types

	def validate(self) -> None:
		"""Validate mapping fields and auto-set company/currency from the linked Bank Account."""
		if not self.provider_account_id:
			frappe.throw(frappe._("Provider Account ID is required."))

		# Auto-set company and currency from the linked Bank Account.
		if self.bank_account:
			bank_ac = frappe.db.get_value(
				"Bank Account", self.bank_account, ["company", "account"], as_dict=True, cache=True
			)
			if bank_ac:
				if not self.company:
					self.company = bank_ac.company
				if not self.currency and bank_ac.account:
					self.currency = frappe.db.get_value(
						"Account", bank_ac.account, "account_currency", cache=True
					)


__all__ = [
	"BankConnectorAccountMapping",
]
