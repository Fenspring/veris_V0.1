# Connectors

A connector is the only place vendor-specific code is allowed to live.

## The interface

```python
authenticate(credentials) -> AuthResult
test_connection()         -> ConnectionStatus
discover()                -> DiscoveryResult     # counts, before syncing anything
sync(since, cursor)       -> Iterable[SyncPage]  # pages, each with a checkpoint
health_check()            -> HealthStatus
disconnect()              -> None
```

**There is no write method.** Veris reads, normalizes, relates and recommends.
The registry refuses to register a connector declaring write capabilities, so
this is enforced rather than intended.

## Registry metadata drives the UI

```python
ConnectorInfo(
    id="healthstream", name="HealthStream", category="LMS",
    auth_methods=("oauth", "api_key"),
    capabilities=("courses", "users", "assignments", "completions"),
    reads=("Course catalogue", "Staff roster", "Assignments", "Completions"),
    availability="planned",              # available | planned
    requires_vendor_enablement=True,
    setup_note="Your vendor must enable API access before this can be connected.",
)
```

The Connection Center renders from this. Adding an integration adds no dashboard
code — which is the difference between a plugin architecture and a switch
statement.

`reads` is shown to the customer before they connect anything. It is written in
their language, not the vendor's API vocabulary.

## Availability, and never faking an integration

| State | Meaning | In the UI |
|---|---|---|
| `available` | Implemented and usable now | Connect button works |
| `planned` | Declared so the customer can see what is coming and what it needs | Shown, explained, refuses to connect |

Showing a vendor's name next to a Connect button that silently does nothing is
worse than omitting the vendor. A planned connector always explains what it will
need and what is available today instead.

## Integration mechanisms

Not every healthcare vendor offers an API, and a framework that assumes one
cannot integrate most of the market. Supported auth and transport:
`oauth`, `api_key`, `basic`, `sftp`, `file_import`, `manual_upload`, `none`.

**File import is the one that matters most.** Most systems can produce a
scheduled export even when their API sits behind an account manager. It ships
today, maps columns automatically, and asks about only what it could not place.
Veris should never have to say "we don't support your vendor" — only "let's
connect it another way."

## Contract tests

Every connector, mock or real, must pass `tests/test_connectors.py`:

- implements the protocol; metadata is valid and renderable
- read-only: no write capability declared, no write method exposed
- `test_connection` reports `AUTHENTICATION_REQUIRED` before authentication
- discovery reports counts before any sync
- sync yields typed records with external ids, and terminates
- health and disconnect behave
- re-sync is idempotent; a checkpoint is recorded after each page
- transient failures retry and succeed; rate limits obey the vendor's hint
- one bad record does not abandon the run
- credentials never reach the database; errors are redacted
- references tolerate arrival order

A new connector is therefore proven against the same bar as the ones already
shipping.

## Writing one

1. Implement the six methods. Put every vendor quirk inside.
2. Declare `ConnectorInfo`, including `reads` in plain language.
3. Emit normalized records — see `DATA_MODEL.md` — each with `_type` and
   `external_id`.
4. Yield pages with cursors so long syncs resume.
5. Raise `RateLimited(retry_after=…)` and `TransientError` so the engine can
   back off rather than fail.
6. Register it. Run the contract tests. Add `docs/connectors/<id>.md`.

## Current catalogue

**Available:** demo LMS, demo policy system, demo standards feed, file import.
**Planned:** HealthStream, Relias, Cornerstone, Workday Learning, Moodle,
Docebo, SAP SuccessFactors, PolicyStat, PowerDMS, PolicyTech, SharePoint, CMS,
The Joint Commission, DNV, ACHC, state licensure sources.

Ordering follows an integration score — market reach, API quality,
documentation, authentication simplicity, data accessibility, change detection,
webhooks, minus engineering complexity — not brand recognition.
