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

## Change Management

- Tie implementation to issue IDs in commit and PR descriptions.
- Keep each PR focused on one issue or one tightly-coupled slice.
- Update README when scope, setup, or operator workflow changes.
