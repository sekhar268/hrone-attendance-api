# Decisions

1. **Indexes.** The unique employee-code index enforces the HR business key. Employee indexes support department-and-code listing, department headcount by join date, and the summary's joined-on cutoff. Attendance has a unique `(emp_code, date)` index to prevent duplicate daily records, an `(emp_code, date)` index for employee/month reads and punch-outs, and `(date, emp_code)` for the sorted attendance list and date-range analytics. I did not add a standalone status index: status has few values, and the primary list query also needs date sorting.

2. **Punch-in race.** Requests first look up the employee, then independently try inserting the attendance record. MongoDB's unique `(emp_code, date)` index accepts one insert and rejects the other with a duplicate-key error; that error becomes HTTP 409. The insert, not an earlier existence check, decides the winner.

3. **Ties.** The leaderboard uses MongoDB `$rank`, then includes every row whose rank is at or below the requested limit. A tie at the cutoff is returned in full; tied employees are ordered by code.

4. **Headcount.** The monthly department pipeline starts from employees, limits them by `joined_on` through month end, and looks up their logs. Employees with no matching logs remain in the result.

5. **100x data.** I would benchmark the indexed query plans and consider materialized daily summaries if the measured aggregation cost became too high.
