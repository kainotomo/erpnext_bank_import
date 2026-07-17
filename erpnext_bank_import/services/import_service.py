"""Unified import orchestration service.

Provides a single pipeline for both manual and scheduled imports,
with duplicate-safe writes using ``transaction_id``-based dedup
(following the same ``frappe.get_all`` pattern as ERPNext's
``BankStatementImportLog.check_for_conflicts``).

Each run produces a structured ``Bank Import Run Log`` record with
per-account results, timing, and diagnostic context.

Usage
-----
.. code-block:: python

    from erpnext_bank_import.services.import_service import import_transactions

    # Incremental (uses last_synced_at per mapping)
    summary = import_transactions("My Revolut")

    # Backfill explicit window
    summary = import_transactions("My Revolut", date_from="2026-01-01", date_to="2026-03-31")

    # Scheduled job
    from erpnext_bank_import.services.import_service import import_all_enabled_connectors

    import_all_enabled_connectors()
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC

import frappe
from frappe.utils import create_batch, nowdate

from erpnext_bank_import.connectors import (
	get_all_enabled_connectors,
	get_connector,
	get_connector_config,
)
from erpnext_bank_import.connectors.exceptions import (
	AuthenticationError,
	ConnectorError,
	MaxRetriesExceededError,
)
from erpnext_bank_import.schema.transaction import apply_mapping
from erpnext_bank_import.services.diagnostics import (
	PHASE_AUTH,
	PHASE_FETCH,
	PHASE_INSERT,
	PHASE_UNKNOWN,
	diagnose_error,
)
from erpnext_bank_import.services.run_log import RunLogger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE: int = 500
"""Number of transactions processed per batch for progress reporting.

Matches ERPNext's Importer batch-size pattern (``data_import_batch_size``
defaults to 1000; we use a smaller chunk for finer progress granularity).
"""

DEDUP_CHUNK_SIZE: int = 500
"""Max transaction IDs per SQL ``IN`` clause when checking for duplicates.

MariaDB/MySQL have query-size limits; chunking prevents hitting them."""

PROGRESS_EVENT: str = "bank_import_progress"
"""Realtime event name published during import for UI progress."""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_transactions(
	connector_name: str,
	date_from: str | None = None,
	date_to: str | None = None,
	account_ids: list[str] | None = None,
	trigger: str = "Manual",
	on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
	"""Import transactions for a Bank Connector.

	Args:
	    connector_name: Name of the ``Bank Connector`` DocType record.
	    date_from: Start date (``YYYY-MM-DD``).  ``None`` means
	        incremental sync (uses ``last_synced_at`` per mapping).
	    date_to: End date (``YYYY-MM-DD``).  ``None`` means today.
	    account_ids: Optional list of provider account IDs to restrict
	        the import to specific mappings.
	    trigger: ``"Manual"`` or ``"Scheduled"`` — recorded in the run log.
	    on_progress: Optional callback ``fn(current, total, message)``
	        invoked after each batch for progress reporting.

	Returns:
	    A summary dict with keys ``connector_name``, ``status``
	    (``"success"`` | ``"partial"`` | ``"error"``), ``results``
	    (list of per-account results), and optionally ``error``.
	"""
	# Load config & instantiate connector
	try:
		config = get_connector_config(connector_name)
		connector = get_connector(config.provider_name, config=config)
	except Exception as e:
		return _error_summary(connector_name, f"Configuration error: {e}")

	# Validate import window for manual backfills
	if trigger == "Manual":
		window_error = _validate_import_window(connector_name, date_from, date_to)
		if window_error:
			return _error_summary(connector_name, window_error)

	# Resolve date window for the run log
	run_date_from = date_from
	run_date_to = date_to

	# Create the run logger (context manager handles completion on exit)
	with RunLogger(
		connector_name=connector_name,
		provider_name=config.provider_name,
		trigger=trigger,
		date_from=run_date_from,
		date_to=run_date_to,
	) as run:

		def _progress(current: int, total: int, msg: str) -> None:
			run.set_progress(current, total, msg)
			if on_progress:
				on_progress(current, total, msg)

		# Ensure authentication
		auth_ok = _ensure_auth(connector)
		if not auth_ok:
			diagnostic = diagnose_error(
				AuthenticationError("Authentication failed — unable to refresh token"),
				phase=PHASE_AUTH,
			)
			run.set_error(
				summary=diagnostic["suggested_action"],
				traceback_str=diagnostic["context"].get("message"),
			)
			return _error_summary(
				connector_name,
				f"{diagnostic['error_type']}: {diagnostic['suggested_action']}",
			)

		# Load the parent DocType to iterate account mappings
		doc = frappe.get_doc("Bank Connector", connector_name)

		results: list[dict] = []
		overall_status = "success"
		total_mappings = sum(
			1
			for m in doc.account_mappings
			if m.is_enabled and (not account_ids or m.provider_account_id in account_ids)
		)
		mappings_done = 0

		for mapping in doc.account_mappings:
			if not mapping.is_enabled:
				continue
			if account_ids and mapping.provider_account_id not in account_ids:
				continue

			mappings_done += 1
			_progress(
				mappings_done,
				total_mappings,
				f"Processing account {getattr(mapping, 'provider_account_name', None) or mapping.provider_account_id}...",
			)

			result = _import_for_account(
				connector=connector,
				connector_name=connector_name,
				mapping=mapping,
				date_from=date_from,
				date_to=date_to,
				on_progress=_progress,
			)
			results.append(result)
			run.add_account_result(**result)
			if result.get("error"):
				overall_status = "partial"

		if not results:
			msg = "No enabled account mappings found"
			run.set_error(summary=msg)
			return _error_summary(connector_name, msg)

	return {
		"connector_name": connector_name,
		"status": overall_status,
		"results": results,
	}


def enqueue_import(
	connector_name: str,
	date_from: str | None = None,
	date_to: str | None = None,
	trigger: str = "Manual",
) -> str | None:
	"""Enqueue an import as a background job (ERPNext-aligned pattern).

	Follows the same approach as ERPNext's
	``BankStatementImport.start_import()``: the job runs in the
	``default`` queue with a generous timeout.

	Args:
	    connector_name: Name of the ``Bank Connector`` record.
	    date_from: Optional start date (``YYYY-MM-DD``).
	    date_to: Optional end date (``YYYY-MM-DD``).
	    trigger: ``"Manual"`` or ``"Scheduled"``.

	Returns:
	    A ``job_id`` if the job was enqueued, or ``None`` if an
	    identical job is already pending.
	"""
	from frappe.utils.background_jobs import is_job_enqueued
	from frappe.utils.scheduler import is_scheduler_inactive

	run_now = frappe.in_test or frappe.conf.developer_mode

	if is_scheduler_inactive() and not run_now:
		frappe.msgprint(
			frappe._("Scheduler is inactive. Cannot import data in background."),
			title=frappe._("Scheduler Inactive"),
		)
		return None

	job_id = f"bank_import::{connector_name}"
	if not is_job_enqueued(job_id):
		frappe.enqueue(
			"erpnext_bank_import.services.import_service.import_transactions",
			queue="default",
			timeout=6000,
			job_id=job_id,
			connector_name=connector_name,
			date_from=date_from,
			date_to=date_to,
			trigger=trigger,
			on_progress=_publish_progress,
			now=run_now,
		)
		frappe.msgprint(
			frappe._("Import for '{0}' has been queued. Check Bank Import Run Log for results.").format(
				connector_name
			),
			title=frappe._("Import Queued"),
			indicator="green",
		)
		return job_id

	frappe.msgprint(
		frappe._("An import for '{0}' is already in progress.").format(connector_name),
		title=frappe._("Import Queued"),
	)
	return None


def import_all_enabled_connectors() -> list[dict]:
	"""Import transactions for every enabled connector.

	Entry point for the hourly scheduled job.  Calls
	:func:`import_transactions` with no explicit dates and
	``trigger="Scheduled"`` so that every connector runs in
	incremental mode.
	"""
	summaries: list[dict] = []
	failures: list[tuple[str, str]] = []

	for name in get_all_enabled_connectors():
		try:
			summary = import_transactions(name, trigger="Scheduled")
			summaries.append(summary)
		except Exception as e:
			diagnostic = diagnose_error(e, phase=PHASE_UNKNOWN)
			frappe.log_error(
				message=f"{diagnostic['error_type']}: {e}\nAction: {diagnostic['suggested_action']}",
				title=f"Bank import failed for {name}",
			)
			failures.append((name, diagnostic["suggested_action"]))
			summaries.append(_error_summary(name, str(e)))

	# If any connectors failed, write a single aggregated log entry
	if failures:
		agg_msg = "; ".join(f"{name}: {action}" for name, action in failures)
		frappe.log_error(
			message=agg_msg,
			title=f"Bank import completed with {len(failures)} failure(s)",
		)

	return summaries


def get_import_health(connector_name: str, hours: int = 24) -> dict:
	"""Return a health summary for a connector by cross-referencing
	Frappe's ``Scheduled Job Log`` with our ``Bank Import Run Log``.

	Uses Frappe's built-in scheduler log (no custom tracking needed)
	to show recent run status, plus our per-connector run log for
	per-account details.

	Args:
	    connector_name: The ``Bank Connector`` record name.
	    hours: Look-back window in hours (default 24).

	Returns:
	    A dict with keys:
	    - ``connector_name``
	    - ``recent_scheduler_runs``: list of ``Scheduled Job Log`` entries
	    - ``last_import_run``: the most recent ``Bank Import Run Log``
	    - ``recent_error_count``: number of failed runs in the window
	"""
	from datetime import datetime, timedelta, timezone

	since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

	# Query Frappe's built-in Scheduled Job Log
	scheduler_logs = frappe.get_all(
		"Scheduled Job Log",
		filters={
			"scheduled_job_type": ("like", "%import_all_enabled_connectors%"),
			"creation": (">=", since),
		},
		fields=["status", "creation", "details"],
		order_by="creation desc",
		limit=20,
	)

	# Query our own run log for this connector
	last_run = frappe.get_all(
		"Bank Import Run Log",
		filters={"connector_name": connector_name},
		fields=[
			"name",
			"status",
			"started_at",
			"ended_at",
			"total_created",
			"total_skipped",
			"error_summary",
		],
		order_by="started_at desc",
		limit=1,
	)

	error_count = frappe.db.count(
		"Bank Import Run Log",
		filters={
			"connector_name": connector_name,
			"status": ("in", ("Error", "Partial")),
			"started_at": (">=", since),
		},
	)

	return {
		"connector_name": connector_name,
		"recent_scheduler_runs": scheduler_logs,
		"last_import_run": last_run[0] if last_run else None,
		"recent_error_count": error_count,
	}


# ---------------------------------------------------------------------------
# Per-account import
# ---------------------------------------------------------------------------


def _import_for_account(
	connector,
	connector_name: str,
	mapping,
	date_from: str | None = None,
	date_to: str | None = None,
	on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
	"""Fetch, deduplicate, and insert transactions for one account mapping.

	Args:
	    connector: An initialised ``BankConnector`` instance.
	    connector_name: Name of the Bank Connector (for logging).
	    mapping: A ``BankConnectorAccountMapping`` child-table row.
	    date_from: Explicit start date, or ``None`` for incremental.
	    date_to: Explicit end date, or ``None`` for today.
	    on_progress: Optional callback ``fn(current, total, message)``
	        invoked after each batch.

	Returns:
	    Dict with keys ``account_id``, ``created``, ``skipped``, ``error``.
	"""
	account_id = mapping.provider_account_id
	bank_account = mapping.bank_account

	# -- Resolve date window ------------------------------------------------
	update_cursor = date_from is None  # incremental → advance cursor

	if date_from is None:
		if mapping.last_synced_at:
			# last_synced_at is a datetime object; convert to YYYY-MM-DD
			date_from = frappe.utils.getdate(mapping.last_synced_at).isoformat()
		else:
			date_from = frappe.utils.add_days(frappe.utils.nowdate(), -90)  # no cursor yet → default 90 days

	if date_to is None:
		date_to = frappe.utils.today()

	# -- Ensure auth -------------------------------------------------------
	try:
		if not connector.is_authenticated():
			connector.refresh_token()
	except AuthenticationError:
		diagnostic = diagnose_error(AuthenticationError("Authentication failed"), phase=PHASE_AUTH)
		return _account_result(account_id, error=diagnostic["suggested_action"])

	# -- Set current bank account for token resolution (Revolut etc.) ------
	# Use the connector name (not the Bank Account name) because the
	# OAuth callback stores the token keyed by connector_name.
	if hasattr(connector, "set_current_bank_account"):
		connector.set_current_bank_account(connector_name)

	# -- Fetch transactions from the bank API ------------------------------
	try:
		raw_txns = connector.fetch_all_transactions(
			account_id=account_id,
			date_from=date_from,
			date_to=date_to,
		)
	except MaxRetriesExceededError as e:
		diagnostic = diagnose_error(e, phase=PHASE_FETCH)
		frappe.log_error(
			message=f"{diagnostic['error_type']}: {e}\nAction: {diagnostic['suggested_action']}",
			title=f"Fetch failed for {connector_name} / {account_id}",
		)
		return _account_result(account_id, error=f"{diagnostic['error_type']}: {e}")
	except Exception as e:
		diagnostic = diagnose_error(e, phase=PHASE_FETCH)
		frappe.log_error(
			message=f"{diagnostic['error_type']}: {e}\nAction: {diagnostic['suggested_action']}",
			title=f"Fetch failed for {connector_name} / {account_id}",
		)
		return _account_result(account_id, error=diagnostic["suggested_action"])

	if not raw_txns:
		return _account_result(account_id, created=0, skipped=0)

	# -- Normalise & collect IDs for dedup  --------------------------------
	mapped_txns: list[dict] = []
	transaction_ids: list[str] = []

	# Resolve company from the Bank Account
	company = frappe.get_cached_value("Bank Account", bank_account, "company")

	total_fetched = len(raw_txns)

	for idx, txn in enumerate(raw_txns):
		if on_progress and idx % BATCH_SIZE == 0:
			on_progress(idx, total_fetched, f"Normalising transactions... ({idx}/{total_fetched})")

		try:
			bt_dict = apply_mapping(txn)
		except Exception as e:
			diagnostic = diagnose_error(e, phase=PHASE_FETCH)
			frappe.log_error(
				message=f"Normalization failed for {connector_name} / {account_id}: {e}",
				title="Transaction normalisation failed",
			)
			continue
		bt_dict["bank_account"] = bank_account
		bt_dict["company"] = company
		# Follow ERPNext's insert_transactions pattern: start as Unreconciled
		bt_dict.setdefault("status", "Unreconciled")
		mapped_txns.append(bt_dict)

		tid = bt_dict.get("transaction_id")
		if tid:
			transaction_ids.append(tid)

	# -- Batch-check existing transactions (chunked for SQL safety) ---------
	existing_ids = _get_existing_transaction_ids(bank_account, transaction_ids)

	# -- Insert new transactions in batches --------------------------------
	created = 0
	skipped = 0
	max_txn_date: str = date_from

	# Determine which transactions need inserting
	to_insert = []
	for bt_dict in mapped_txns:
		tid = bt_dict.get("transaction_id")
		if tid and tid in existing_ids:
			skipped += 1
		else:
			to_insert.append(bt_dict)

	total_to_insert = len(to_insert)

	for batch_idx, batch in enumerate(create_batch(to_insert, BATCH_SIZE)):
		batch_created = 0
		batch_errors = 0

		for bt_dict in batch:
			try:
				doc = frappe.get_doc({"doctype": "Bank Transaction", **bt_dict})
				doc.insert()
				doc.submit()
				created += 1
				batch_created += 1

				txn_date = bt_dict.get("date")
				if txn_date and txn_date > max_txn_date:
					max_txn_date = txn_date

			except Exception as e:
				batch_errors += 1
				diagnostic = diagnose_error(e, phase=PHASE_INSERT)
				frappe.log_error(
					message=f"{diagnostic['error_type']}: {e}\nAction: {diagnostic['suggested_action']}",
					title=f"Bank Transaction insert failed for {bt_dict.get('transaction_id', '(no id)')}",
				)
				# continue with next transaction

		# Emit progress after each batch (ERPNext-aligned pattern)
		if on_progress:
			done = (batch_idx + 1) * BATCH_SIZE
			if done > total_to_insert:
				done = total_to_insert
			on_progress(
				done,
				total_to_insert,
				f"Inserted {created} transactions ({batch_created} in this batch, {batch_errors} errors)",
			)

	# -- Update sync cursor (incremental mode only) ------------------------
	if update_cursor and created > 0:
		_update_last_synced(mapping, max_txn_date)

	return _account_result(account_id, created=created, skipped=skipped)


# ---------------------------------------------------------------------------
# Progress publishing (used by enqueue_import)
# ---------------------------------------------------------------------------


def _publish_progress(current: int, total: int, message: str) -> None:
	"""Publish import progress via Frappe realtime, scoped to the current user.

	ERPNext-aligned pattern: the ``Importer`` publishes
	``data_import_progress`` events; we publish ``bank_import_progress``.
	The ``user`` parameter ensures the event is only sent to the user
	who triggered the import, not to all site users.
	"""
	frappe.publish_realtime(
		PROGRESS_EVENT,
		{"current": current, "total": total, "message": message},
		user=frappe.session.user,
	)


# ---------------------------------------------------------------------------
# Window validation
# ---------------------------------------------------------------------------


def _validate_import_window(connector_name: str, date_from: str | None, date_to: str | None) -> str | None:
	"""Check that the requested import window is within the configured limit.

	Returns an error message string if the window exceeds
	``max_import_window_days``, or ``None`` if the window is valid.
	Incremental syncs (no explicit ``date_from``) are always allowed.

	The ``force_full_backfill`` flag on the connector bypasses this check.
	"""
	if date_from is None:
		return None  # incremental sync, always allowed

	doc = frappe.get_doc("Bank Connector", connector_name)

	# Allow override via force_full_backfill checkbox (use attribute access)
	if getattr(doc, "force_full_backfill", False):
		return None

	max_days = getattr(doc, "max_import_window_days", None) or 365
	_from = frappe.utils.getdate(date_from)
	_to = frappe.utils.getdate(date_to or nowdate())
	window_days = (_to - _from).days

	if window_days > max_days:
		return (
			f"The requested import window ({window_days} days) exceeds the "
			f"maximum allowed ({max_days} days) for '{connector_name}'. "
			f"Either reduce the date range or enable the "
			f"'Force Full Backfill' option on the Bank Connector record."
		)

	return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_existing_transaction_ids(bank_account: str, transaction_ids: list[str]) -> set[str]:
	"""Batch-check which ``transaction_id``\\ s already exist for this bank account.

	Chunks the ``IN`` clause into ``DEDUP_CHUNK_SIZE`` batches to avoid
	hitting SQL query-size limits with very large import windows.

	Follows ERPNext's ``check_for_conflicts()`` pattern
	(:func:`frappe.get_all`) but uses exact ``transaction_id`` match
	instead of a date-range overlap, giving precise dedup rather than
	a blanket warning.
	"""
	if not transaction_ids:
		return set()

	existing: set[str] = set()
	for chunk in create_batch(transaction_ids, DEDUP_CHUNK_SIZE):
		chunk_result = frappe.get_all(
			"Bank Transaction",
			filters={
				"bank_account": bank_account,
				"transaction_id": ["in", list(chunk)],
				"docstatus": 1,
			},
			pluck="transaction_id",
		)
		existing.update(chunk_result)

	return existing


def _update_last_synced(mapping, max_date: str) -> None:
	"""Persist the latest transaction date as the per-account sync cursor.

	Also updates ``last_integration_date`` on the linked ``Bank Account``
	doctype so ERPNext's native banking views reflect the latest sync.
	"""
	mapping.db_set("last_synced_at", max_date)

	# Update ERPNext's native last_integration_date on the Bank Account
	if mapping.bank_account:
		frappe.db.set_value(
			"Bank Account",
			mapping.bank_account,
			"last_integration_date",
			max_date,
		)


def _ensure_auth(connector) -> bool:
	"""Return ``True`` if the connector has a valid session.

	Attempts a token refresh if the initial check fails.
	"""
	try:
		if connector.is_authenticated():
			return True
		connector.refresh_token()
		return True
	except AuthenticationError:
		return False


def _account_result(
	account_id: str,
	created: int = 0,
	skipped: int = 0,
	error: str | None = None,
) -> dict:
	return {
		"account_id": account_id,
		"created": created,
		"skipped": skipped,
		"error": error,
	}


def _error_summary(connector_name: str, error: str) -> dict:
	return {
		"connector_name": connector_name,
		"status": "error",
		"results": [],
		"error": error,
	}


__all__ = [
	"enqueue_import",
	"get_import_health",
	"import_all_enabled_connectors",
	"import_transactions",
]
