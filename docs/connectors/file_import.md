# File import (CSV)

**Status:** available · **Category:** DOCUMENT · **Auth:** file

The fallback that means Veris never has to say "we don't support your vendor."
Most healthcare systems can produce a scheduled export even when their API sits
behind an account manager.

## Zero-config mapping

Columns are matched against known aliases and mapped onto the normalized schema.
Anything unrecognised is *reported*, not guessed at — a wrong automatic mapping
is worse than a short question.

```
Employee ID     → person_external_id
Course ID       → course_external_id
Completion Status → status
Date Completed  → completed_at
Cost Center     → department
Widget Score    → needs your help
```

## Composite keys

Completion exports rarely carry their own id — the row is identified by *who*
completed *what*. Without synthesising `person:course`, every row would take the
course id as its key and overwrite the previous one, silently collapsing
thousands of records into a handful. This was a real bug, caught by a contract
test.

## Arrival order

A completions export may be imported before the roster. Rows are retained with
unresolved references and repaired when the roster arrives.

## Limits

- Requires a column usable as a unique id; refuses clearly if absent.
- No incremental sync: a file import is a full snapshot.
- Encoding errors are replaced rather than fatal.
