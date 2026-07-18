// Client-side script for Bank Connector form
// - Auto-fills API URLs based on Sandbox Mode for Revolut or BoC
// - Adds provider-specific action buttons after save

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

const BOC_URLS = {
	sandbox: {
		api_base_url: "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2",
		authorize_url: "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2/oauth2/authorize",
		token_url: "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2/oauth2/token",
	},
	production: {
		api_base_url: "https://apis.bankofcyprus.com/df-boc-org-prd/prod/psd2",
		authorize_url: "https://apis.bankofcyprus.com/df-boc-org-prd/prod/psd2/oauth2/authorize",
		token_url: "https://apis.bankofcyprus.com/df-boc-org-prd/prod/psd2/oauth2/token",
	},
};

const EUROBANK_URLS = {
	sandbox: {
		api_base_url: "https://sandbox-apis.hellenicbank.com",
		authorize_url: "https://sandbox-oauth.hellenicbank.com/v2/oauth2/auth",
		token_url: "https://sandbox-oauth.hellenicbank.com/v2/token/exchange",
	},
	production: {
		api_base_url: "https://apisprod.hellenicbank.com",
		authorize_url: "https://oauthprod.hellenicbank.com/v2/oauth2/auth",
		token_url: "https://oauthprod.hellenicbank.com/v2/token/exchange",
	},
};

frappe.ui.form.on("Bank Connector", {
	refresh: function (frm) {
		// Auto-fill URLs for known providers on every load
		if (frm.doc.provider_name === "revolut") {
			_setup_urls(frm);
		} else if (frm.doc.provider_name === "bank_of_cyprus") {
			_setup_boc_urls(frm);
		} else if (frm.doc.provider_name === "eurobank") {
			_setup_eurobank_urls(frm);
		}
		// Only show action buttons after first save
		if (!frm.doc.__islocal) {
			_setup_actions(frm);
		}
	},

	sandbox: function (frm) {
		if (frm.doc.provider_name === "revolut") {
			_setup_urls(frm);
		} else if (frm.doc.provider_name === "bank_of_cyprus") {
			_setup_boc_urls(frm);
		} else if (frm.doc.provider_name === "eurobank") {
			_setup_eurobank_urls(frm);
		}
	},

	provider_name: function (frm) {
		if (frm.doc.provider_name === "revolut") {
			frm.set_value("sandbox", 1);
			_setup_urls(frm);
		} else if (frm.doc.provider_name === "bank_of_cyprus") {
			frm.set_value("sandbox", 1);
			_setup_boc_urls(frm);
		} else if (frm.doc.provider_name === "eurobank") {
			frm.set_value("sandbox", 1);
			_setup_eurobank_urls(frm);
		}
	},
});

function _setup_urls(frm) {
	if (frm.doc.provider_name !== "revolut") return;

	// Always set auth method for Revolut.
	frm.set_value("auth_method", "oauth2");

	// Set scopes for read-only access (only if not already customised).
	if (!frm.doc.scopes) {
		frm.set_value("scopes", "READ");
	}

	// Set JWT issuer to current domain (only if not already customised).
	if (!frm.doc.jwt_issuer) {
		frm.set_value("jwt_issuer", window.location.hostname);
	}

	const mode = frm.doc.sandbox ? "sandbox" : "production";
	const urls = REVOLUT_URLS[mode];

	// Always set the API URLs — the user just selected Revolut or toggled sandbox.
	frm.set_value("api_base_url", urls.api_base_url);
	frm.set_value("authorize_url", urls.authorize_url);
	frm.set_value("token_url", urls.token_url);

	// Set default redirect URI (only if not already customised).
	if (!frm.doc.redirect_uri) {
		const default_redirect =
			window.location.origin +
			"/api/method/erpnext_bank_import.connectors.revolut.oauth_callback";
		frm.set_value("redirect_uri", default_redirect);
	}
}

function _setup_boc_urls(frm) {
	if (frm.doc.provider_name !== "bank_of_cyprus") return;

	// Always set auth method for BoC.
	frm.set_value("auth_method", "oauth2");

	// Set scopes for BoC (both TPP and User OAuth2 scopes).
	frm.set_value("scopes", "TPPOAuth2Security UserOAuth2Security");

	const mode = frm.doc.sandbox ? "sandbox" : "production";
	const urls = BOC_URLS[mode];

	// Always set the API URLs — the user just selected BoC or toggled sandbox.
	frm.set_value("api_base_url", urls.api_base_url);
	frm.set_value("authorize_url", urls.authorize_url);
	frm.set_value("token_url", urls.token_url);

	// Always set the redirect URI to match the current site origin.
	const default_redirect =
		window.location.origin +
		"/api/method/erpnext_bank_import.connectors.bank_of_cyprus.oauth_callback";
	frm.set_value("redirect_uri", default_redirect);
}

function _setup_eurobank_urls(frm) {
	if (frm.doc.provider_name !== "eurobank") return;

	// Always set auth method for Eurobank.
	frm.set_value("auth_method", "oauth2");

	// Set B2B scopes for account information service.
	frm.set_value("scopes", "v2.b2b.get.accounts,v2.b2b.get.account.details,v2.b2b.get.account.transactions");

	const mode = frm.doc.sandbox ? "sandbox" : "production";
	const urls = EUROBANK_URLS[mode];

	// Always set the API URLs — the user just selected Eurobank or toggled sandbox.
	frm.set_value("api_base_url", urls.api_base_url);
	frm.set_value("authorize_url", urls.authorize_url);
	frm.set_value("token_url", urls.token_url);

	// Set the redirect URI to the callback endpoint.
	const default_redirect =
		window.location.origin +
		"/api/method/erpnext_bank_import.connectors.eurobank.oauth_callback";
	frm.set_value("redirect_uri", default_redirect);
}

function _setup_actions(frm) {
	if (frm.doc.provider_name === "revolut") {
		_setup_revolut_actions(frm);
	} else if (frm.doc.provider_name === "bank_of_cyprus") {
		_setup_boc_actions(frm);
	} else if (frm.doc.provider_name === "eurobank") {
		_setup_eurobank_actions(frm);
	}
}

// ---------------------------------------------------------------------------
// Revolut-specific actions
// ---------------------------------------------------------------------------

function _setup_revolut_actions(frm) {
	if (frm.doc.__islocal) return;

	frm.add_custom_button(
		__("Generate Certificate"),
		function () {
			frappe.call({
				method: "erpnext_bank_import.connectors.revolut.generate_certificate",
				callback: function (r) {
					if (!r.message) return;
					const { private_key, public_certificate } = r.message;
					const hostname = window.location.hostname;
					const redirect_uri =
						frm.doc.redirect_uri ||
						window.location.origin +
							"/api/method/erpnext_bank_import.connectors.revolut.oauth_callback";

					// Auto-fill private key, redirect URI, and issuer
					frm.set_value("jwt_private_key", private_key);
					if (!frm.doc.redirect_uri) {
						frm.set_value("redirect_uri", redirect_uri);
					}

					// Show dialog with editable fields
					const default_issuer = frm.doc.jwt_issuer || hostname;
					const d = new frappe.ui.Dialog({
						title: __("Certificate Generated"),
						fields: [
							{
								fieldtype: "HTML",
								options: __(
									"<p><strong>1.</strong> In Revolut Business → Settings → APIs → Add API certificate:</p>" +
										"<p><strong>2.</strong> Paste the <strong>Public Certificate</strong> below</p>" +
										"<p><strong>3.</strong> Set the <strong>OAuth Redirect URI</strong> to:</p>" +
										'<pre style="background:#f5f5f5;padding:8px;border-radius:4px;font-size:12px;">' +
										redirect_uri +
										"</pre>" +
										"<p><strong>4.</strong> Review the fields below, then click <strong>Save & Close</strong></p>"
								),
							},
							{
								fieldtype: "Code",
								fieldname: "public_cert",
								label: __("Public Certificate"),
								default: public_certificate,
								read_only: 1,
								options: "PEM",
							},
							{
								fieldtype: "Data",
								fieldname: "jwt_issuer",
								label: __("JWT Issuer (iss) — your domain without https://"),
								default: default_issuer,
								reqd: 1,
								description: __(
									"Must match the domain in your OAuth Redirect URI. For Revolut, set this without 'https://' (e.g. erp.mycompany.com)"
								),
							},
							{
								fieldtype: "Data",
								fieldname: "client_id",
								label: __("Client ID (from Revolut)"),
								reqd: 1,
							},
						],
						primary_action_label: __("Save & Close"),
						primary_action: function () {
							const values = d.get_values();
							if (!values) return;
							frm.set_value("client_id", values.client_id);
							frm.set_value("jwt_issuer", values.jwt_issuer);
							frm.set_value("redirect_uri", redirect_uri);
							frm.save();
							d.hide();
						},
					});
					d.show();
				},
			});
		},
		__("Actions")
	);

	frm.add_custom_button(
		__("Authorize Connector"),
		function () {
			// Save first if there are unsaved changes, then call the backend.
			var callback = function () {
				frappe.call({
					method: "erpnext_bank_import.connectors.revolut.start_oauth_flow",
					args: { connector_name: frm.doc.connector_name },
					callback: function (r) {
						if (r.message) {
							window.open(r.message, "_blank");
						}
					},
				});
			};
			if (frm.is_dirty()) {
				frm.save(null, null, null, callback);
			} else {
				callback();
			}
		},
		__("Actions")
	);
}

// ---------------------------------------------------------------------------
// Bank of Cyprus-specific actions
// ---------------------------------------------------------------------------

function _setup_boc_actions(frm) {
	if (frm.doc.__islocal) return;

	frm.add_custom_button(
		__("Authorize Connector"),
		function () {
			// Save first if there are unsaved changes, then call the backend.
			var callback = function () {
				frappe.call({
					method: "erpnext_bank_import.connectors.bank_of_cyprus.start_oauth_flow",
					args: { connector_name: frm.doc.connector_name },
					callback: function (r) {
						if (r.message) {
							window.open(r.message, "_blank");
						}
					},
				});
			};
			if (frm.is_dirty()) {
				frm.save(null, null, null, callback);
			} else {
				callback();
			}
		},
		__("Actions")
	);
}

// ---------------------------------------------------------------------------
// Eurobank-specific actions
// ---------------------------------------------------------------------------

function _setup_eurobank_actions(frm) {
	if (frm.doc.__islocal) return;

	frm.add_custom_button(
		__("Authorize Connector"),
		function () {
			// Save first if there are unsaved changes, then call the backend.
			var callback = function () {
				frappe.call({
					method: "erpnext_bank_import.connectors.eurobank.start_oauth_flow",
					args: { connector_name: frm.doc.connector_name },
					callback: function (r) {
						if (r.message) {
							window.open(r.message, "_blank");
						}
					},
				});
			};
			if (frm.is_dirty()) {
				frm.save(null, null, null, callback);
			} else {
				callback();
			}
		},
		__("Actions")
	);
}
