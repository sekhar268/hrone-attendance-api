# Review of starter `app/main.py`

| # | Where | What is wrong | How to notice | Fix |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses naive local time and ignores IST; overnight punch-ins use the wrong shift date. | Change host timezone or punch at 05:00 on an overnight shift. | Convert instants to UTC and calculate against the IST attendance date. |
| 2 | `compute_late_minutes` | Grace check and whole-minute floor need strict elapsed-time comparison after second truncation. | 09:40:00 and 09:40:01 for a 09:30 shift. | Compare elapsed seconds to 600, then floor elapsed minutes. |
| 3 | `compute_work_hours` | Python `round` uses ties-to-even and floating point. | Work duration at a half-cent rounding boundary. | Use Decimal with `ROUND_HALF_UP`. |
| 4 | `compute_overtime` | Doesn't handle overnight end dates and fails to enforce the 30-minute minimum. | Overnight punch-out or 29:59 / 30:00 after shift end. | Calculate next-day shift end when applicable and apply the threshold. |
| 5 | `/health` | Returns healthy without contacting MongoDB. | Stop MongoDB and request `/health`. | Ping MongoDB and return 503 on failure. |
| 6 | `create_employee` | Check-then-insert is race-prone; no database uniqueness guarantee. | Send concurrent creates for the same code. | Unique index and map duplicate-key error to 409. |
| 7 | `create_employee` | `created_at` is naive, and shift/date/email/code validation is absent. | Submit malformed fields or inspect timestamp offset. | Validate request fields and store UTC-aware server time. |
| 8 | `list_employees` | Skip is `page * page_size`; total ignores department; results are unsorted. | Request page 1 with a filter. | Use `(page-1)*page_size`, filtered count, stable sort and bounded pagination. |
| 9 | `punch_in` | Doesn't return 404 for unknown employee and dereferences `None`. | Punch in with an unknown code. | Check employee existence first. |
| 10 | `punch_in` | Uses host-local time, milliseconds incorrectly/local-naive conversion, and UTC date instead of IST attendance date. | Compare a near-midnight UTC instant to its IST date. | Validate epoch ms, truncate seconds, compute IST date and store UTC BSON datetime. |
| 11 | `punch_in` | Doesn't validate status/timestamp and treats timestamp zero as omitted. | Pass invalid status or epoch seconds. | Strict request models and explicit `is None` handling. |
| 12 | `punch_in` | Check-then-insert allows duplicate same-day records under concurrency; exposes Mongo `_id` as `id`. | Fire parallel same-day requests; inspect response keys. | Unique `(emp_code,date)` index and 409 mapping; never expose an id. |
| 13 | `list_attendance` | Loads the full collection into Python, ignores input date order/validation, and sorting is unstable. | Run against a large collection or request reversed dates. | Indexed filtered count and cursor sort/skip/limit with date and employee tie-break. |
| 14 | `list_attendance` | Leaks `_id` and returns BSON datetimes rather than epoch milliseconds; missing legacy fields not normalized. | Inspect response with sample legacy record. | Serialize explicitly, omit `_id`, convert instants and supply defaults. |
| 15 | All list endpoints | No validation for page bounds or page size cap. | Request page 0 or size 101. | Enforce page >= 1 and size 1..100. |

## Reviewed and kept

The starter's use of a separate employee business key (`emp_code`) is consistent with the data model. The `history` field belongs only to manual corrections; punch-in and punch-out should not add audit entries.
