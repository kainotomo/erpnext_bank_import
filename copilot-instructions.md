# Copilot Instructions

## Project Context

This repository is a Frappe/ERPNext app.

Primary goal for current launch scope:
- Import bank transactions from Revolut API into ERPNext Bank Transaction.

Launch scope constraints:
- In scope: shared foundation + Revolut connector + automated tests + production hardening.
- Out of scope (for now): Bank of Cyprus, Eurobank Cyprus, payment initiation, automatic journal entry creation, automatic reconciliation logic.

## Delivery Order

Follow GitHub issues in strict sequence:
- A1 -> A2 -> A3 -> A4 -> A5 -> A6 -> A7 -> A8 -> A9 -> A10 -> A11

Do not start provider implementation before shared foundations are in place.

## ERPNext-First Principles

- Reuse ERPNext and Frappe primitives before creating custom models.
- Use ERPNext Bank Transaction as the canonical imported transaction record.
- Do not build a custom reconciliation engine.
- Keep provider logic isolated from ERPNext write logic through a normalized internal schema.

## Architecture Guidelines

- Keep connector code provider-specific and composable.
- Keep shared services bank-agnostic:
  - auth/token lifecycle
  - account discovery interface
  - transaction fetch + pagination/windowing
  - normalization contract
  - idempotent import service
- Preserve external transaction identifiers for idempotency and auditability.
- Ensure re-runs of the same window are duplicate-safe.

## Security Rules

- Never hardcode credentials, secrets, or tokens.
- Store secrets using Frappe secure patterns (encrypted password fields and secure retrieval APIs).
- Avoid logging secrets or full raw credentials.
- Keep OAuth/token failures actionable but sanitized.

## Frappe/ERPNext Implementation Rules

- Prefer standard Frappe app layout and naming conventions.
- Use hooks only where required; keep hook registrations minimal and explicit.
- Use background/scheduled jobs for sync operations where appropriate.
- Keep whitelisted methods narrow in scope and permission-aware.
- Validate company/account mappings before import writes.

## Testing Requirements

Automated tests are mandatory for launch scope.

Minimum required test coverage areas:
- token lifecycle (issue, refresh, expiry handling)
- idempotency and duplicate prevention
- import orchestration for manual and scheduled flows
- Revolut normalization/mapping edge cases
- failure and retry behavior

Testing approach:
- Use deterministic fixtures for API responses.
- Prefer isolated unit tests for pure mapping/logic.
- Add integration-style tests for service orchestration.

## Code Quality

- Keep modules small and single-purpose.
- Add short, meaningful docstrings/comments where logic is not obvious.
- Avoid introducing new dependencies unless justified.
- Preserve backward compatibility of existing behavior unless issue scope requires change.

## Pre-Commit & Linting Discipline

This project uses **tab indentation** (not spaces). All Python files must use tabs to match the project convention.

Before committing, **always run both of these**:

```bash
ruff check --fix .
ruff format .
```

This ensures:
- Ruff linter rules pass (import sorting, `__all__` ordering, unused variable detection, etc.).
- Ruff formatter normalises all files to tab indentation.

The CI's **Frappe Linter** job runs `pre-commit run --all-files` followed by **Semgrep rules**. Pre-commit checks **all files** in the repo, not just changed ones. If pre-existing files fail formatting, they must be fixed too — otherwise the CI merge commit will fail.

**Common pitfalls to avoid:**
- Creating new files with space indentation — they will fail the formatter hook in CI.
- Leaving unused unpacked variables (e.g. `txns, next_token = ...` where `next_token` is unused). Use `_` or `[0]` subscript instead.
- Forgetting to add test dependencies to `[tool.bench.dev-dependencies]` in `pyproject.toml`. Frappe's CI test runner does not have `pytest` installed by default.
- Omitting `_()` / `frappe._()` translate wrappers on user-facing strings in `frappe.throw()`, `frappe.msgprint()`, and similar calls — the Frappe Semgrep rules enforce this and will fail CI.  Only non-user-facing messages (e.g. debug logs) can skip it.
- Not running Semgrep locally before pushing.  Pre-commit does **not** include Semgrep — it's a separate CI step.  To catch Semgrep issues early:

  ```bash
  pip install semgrep
  git clone --depth 1 https://github.com/frappe/semgrep-rules.git
  semgrep ci --config ./frappe-semgrep-rules/rules
  ```

## Change Management

- Tie implementation to issue IDs in commit and PR descriptions.
- Keep each PR focused on one issue or one tightly-coupled slice.
- Update README when scope, setup, or operator workflow changes.
