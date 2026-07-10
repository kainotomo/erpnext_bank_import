"""Controller for the Bank Import Run Log DocType.

Each record represents a single import run (manual or scheduled).  This
DocType is append-only — records are created by ``RunLogger`` in
``erpnext_bank_import.services.run_log`` and should not be manually
created, edited, or deleted.

Fields:
    - ``connector_name``: The ``Bank Connector`` that was executed.
    - ``provider_name``: Provider slug (denormalized for quick filtering).
    - ``status``: ``"Success"`` | ``"Partial"`` | ``"Error"``.
    - ``trigger``: ``"Manual"`` | ``"Scheduled"``.
    - ``started_at`` / ``ended_at`` / ``duration_seconds``: Timing.
    - ``total_created`` / ``total_skipped``: Transaction counts.
    - ``date_from`` / ``date_to``: Import window.
    - ``per_account_results``: JSON list of per-account result dicts.
    - ``error_summary`` / ``error_traceback``: Failure details.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BankImportRunLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		connector_name: DF.Data
		provider_name: DF.Data | None
		status: DF.Literal["Success", "Partial", "Error"]
		trigger: DF.Literal["Manual", "Scheduled"] | None
		started_at: DF.Datetime
		ended_at: DF.Datetime | None
		duration_seconds: DF.Float
		total_created: DF.Int
		total_skipped: DF.Int
		date_from: DF.Date | None
		date_to: DF.Date | None
		per_account_results: DF.JSON | None
		error_summary: DF.SmallText | None
		error_traceback: DF.LongText | None
	# end: auto-generated types

	def before_insert(self) -> None:
		"""Ensure required fields are set before first save."""
		if not self.started_at:
			self.started_at = frappe.utils.now_datetime()

	def before_save(self) -> None:
		"""Compute duration before persisting."""
		if self.started_at and self.ended_at:
			from frappe.utils import time_diff_in_seconds

			self.duration_seconds = time_diff_in_seconds(self.ended_at, self.started_at)
