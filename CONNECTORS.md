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
    capabilities=("course_catalog", "person_roster", "completion_records"),
    reads=("Course catalogue", "Staff roster", "Assignments", "Completions"),
    supports_incremental=True,
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

## Capabilities

`capabilities` are keys into a shared vocabulary (`CAPABILITIES` in
`veris/connectors/base.py`), validated at registration. A connector cannot
invent one.

A capability is not a feature list. Each entry states what Veris can say when
something supplies it, and **what Veris cannot assess when nothing does**:

```python
Capability(
    "completion_records", "Training completions", "operational",
    enables="Who completed what, and what is overdue.",
    without_it="Veris cannot tell you whether required training was actually done.")
```

That second sentence is the one the product shows. It is why the vocabulary is
shared rather than per-connector: "Veris cannot tell you X" is only useful next
to "connect Y and it can", and both come from the same declaration.

Capabilities split in the same place the data model does. `knowledge`
capabilities become documents and can be cited; `operational` ones become
normalized rows and never can (see `DATA_MODEL.md`).

**Declared is not delivered.** `ConnectorInfo.capabilities` is what the vendor
can provide. What a particular connection has actually supplied is computed from
the rows it produced, and the difference is reported as
`degraded_capabilities` — a connection can be reachable, authenticated and
green while having quietly stopped returning completions.

The intelligence layer consults delivered capabilities, never connector ids:

```python
POLICY_TRAINING = AgentInfo(..., requires=("policy_metadata", "course_catalog"))
```

Connect a different vendor that supplies the same thing and the agent runs, with
no edit. Connect one that supplies less and the agent does not run, and says
which knowledge is missing instead of guessing.

## Health

Every connection reports the same record, whatever the vendor
(`ConnectorHealth`):

```
connection · connector · state · message · authenticated · auth_method
capabilities · degraded_capabilities
last_sync_at · next_sync_at · last_run{status, synced, failed, attempts}
consecutive_failures · records · latency_ms · error (redacted) · checked_at
```

```
GET /api/v1/connections/{id}/health
GET /api/v1/health/connections
GET /api/v1/capabilities        # what Veris can and cannot assess, and why
```

A connector supplies only `HealthStatus` — whether it can reach its source, and
how long that took. Everything else is Veris's own record of what the connection
has done, which is not something a vendor should be trusted to report.

## Availability, and never faking an integration

| State | Meaning | In the UI |
|---|---|---|
| `available` | Somebody ran it against the real system and recorded what passed | Connect button works |
| `unverified` | Implemented against a published contract; never executed live | Connectable, labelled unverified, and says so before you connect |
| `planned` | Declared so the customer can see what is coming and what it needs | Shown, explained, refuses to connect |

Showing a vendor's name next to a Connect button that silently does nothing is
worse than omitting the vendor. A planned connector always explains what it will
need and what is available today instead.

`unverified` exists because the two alternatives are both dishonest. Calling
such a connector `available` claims a working integration on the strength of
code compiling. Calling it `planned` hides working code a design partner could
verify in an afternoon. Neither tells the customer what is true.

**A connector cannot promote itself.** The registry refuses to register anything
as `available` without a verification record, and the only thing that writes one
is a run against the live system:

```bash
make verify-connector CONNECTOR=ecfr BY="your name"
```

That records the date, the person, the environment, the endpoints that answered,
and which of nine checks passed, into
`docs/connectors/verification/<id>.json`. A partial result is written too —
four of nine passing, with the failures named, is more useful to the next
engineer than a red cross and more honest than a green tick.

Two exemptions, both because there is no external system to reach: a mock
declares `status: "mock"`, and file import is verified continuously by the
contract suite.

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
- capabilities come from the shared vocabulary, and every capability states what
  its absence costs
- health is one shape for every connector, and a declared-but-undelivered
  capability is reported as degraded
- every normalized row preserves its external identity, and the vendor's id is
  never the Veris id
- a record with no source identifier is rejected rather than given a guessed key

A new connector is therefore proven against the same bar as the ones already
shipping.

## Writing one

1. Implement the six methods. Put every vendor quirk inside.
2. Declare `ConnectorInfo`: `reads` in plain language, and `capabilities` from
   the shared vocabulary. Declare only what the connector actually returns —
   an overclaimed capability makes the intelligence layer believe it can assess
   something no data supports.
3. Emit normalized records — see `DATA_MODEL.md` — each with `_type` and
   `source_id`. Optionally `_source_type` (the vendor's own name for the record)
   and `_source_updated_at` (when the vendor last changed it); Veris records
   both rather than substituting its own.
4. Yield pages with cursors so long syncs resume.
5. Raise `RateLimited(retry_after=…)` and `TransientError` so the engine can
   back off rather than fail.
6. Register it. Run the contract tests. Add `docs/connectors/<id>.md`.

## Current catalogue

**Available:** demo LMS, demo policy system, demo standards feed, file import.
**Unverified:** eCFR — real code against a public, credential-free federal
source, and the only connector that supplies regulation *text* rather than
metadata. See `docs/connectors/ecfr.md`.
**Planned:** HealthStream, Relias, Cornerstone, Workday Learning, Moodle,
Docebo, SAP SuccessFactors, PolicyStat, PowerDMS, PolicyTech, SharePoint, CMS,
The Joint Commission, DNV, ACHC, state licensure sources.

Ordering follows an integration score — market reach, API quality,
documentation, authentication simplicity, data accessibility, change detection,
webhooks, minus engineering complexity — not brand recognition.
