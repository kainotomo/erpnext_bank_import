"""Health check endpoint for the bank import system.

Provides a whitelisted method that external monitoring tools
(UptimeRobot, Nagios, etc.) can poll to verify the app is healthy.

Returns connector statuses, token expiry info, and recent import
error counts — all from existing Frappe/ERPNext data stores (no
custom health-tracking infrastructure needed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import frappe


@frappe.whitelist()
def check() -> dict:
	"""Return a health summary for all enabled connectors.

	Returns:
	    A dict with:
	    - ``status``: ``"healthy"`` if no recent errors, else ``"degraded"``
	    - ``connectors``: list of per-connector health details
	    - ``recent_global_error_count``: total errors across all
	      connectors in the last 24h
	    - ``last_scheduler_run``: status of the most recent hourly job
	"""
	since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

	connectors: list[dict] = []
	global_error_count = 0

	for record in frappe.get_all(
		"Bank Connector",
		filters={"enabled": 1},
		fields=["connector_name", "provider_name"],
		order_by="connector_name",
	):
		# Most recent run log for this connector
		last_run = frappe.get_all(
			"Bank Import Run Log",
			filters={"connector_name": record.connector_name},
			fields=["name", "status", "started_at", "total_created", "total_skipped", "error_summary"],
			order_by="started_at desc",
			limit=1,
		)

		# Error count in last 24h
		error_count = frappe.db.count(
			"Bank Import Run Log",
			filters={
				"connector_name": record.connector_name,
				"status": ("in", ("Error", "Partial")),
				"started_at": (">=", since),
			},
		)
		global_error_count += error_count

		connectors.append(
			{
				"connector_name": record.connector_name,
				"provider_name": record.provider_name,
				"last_run": last_run[0] if last_run else None,
				"recent_error_count_24h": error_count,
			}
		)

	# Last scheduled job run status (from Frappe's built-in Scheduled Job Log)
	last_scheduler = frappe.get_all(
		"Scheduled Job Log",
		filters={
			"scheduled_job_type": ("like", "%import_all_enabled_connectors%"),
		},
		fields=["status", "creation", "details"],
		order_by="creation desc",
		limit=1,
	)

	overall_status = "healthy" if global_error_count == 0 else "degraded"

	return {
		"status": overall_status,
		"connectors": connectors,
		"recent_global_error_count_24h": global_error_count,
		"last_scheduler_run": last_scheduler[0] if last_scheduler else None,
	}


__all__ = [
	"check",
]
