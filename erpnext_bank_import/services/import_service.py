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

import frappe

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
# Public API
# ---------------------------------------------------------------------------


def import_transactions(
	connector_name: str,
	date_from: str | None = None,
	date_to: str | None = None,
	account_ids: list[str] | None = None,
	trigger: str = "Manual",
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

		for mapping in doc.account_mappings:
			if not mapping.is_enabled:
				continue
			if account_ids and mapping.provider_account_id not in account_ids:
				continue

			result = _import_for_account(
				connector=connector,
				connector_name=connector_name,
				mapping=mapping,
				date_from=date_from,
				date_to=date_to,
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


# ---------------------------------------------------------------------------
# Per-account import
# ---------------------------------------------------------------------------


def _import_for_account(
	connector,
	connector_name: str,
	mapping,
	date_from: str | None = None,
	date_to: str | None = None,
) -> dict:
	"""Fetch, deduplicate, and insert transactions for one account mapping.

	Args:
	    connector: An initialised ``BankConnector`` instance.
	    connector_name: Name of the Bank Connector (for logging).
	    mapping: A ``BankConnectorAccountMapping`` child-table row.
	    date_from: Explicit start date, or ``None`` for incremental.
	    date_to: Explicit end date, or ``None`` for today.

	Returns:
	    Dict with keys ``account_id``, ``created``, ``skipped``, ``error``.
	"""
	account_id = mapping.provider_account_id
	bank_account = mapping.bank_account

	# -- Resolve date window ------------------------------------------------
	update_cursor = date_from is None  # incremental → advance cursor

	if date_from is None:
		if mapping.last_synced_at:
			date_from = str(mapping.last_synced_at)
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
	if hasattr(connector, "set_current_bank_account"):
		connector.set_current_bank_account(bank_account)

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

	for txn in raw_txns:
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

	# -- Batch-check existing transactions ---------------------------------
	existing_ids = _get_existing_transaction_ids(bank_account, transaction_ids)

	# -- Insert new transactions -------------------------------------------
	created = 0
	skipped = 0
	max_txn_date: str = date_from

	for bt_dict in mapped_txns:
		tid = bt_dict.get("transaction_id")
		if tid and tid in existing_ids:
			skipped += 1
			continue

		try:
			doc = frappe.get_doc({"doctype": "Bank Transaction", **bt_dict})
			doc.insert()
			doc.submit()
			created += 1

			txn_date = bt_dict.get("date")
			if txn_date and txn_date > max_txn_date:
				max_txn_date = txn_date

		except Exception as e:
			diagnostic = diagnose_error(e, phase=PHASE_INSERT)
			frappe.log_error(
				message=f"{diagnostic['error_type']}: {e}\nAction: {diagnostic['suggested_action']}",
				title=f"Bank Transaction insert failed for {tid or '(no id)'}",
			)
			# continue with next transaction

	# -- Update sync cursor (incremental mode only) ------------------------
	if update_cursor and created > 0:
		_update_last_synced(mapping, max_txn_date)

	return _account_result(account_id, created=created, skipped=skipped)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_existing_transaction_ids(bank_account: str, transaction_ids: list[str]) -> set[str]:
	"""Batch-check which ``transaction_id``\\ s already exist for this bank account.

	Follows ERPNext's ``check_for_conflicts()`` pattern
	(:func:`frappe.get_all`) but uses exact ``transaction_id`` match
	instead of a date-range overlap, giving precise dedup rather than
	a blanket warning.
	"""
	if not transaction_ids:
		return set()

	existing = frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": bank_account,
			"transaction_id": ["in", transaction_ids],
			"docstatus": 1,
		},
		pluck="transaction_id",
	)
	return set(existing)


def _update_last_synced(mapping, max_date: str) -> None:
	"""Persist the latest transaction date as the per-account sync cursor."""
	mapping.db_set("last_synced_at", max_date)


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
	"import_all_enabled_connectors",
	"import_transactions",
]
