# Eurobank (Hellenic Bank) — UAT Checklist

> **Issue:** [#17 — Eurobank Automated Tests And UAT](https://github.com/kainotomo/erpnext_bank_import/issues/17)
>
> **Environment:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ (sandbox / staging / production)
>
> **Tester:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ **Date:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

---

## 1. Connector Setup

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 1.1 | Create Eurobank connector | 1. Go to **Bank Connector** list<br>2. Create new, select `eurobank` provider<br>3. Verify sandbox mode auto-checked<br>4. Verify API URLs auto-filled<br>5. Fill sandbox `client_id` and `client_secret`<br>6. Save | Connector is saved and enabled. API Base URL, Authorize URL, Token URL, Scopes, and Redirect URI are pre-filled | | |
| 1.2 | Validate connector config | 1. Edit the connector<br>2. Clear `client_id` and save | Save fails with validation error about missing `client_id` | | |
| 1.3 | Validate empty api_base_url | 1. Edit the connector<br>2. Clear `api_base_url` and save | Save fails with validation error about missing `api_base_url` | | |
| 1.4 | Disable connector | 1. Toggle **Enabled** off<br>2. Save | Connector is disabled | | |
| 1.5 | Toggle sandbox mode | 1. Uncheck **Sandbox Mode**<br>2. Observe URL fields | URLs switch from `sandbox-apis` / `sandbox-oauth` to `apisprod` / `oauthprod` | | |
| 1.6 | Redirect URI auto-fill | 1. Create new Eurobank connector<br>2. Check **Redirect URI** field | Pre-filled with current site origin + `/api/method/erpnext_bank_import.connectors.eurobank.oauth_callback` | | |

---

## 2. OAuth Authorisation Flow

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 2.1 | Initiate OAuth flow | 1. Open the Eurobank connector<br>2. Click **Authorize Connector** | Browser opens new tab redirecting to Eurobank consent page at `sandbox-oauth.hellenicbank.com` | | |
| 2.2 | User logs in | 1. On the Eurobank login page, enter sandbox user `customer01` / `11223344`<br>2. Click **Login** | Account selection screen is shown | | |
| 2.3 | Select accounts | 1. Select 1–2 accounts to grant access to<br>2. Click **Confirm / Authorise** | Browser redirects back to the ERPNext callback URL with `code` and `state` parameters | | |
| 2.4 | Callback completes | 1. Observe the redirect back to ERPNext | Success message: *"OAuth2 authorization successful for connector '…'"* | | |
| 2.5 | Verify token stored | 1. Check **Bank Connector Token** list<br>2. Find the record for eurobank | Record exists with `provider_name` = `eurobank` and `bank_account` = connector name. Access token and refresh token are populated | | |
| 2.6 | User declines consent | 1. On the Eurobank consent screen, click **Decline / Cancel** | Error response is handled gracefully; user sees an appropriate error message | | |
| 2.7 | Missing state parameter | 1. Manually navigate to callback URL with `code` but no `state` | Error: *"Missing state parameter"* | | |
| 2.8 | Expired state | 1. Call callback with a valid code but an unknown/expired `state` | Error: *"OAuth state expired or invalid"* | | |

---

## 3. Account Discovery

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 3.1 | Discover accounts | 1. After successful auth, go to the connector<br>2. Click **Fetch Accounts** | All authorised accounts appear with correct IBAN, currency, and account name | | |
| 3.2 | Account details match | 1. Compare displayed account info with what was selected during consent | Account IDs, IBANs, and currencies match | | |
| 3.3 | Inactive accounts filtered | 1. If any closed/blocked accounts exist in the response | They are NOT added to the account mappings table | | |
| 3.4 | Empty account list | 1. If the sandbox user has no accounts or consent was not granted | Empty result shown; no mappings created | | |

---

## 4. Transaction Import

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 4.1 | Import transactions | 1. Go to the connector<br>2. Set date range (last 90 days)<br>3. Click **Import Transactions** | Import completes with status **Success** or **Partial**<br>Bank transactions are created | | |
| 4.2 | Verify imported data | 1. Open **Bank Transaction** list<br>2. Filter by the imported bank account | Transactions show correct amounts, dates, descriptions, and counterparty info | | |
| 4.3 | DEBIT sign check | 1. Find a DEBIT transaction in the list | Amount appears as **withdrawal** (negative) | | |
| 4.4 | CREDIT sign check | 1. Find a CREDIT transaction in the list | Amount appears as **deposit** (positive) | | |
| 4.5 | Date format | 1. Check the transaction date in **Bank Transaction** | Date is in ISO format (YYYY-MM-DD), not DD/MM/YYYY as returned by the API | | |
| 4.6 | Currency check | 1. Verify currency field on imported transactions | Currency matches the account's currency (typically EUR, USD, GBP) | | |
| 4.7 | Counterparty data | 1. Check counterparty fields on imported transactions | Counterparty name and account number are populated where available | | |

---

## 5. Idempotency & Deduplication

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 5.1 | Re-import same range | 1. Import transactions (e.g., last 7 days)<br>2. Import the **same** date range again | Second import reports **0 created**, all skipped | | |
| 5.2 | Partial overlap | 1. Import (e.g., last 30 days)<br>2. Import (e.g., last 7 days — overlapping range) | Only new (non-overlapping) transactions are created | | |

---

## 6. Incremental Sync

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 6.1 | Check last_synced_at cursor | 1. After a successful import<br>2. Check the account mapping record | `last_synced_at` is updated to the current date | | |
| 6.2 | Incremental re-import | 1. Import again (scheduled or manual trigger)<br>2. Check the API call parameters (in logs) | `date_from` equals the previous `last_synced_at` value (not a full window) | | |

---

## 7. Error Handling

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 7.1 | Invalid client_secret | 1. Replace `client_secret` with an invalid value<br>2. Re-authorize (OAuth flow) | OAuth callback fails with authentication error | | |
| 7.2 | Expired / revoked token | 1. Delete the connector's token record from **Bank Connector Token**<br>2. Attempt to import | Import fails with message about missing/expired token; user directed to re-authorise | | |
| 7.3 | API unavailable | 1. Set API base URL to an unreachable endpoint<br>2. Attempt to import | Import fails gracefully with a network/API error (no crash, no traceback to user) | | |
| 7.4 | Connector disabled | 1. Disable the connector<br>2. Attempt to import | Import does not run for the disabled connector | | |
| 7.5 | Empty account (no transactions) | 1. Import for an account with no transactions in the date range | Import succeeds with **0 created** | | |
| 7.6 | Authentication before import | 1. Complete OAuth flow successfully<br>2. Wait 30+ minutes for token to expire<br>3. Click **Import Transactions** | Auto-refresh kicks in; import runs successfully (no need to re-authorise) | | |

---

## 8. Scheduled Import

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 8.1 | Hourly scheduled job runs | 1. Wait for the hourly scheduler trigger (or trigger manually via `bench`)<br>2. Check **Bank Import Run Log** | Scheduled run log is created with status **Success** or **Partial** | | |
| 8.2 | Multi-connector scheduling | 1. Enable two Eurobank connectors (or Eurobank + another provider)<br>2. Trigger the scheduled import | All connectors are processed; each produces its own run log | | |
| 8.3 | Failure isolation in schedule | 1. Make one connector fail (e.g., invalid token)<br>2. Trigger the scheduled import | The failing connector logs an error; the other connector still runs successfully | | |

---

## 9. Pagination

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 9.1 | Large import (multi-page) | 1. Set a wide date range with many transactions<br>2. Import | All transactions across all pages are imported (verify count against the bank API) | | |
| 9.2 | Same-date wall | 1. If many transactions share a single date, verify pagination stops safely | Import completes without an infinite loop; any overlap deduplicated | | |
| 9.3 | Page number token | 1. Check the next page cursor after a partial page | `next_token` is `None` when `nextPage` is `null` and page is not full | | |

---

## 10. B2B Scopes & Permissions

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 10.1 | B2B scope authorisation | 1. During consent, verify that B2B scopes are requested | Scopes include `v2.b2b.get.accounts`, `v2.b2b.get.account.details`, `v2.b2b.get.account.transactions` | | |
| 10.2 | Insufficient scope | 1. If the token does not include `v2.b2b.get.accounts` scope<br>2. Attempt to fetch accounts | API returns FORBIDDEN error; handled gracefully | | |

---

## 11. Health & Monitoring

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 11.1 | Health check endpoint | 1. Call health endpoint from browser console | Returns JSON with connector statuses and recent error count | | |
| 11.2 | Health reflects errors | 1. Cause an import to fail<br>2. Call the health check endpoint | Recent error count > 0, status is `"degraded"` | | |
| 11.3 | Diagnostic message | 1. Cause an auth error on import<br>2. Check the error message in the UI | Diagnostic message includes a clear suggested action | | |

---

## 12. Automated Test Coverage

| # | Test Area | Test Class | Test Count | Notes |
|---|-----------|------------|------------|-------|
| 12.1 | Configuration validation | `TestEurobankConfigValidation` | 9 tests | Missing fields, sandbox/production, URL validation |
| 12.2 | Config edge cases | `TestEurobankConfigEdgeCases` | 2 tests | Empty URL, production mode |
| 12.3 | Account discovery | `TestEurobankAccountDiscovery` | 6 tests | Active/filtered accounts, field mapping, empty list, metadata |
| 12.4 | Account discovery edge cases | `TestEurobankAccountDiscoveryEdgeCases` | 4 tests | Malformed response, missing keys, network error |
| 12.5 | API error handling | `TestEurobankErrorHandling` | 6 tests | 401/429/500/403/network errors, retry-after header |
| 12.6 | Rate limiting | `TestEurobankRateLimiting` | 1 test | RPS throttling |
| 12.7 | Auth lifecycle | `TestEurobankAuthLifecycle` | 3 tests | authenticate, is_authenticated, refresh_token |
| 12.8 | Token access | `TestEurobankOAuth` | 3 tests | get_access_token, no-account guard, endpoint verification |
| 12.9 | OAuth flow functions | `TestEurobankOAuthFlowFunctions` | 6 tests | start_oauth_flow, callback, missing params, expired state |
| 12.10 | Transaction fetch | `TestEurobankTransactionFetch` | 9 tests | Count, pending filter, empty, pagination, debit/credit sign, params |
| 12.11 | Transaction fetch edge cases | `TestEurobankTransactionFetchEdgeCases` | 5 tests | Malformed response, rejected filter, full page inference, auth error |
| 12.12 | Transaction normalization | `TestEurobankTransactionNormalisation` | 8 tests | Debit/credit, missing fields, counterparty, reference number |
| 12.13 | Normalization edge cases | `TestEurobankNormalizationEdgeCases` | 5 tests | Zero amount, empty description, otherId fallback, invalid date |
| 12.14 | Date format | `TestEurobankDateFormat` | 3 tests | Start/end of day, no-dash input |
| | **Total** | **14 test classes** | **70 tests** | |

---

## Sign-off

| Item | Status | Comments |
|------|--------|----------|
| All Section 1–12 scenarios executed | ☐ Yes ☐ No | |
| All critical (P0) scenarios pass | ☐ Yes ☐ No | |
| All high (P1) scenarios pass | ☐ Yes ☐ No | |
| Known issues documented | ☐ Yes ☐ No | See notes below |
| Automated test suite passes | ☐ Yes ☐ No | `pytest erpnext_bank_import/tests/` |

**Sign-off by:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ **Date:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**Known Issues / Notes:**
- \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
