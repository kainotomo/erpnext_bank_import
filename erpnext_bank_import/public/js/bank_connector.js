// Client-side script for Bank Connector form
// - Auto-fills API URLs based on Sandbox Mode for Revolut
// - Adds an "Authorize Connector" button

const REVOLUT_URLS = {
	sandbox: {
		api_base_url: "https://sandbox-b2b.revolut.com/api/1.0",
		authorize_url: "https://sandbox-b2b.revolut.com/api/1.0/auth/authorize",
		token_url: "https://sandbox-b2b.revolut.com/api/1.0/auth/token",
	},
	production: {
		api_base_url: "https://b2b.revolut.com/api/1.0",
		authorize_url: "https://b2b.revolut.com/api/1.0/auth/authorize",
		token_url: "https://b2b.revolut.com/api/1.0/auth/token",
	},
};

frappe.ui.form.on("Bank Connector", {
	refresh: function (frm) {
		_setup_authorize_button(frm);
	},

	sandbox: function (frm) {
		_setup_urls(frm);
	},

	provider_name: function (frm) {
		if (frm.doc.provider_name === "revolut" && !frm.doc.api_base_url) {
			frm.set_value("sandbox", 1);
			_setup_urls(frm);
		}
	},
});

function _setup_urls(frm) {
	if (frm.doc.provider_name !== "revolut") return;

	const mode = frm.doc.sandbox ? "sandbox" : "production";
	const urls = REVOLUT_URLS[mode];

	// Only auto-fill if fields are empty or sandbox just changed
	if (!frm.doc.api_base_url || frm.doc.api_base_url.includes("revolut.com")) {
		frm.set_value("api_base_url", urls.api_base_url);
	}
	if (!frm.doc.authorize_url || frm.doc.authorize_url.includes("revolut.com")) {
		frm.set_value("authorize_url", urls.authorize_url);
	}
	if (!frm.doc.token_url || frm.doc.token_url.includes("revolut.com")) {
		frm.set_value("token_url", urls.token_url);
	}
}

function _setup_authorize_button(frm) {
	if (frm.doc.provider_name !== "revolut") return;

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
}
