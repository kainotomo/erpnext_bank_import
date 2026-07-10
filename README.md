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

Automated tests are required for launch scope:

- Shared test harness and fixtures
- OAuth token lifecycle tests
- Import idempotency tests
- Revolut connector tests

Target workflow: foundation tests first, then provider-specific tests.

#### Running tests via Frappe bench (requires a site)

```bash
# First, set the default site
bench use tmp.localhost

# Run all tests for this app
bench run-tests --app erpnext_bank_import

# Run tests for a specific module
bench run-tests --app erpnext_bank_import --module test_transaction_schema
bench run-tests --app erpnext_bank_import --module test_installation
```

> **Note**: Frappe's test runner only discovers `unittest.TestCase` subclasses.
> The pytest-style tests must be run separately (see below).

#### Running pytest-based tests (mock, no site needed)

The `test_connector_interface.py` and `test_oauth_service.py` files use
`pytest` with mocked Frappe dependencies.  Run them directly:

```bash
cd apps/erpnext_bank_import

# All mock-based tests
python -m pytest erpnext_bank_import/tests/ \
  --ignore=erpnext_bank_import/tests/test_installation.py -v

# OAuth service tests only
python -m pytest erpnext_bank_import/tests/test_oauth_service.py -v

# Connector interface tests only
python -m pytest erpnext_bank_import/tests/test_connector_interface.py -v
```


### License

gpl-3.0
