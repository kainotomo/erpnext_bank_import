import frappe
from frappe import _


def before_install():
	"""Validate runtime environment before app installation.

	Checks:
	- Frappe framework version >= 16
	- ERPNext app is installed and version >= 16

	Uses frappe.throw() to halt installation with a descriptive message
	if any check fails.
	"""
	_check_frappe_version()
	_check_erpnext_installed()


def after_install():
	"""Log installation success summary for auditability."""
	import erpnext_bank_import

	frappe.logger().info(
		_(
			"erpnext_bank_import v{app_version} installed successfully. "
			"Frappe: {frappe_version}, ERPNext: {erpnext_version}"
		).format(
			app_version=erpnext_bank_import.__version__,
			frappe_version=frappe.__version__,
			erpnext_version=_get_erpnext_version() or "not detected",
		)
	)


def _check_frappe_version():
	version = frappe.__version__
	major = int(version.split(".")[0])
	if major < 16:
		frappe.throw(
			_("erpnext_bank_import requires Frappe >= 16.0.0. Detected Frappe v{version}.").format(
				version=version
			)
		)


def _check_erpnext_installed():
	if "erpnext" not in frappe.get_installed_apps():
		frappe.throw(
			_("erpnext_bank_import requires ERPNext to be installed. Install ERPNext first, then try again.")
		)

	version = _get_erpnext_version()
	if version:
		major = int(version.split(".")[0])
		if major < 16:
			frappe.throw(
				_("erpnext_bank_import requires ERPNext >= 16.0.0. Detected ERPNext v{version}.").format(
					version=version
				)
			)


def _get_erpnext_version():
	"""Return ERPNext version string, or None if not available."""
	try:
		import erpnext

		return erpnext.__version__
	except ImportError:
		return None
