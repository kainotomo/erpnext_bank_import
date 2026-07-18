# Bank of Cyprus — UAT Checklist

> **Issue:** [#14 — BoC Automated Tests And UAT](https://github.com/kainotomo/erpnext_bank_import/issues/14)
>
> **Environment:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ (sandbox / staging / production)
>
> **Tester:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ **Date:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

---

## 1. Connector Setup

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 1.1 | Create BoC connector | 1. Go to **Bank Connector** list<br>2. Create new, select `bank_of_cyprus` provider<br>3. Fill in sandbox `client_id` and `client_secret`<br>4. Set API base URL to sandbox endpoint<br>5. Save | Connector is saved and enabled | | |
| 1.2 | Validate connector config | 1. Edit the connector<br>2. Clear `client_id` and save | Save fails with validation error about missing `client_id` | | |
| 1.3 | Validate provider config (empty) | 1. Edit the connector<br>2. Clear `api_base_url` and save | Save fails with validation error about missing `api_base_url` | | |
| 1.4 | Disable connector | 1. Toggle **Enabled** off<br>2. Save | Connector is disabled | | |

---

## 2. OAuth Authorisation Flow

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 2.1 | Initiate OAuth flow | 1. Open the BoC connector<br>2. Click **Authorise** (or navigate to the auth URL) | Browser redirects to BoC 1Bank login page | | |
| 2.2 | User logs in to 1Bank | 1. Enter sandbox credentials<br>2. Click **Login** | Account selection screen is shown | | |
| 2.3 | Select accounts | 1. Select 2–3 accounts to grant access to<br>2. Click **Confirm/Authorise** | Browser redirects back to the callback URL with a `code` parameter | | |
| 2.4 | Callback completes successfully | 1. Observe the redirect back to ERPNext | Success message: *"Bank of Cyprus authorisation successful for connector '…'"* | | |
| 2.5 | Verify subscription activated | 1. Check the connector's token record<br>2. Look at `provider_metadata` | `subscription_id` is populated, `subscription_expires_at` is set | | |
| 2.6 | User declines consent | 1. On the BoC consent screen, click **Decline/Deny** | Error is thrown, user sees a message about the authorisation being declined | | |
| 2.7 | No enabled BoC connectors | 1. Disable all BoC connectors<br>2. Manually navigate to the callback URL with a valid code | Error: *"Could not find a pending Bank of Cyprus authorisation"* | | |

---

## 3. Account Discovery

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 3.1 | Discover accounts | 1. After successful auth, go to the connector<br>2. Click **Get Accounts** (or refresh) | All authorised accounts appear with correct IBAN, currency, and account name | | |
| 3.2 | Account details match | 1. Compare displayed account info with what was selected in BoC 1Bank | Account IDs, IBANs, and currencies match | | |

---

## 4. Transaction Import

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 4.1 | Import transactions | 1. Go to the connector<br>2. Set a date range (last 30 days)<br>3. Click **Import** | Import completes with status **Success**<br>Bank transactions are created | | |
| 4.2 | Verify imported data | 1. Open **Bank Transaction** list<br>2. Filter by the imported bank account | Transactions show correct amounts, dates, descriptions, and counterparty info | | |
| 4.3 | DEBIT sign check | 1. Find a DEBIT transaction in the list | Amount appears as **withdrawal** (not deposit) | | |
| 4.4 | CREDIT sign check | 1. Find a CREDIT transaction in the list | Amount appears as **deposit** (not withdrawal) | | |
| 4.5 | Date format | 1. Check the transaction date in **Bank Transaction** | Date is in ISO format (YYYY-MM-DD), not DD/MM/YYYY | | |
| 4.6 | Currency check | 1. Verify currency field on imported transactions | Currency matches the account's currency (typically EUR) | | |
| 4.7 | Counterparty data (production) | 1. If testing against production data, check counterparty fields | Counterparty name, IBAN, and reference number are populated | | |

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
| 6.2 | Incremental re-import | 1. Import again (scheduled or manual trigger)<br>2. Check the API call parameters | `date_from` equals the previous `last_synced_at` value (not a full window) | | |

---

## 7. Error Handling

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 7.1 | Invalid credentials | 1. Remove or corrupt the `client_secret` in the connector config<br>2. Attempt to import | Import fails with an authentication-related error | | |
| 7.2 | Expired subscription | 1. (Simulate) Set `subscription_expires_at` to a past date in token metadata<br>2. Attempt to import | Import fails with message about expired subscription; user directed to re-authorise | | |
| 7.3 | API unavailable | 1. Set API base URL to an unreachable endpoint<br>2. Attempt to import | Import fails gracefully with a network/API error (no crash, no traceback to user) | | |
| 7.4 | Connector disabled | 1. Disable the connector<br>2. Attempt to import | Import does not run for the disabled connector | | |
| 7.5 | Empty account | 1. Import for an account with **no** transactions in the date range | Import succeeds with **0 created** | | |

---

## 8. Scheduled Import

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 8.1 | Hourly scheduled job runs | 1. Wait for the hourly scheduler trigger (or trigger manually via bench)<br>2. Check **Bank Import Run Log** | Scheduled run log is created with status **Success** | | |
| 8.2 | Multi-connector scheduling | 1. Enable two BoC connectors<br>2. Trigger the scheduled import | Both connectors are processed; each produces its own run log | | |
| 8.3 | Failure isolation in schedule | 1. Make one connector fail (e.g., invalid token)<br>2. Trigger the scheduled import | The failing connector logs an error; the other connector still runs successfully | | |

---

## 9. Pagination

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 9.1 | Large import (multi-page) | 1. Set a wide date range with many transactions<br>2. Import | All transactions across all pages are imported (verify count against API) | | |
| 9.2 | Same-date wall | 1. If many transactions share a single date, verify pagination stops safely | Import completes without an infinite loop; any overlap deduplicated | | |

---

## 10. Health & Monitoring

| # | Scenario | Steps | Expected Result | Pass/Fail | Notes |
|---|----------|-------|-----------------|-----------|-------|
| 10.1 | Health check endpoint | 1. Call `frappe.call('erpnext_bank_import.services.health.check')` from browser console or API | Returns a JSON with connector statuses and recent error count | | |
| 10.2 | Health reflects errors | 1. Cause an import to fail<br>2. Call the health check endpoint | `recent_global_error_count_24h` is > 0, status is `"degraded"` | | |
| 10.3 | Diagnostic message | 1. Cause an auth error on import<br>2. Check the error message | Diagnostic message includes a clear suggested action | | |

---

## Sign-off

| Item | Status | Comments |
|------|--------|----------|
| All Section 1–10 scenarios executed | ☐ Yes ☐ No | |
| All critical (P0) scenarios pass | ☐ Yes ☐ No | |
| All high (P1) scenarios pass | ☐ Yes ☐ No | |
| Known issues documented | ☐ Yes ☐ No | See notes below |

**Sign-off by:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ **Date:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**Known Issues / Notes:**
- \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
