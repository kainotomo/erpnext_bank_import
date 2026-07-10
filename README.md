### Erpnext Bank Import

Import bank transactions via API from various banks into ERPNext Bank Transaction doctype

### Current Scope (V1)

- In scope for launch: Revolut transaction import into ERPNext Bank Transaction.
- Out of scope for launch: Bank of Cyprus, Eurobank Cyprus, payment initiation, automatic journal entries, and automatic reconciliation.

### Roadmap

- Milestone A (launch): shared foundation, Revolut connector, automated tests, and production hardening.
- Milestone B (deferred): Bank of Cyprus.
- Milestone C (deferred): Eurobank Cyprus.

Tracked in GitHub Issues:

- Milestone A: https://github.com/kainotomo/erpnext_bank_import/issues/1 to https://github.com/kainotomo/erpnext_bank_import/issues/11
- Milestone B: https://github.com/kainotomo/erpnext_bank_import/issues/12 to https://github.com/kainotomo/erpnext_bank_import/issues/14
- Milestone C: https://github.com/kainotomo/erpnext_bank_import/issues/15 to https://github.com/kainotomo/erpnext_bank_import/issues/17

Implementation order: A1 -> A2 -> A3 -> A4 -> A5 -> A6 -> A7 -> A8 -> A9 -> A10 -> A11

### Compatibility

| Requirement | Minimum Version |
|---|---|
| Frappe | >= 16.0.0 |
| ERPNext | >= 16.0.0 |
| Python | >= 3.12 |
| Node.js | >= 22 |
| MariaDB | >= 10.11 |
| Redis | >= 7 |

> **Note**: ERPNext must be installed on the bench before installing this app.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench --site your-site install-app erpnext_bank_import
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/erpnext_bank_import
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### Testing

```bash
# Frappe integration tests (requires a site)
bench use tmp.localhost
bench run-tests --app erpnext_bank_import

# Mock-based pytest tests (fast, no site needed)
cd apps/erpnext_bank_import
python -m pytest erpnext_bank_import/tests/ -v \
  --ignore=erpnext_bank_import/tests/test_installation.py \
  --ignore=erpnext_bank_import/tests/test_bank_connector.py \
  --ignore=erpnext_bank_import/tests/test_bank_import_run_log.py
```


### License

gpl-3.0
