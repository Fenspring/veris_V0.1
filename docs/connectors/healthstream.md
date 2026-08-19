# HealthStream

**Status:** planned · **Category:** LMS · **Auth:** OAuth or API key
**Requires vendor enablement:** yes

## Before writing this connector

- Confirm which API tier the customer's contract includes; access is commonly
  enabled per-customer by an account manager rather than self-service.
- Establish whether a sandbox is available. If not, the contract tests plus the
  demo LMS are the development target.

## What to determine

| Question | Why it matters |
|---|---|
| Pagination style | Cursor vs offset changes the checkpoint shape |
| Change detection | A `modified_since` filter is the difference between incremental sync and re-downloading everything |
| Rate limits | Must map onto `RateLimited(retry_after=…)` |
| Course revision date | `content_updated_at` is what makes policy/training drift detectable — without it the drift agent cannot run |
| Role and department fields | Needed to relate training to the staff a requirement applies to |

## Until then

The customer can export from HealthStream and use file import today, then
connect the live system later without losing anything: records keyed by the same
external ids merge rather than duplicate.
