// Client-side script for Bank Connector form
// Adds an "Authorize" button that opens the Revolut consent URL in a new tab.

frappe.ui.form.on("Bank Connector", {
	refresh: function (frm) {
		// Only show for Revolut connectors with a stored config
		if (frm.doc.provider_name !== "revolut") return;
		if (!frm.doc.client_id) return;

		frm.add_custom_button(
			__("Authorize Connector"),
			function () {
				frappe.call({
					method:
						"erpnext_bank_import.connectors.revolut.start_oauth_flow",
					args: { connector_name: frm.doc.connector_name },
					callback: function (r) {
						if (r.message) {
							window.open(r.message, "_blank");
						}
					},
				});
			},
			__("Actions")
		);
	},
});
