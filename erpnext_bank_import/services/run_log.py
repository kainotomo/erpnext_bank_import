"""Structured run logging for bank import operations.

Provides ``RunLogger`` — a context manager that creates and populates
``Bank Import Run Log`` records for each import run.  Usage:

.. code-block:: python

    from erpnext_bank_import.services.run_log import RunLogger

    with RunLogger(
        connector_name="My Revolut",
        provider_name="revolut",
        trigger="scheduled",
        date_from="2026-01-01",
        date_to="2026-06-30",
    ) as run:
        result = do_import(...)
        run.add_account_result(account_id="...", created=10, skipped=2)

    # On exit, the run log is auto-completed with status, duration, etc.
    print(run.name)  # the DocType record name
"""

from __future__ import annotations

import traceback
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

import frappe
from frappe.utils import now_datetime

from erpnext_bank_import.connectors.exceptions import is_transient_error

# ------------------------------------------------------------------
# RunLogger context manager
# ------------------------------------------------------------------


class RunLogger(AbstractContextManager):
	"""Context manager that creates a structured run log for an import.

	Creates a ``Bank Import Run Log`` record on entry and completes it
	(with status, duration, aggregated counts) on exit — even if an
	exception occurs.

	Typical usage::

	    with RunLogger("My Revolut", "revolut") as run:
	        run.add_account_result(account_id="acc-1", created=5, skipped=0)
	        run.add_account_result(account_id="acc-2", created=3, skipped=1)
	"""

	def __init__(
		self,
		connector_name: str,
		provider_name: str | None = None,
		trigger: str = "Manual",
		date_from: str | None = None,
		date_to: str | None = None,
	) -> None:
		"""Initialise (does not create the log record yet — that happens on enter).

		Args:
		    connector_name: Name of the ``Bank Connector`` record.
		    provider_name: Provider slug (e.g. ``"revolut"``, ``"mock"``).
		    trigger: ``"Manual"`` or ``"Scheduled"``.
		    date_from: Start of the import window (YYYY-MM-DD).
		    date_to: End of the import window (YYYY-MM-DD).
		"""
		self._connector_name = connector_name
		self._provider_name = provider_name
		self._trigger = trigger
		self._date_from = date_from
		self._date_to = date_to
		self._account_results: list[dict[str, Any]] = []
		self._error_summary: str | None = None
		self._error_traceback: str | None = None
		try:
			self._started_at = now_datetime()
		except Exception:
			self._started_at = datetime.now()
		self.name: str | None = None
		"""DocType name of the created log record, set after entry."""

	def __enter__(self) -> "RunLogger":
		"""Create the ``Bank Import Run Log`` record."""
		doc = frappe.get_doc(
			{
				"doctype": "Bank Import Run Log",
				"connector_name": self._connector_name,
				"provider_name": self._provider_name,
				"status": "Success",  # will be updated on exit if error
				"trigger": self._trigger,
				"started_at": self._started_at,
				"date_from": self._date_from,
				"date_to": self._date_to,
				"total_created": 0,
				"total_skipped": 0,
			}
		)
		doc.insert(ignore_permissions=True)
		self.name = doc.name
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: Any,
	) -> bool:
		"""Finalise the run log record.

		If an exception occurred within the context, captures it as the
		run's error and sets status accordingly.
		"""
		try:
			ended_at = now_datetime()
		except Exception:
			ended_at = datetime.now()
		status = "Success"
		error_summary: str | None = None
		error_traceback: str | None = None

		if exc_val is not None:
			status = "Error"
			error_summary = f"{type(exc_val).__name__}: {exc_val}"
			error_traceback = "".join(traceback.format_exception(type(exc_val), exc_val, exc_tb))
		elif self._error_summary:
			status = "Error"
			error_summary = self._error_summary
			error_traceback = self._error_traceback
		elif self._account_results:
			has_errors = any(r.get("error") for r in self._account_results)
			status = "Partial" if has_errors else "Success"

		# Compute total created/skipped
		total_created = sum(r.get("created", 0) for r in self._account_results)
		total_skipped = sum(r.get("skipped", 0) for r in self._account_results)

		try:
			frappe.db.set_value(
				"Bank Import Run Log",
				self.name,
				{
					"status": status,
					"ended_at": ended_at,
					"total_created": total_created,
					"total_skipped": total_skipped,
					"per_account_results": frappe.as_json(self._account_results),
					"error_summary": error_summary or self._error_summary,
					"error_traceback": error_traceback or self._error_traceback,
				},
			)
		except Exception:
			# Logging infrastructure must never crash the caller
			frappe.log_error(
				message=f"Failed to update run log {self.name}: {exc_val}",
				title="Run log update failed",
			)

		# Do NOT suppress the original exception
		return False

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	def add_account_result(
		self,
		account_id: str,
		created: int = 0,
		skipped: int = 0,
		error: str | None = None,
		**extra: Any,
	) -> None:
		"""Record the result for one account mapping.

		Args:
		    account_id: Provider-specific account identifier.
		    created: Number of new transactions created.
		    skipped: Number of duplicate transactions skipped.
		    error: Error message (if any).
		    **extra: Additional context (e.g. ``date_from``, ``date_to``).
		"""
		self._account_results.append(
			{
				"account_id": account_id,
				"created": created,
				"skipped": skipped,
				"error": error,
				**extra,
			}
		)

	def set_error(self, summary: str, traceback_str: str | None = None) -> None:
		"""Explicitly mark the run as errored with a summary.

		Use this when an error occurs that is handled before the context
		exits (e.g. a caught exception that doesn't propagate).
		"""
		self._error_summary = summary
		if traceback_str:
			self._error_traceback = traceback_str

	@property
	def account_results(self) -> list[dict[str, Any]]:
		"""Return the list of per-account results recorded so far."""
		return list(self._account_results)

	@property
	def total_created(self) -> int:
		"""Return the sum of created transactions across all accounts."""
		return sum(r.get("created", 0) for r in self._account_results)

	@property
	def total_skipped(self) -> int:
		"""Return the sum of skipped transactions across all accounts."""
		return sum(r.get("skipped", 0) for r in self._account_results)


__all__ = [
	"RunLogger",
]
