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
		"""Validate mapping fields and auto-set company/currency from the linked Bank Account.

		Also populates ``integration_id`` on the linked ``Bank Account`` doctype
		with the ``provider_account_id`` so that ERPNext's native banking views
		can see this bank account is linked to an external provider.
		"""
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

		# Populate integration_id on the linked Bank Account so ERPNext
		# native banking views (e.g. Plaid, Banking module) know this
		# account is connected to an external provider.
		if self.is_enabled and self.provider_account_id:
			current_integration_id = frappe.db.get_value(
				"Bank Account", self.bank_account, "integration_id", cache=True
			)
			if current_integration_id != self.provider_account_id:
				frappe.db.set_value(
					"Bank Account",
					self.bank_account,
					"integration_id",
					self.provider_account_id,
				)


__all__ = [
	"BankConnectorAccountMapping",
]
