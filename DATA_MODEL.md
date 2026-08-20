# Data model

## The distinction that shapes everything

| | Example | Stored as | Can be cited? |
|---|---|---|---|
| **Knowledge** — makes a normative claim | policy, standard, requirement, procedure step | document → evidence → entity | yes, to a verified span |
| **Operational fact** — a state of the world | employee, course, completion | normalized record | no |

A policy can be quoted. "12,842 people completed this course" cannot — it is
true, but no document says it. Keeping the two apart is what guarantees every
citation resolves to text a human can read.

## Knowledge

```
sources        publisher · authority · jurisdiction · version · effective_date
  └─ documents document_type · owner · department · content hash · canonical text
       ├─ evidence     char_start · char_end · quote        (a citable span)
       └─ entities     role · locator · statement · provenance_class
             └─ relationships   type · confidence · rationale · provenance · status
```

**Entity roles** follow the obligation lifecycle: `REQUIRES`, `COMMITS`,
`OPERATIONALIZES`, `TEACHES`, `VALIDATES`, `MEASURES`, `DESCRIBES`. The role
comes from the document's declared type — the organization already classified it
when it filed it.

**Provenance class** on every relationship and finding: `SOURCE_FACT`,
`VERIS_INTERPRETATION`, `MODEL_INFERENCE`, `HUMAN_REVIEW`.

## Operational facts

```
connections   connector_id · category · status · cursor · last_sync_at
  ├─ sync_runs      kind · status · discovered · synced · failed · cursors
  ├─ people         source_id · job_role · department · facility
  ├─ courses        source_id · title · content_updated_at · required
  ├─ completions    person · course · status · completed_at · due_at
  └─ evidence_records  attestations, acknowledgements, audit results
```

`courses.content_updated_at` is what makes policy/training drift detectable, and
is the single most valuable field the LMS supplies.

## External identity

Every row that came from a connected system carries where it came from, as
columns rather than as a JSON blob:

```
source_system        which connector supplied it
source_record_type   the vendor's own name for the record
source_id            the vendor's identifier, unaltered
source_updated_at    when the vendor last changed it
imported_at          when Veris read it (Veris's clock, labelled as such)
```

`store.record_origin(table, id)` answers it for any row — the question an
auditor asks and the question a support engineer asks.

**The vendor's id is never the Veris id.** `id` is minted in Veris's own
namespace (`uuid5` over the connection and the source id). It is *derived* so a
re-sync updates a row instead of duplicating it, but derivation is not adoption:
two systems that happen to number their people identically produce two rows, a
vendor that renumbers does not silently rewrite Veris's history, and nothing
downstream can parse a Veris id back into a vendor id and act on it.

A record arriving with no identifier of its own is rejected and isolated as a
failed record. There is no correct guess: the next sync would either duplicate
it or overwrite something else.

## Schema versions

`schema_meta` records the version. Migrations are additive and applied at
startup (`MIGRATIONS` in `veris/store.py`). A hospital running Veris for a month
has connected systems and reviewed findings; an upgrade that asked them to start
again would be asking them to discard organizational knowledge.

`tests/test_migrations.py` builds a database in the previous shape by hand and
asserts that data survives, that reopening reapplies nothing, and that a
migrated database has the same shape as a fresh one — the check that catches
drift between the two routes to the same version.

## Metadata-only documents

When a policy system supplies metadata but not text, the document is recorded
with `metadata_only: true`. Veris knows the policy exists, who owns it and when
it is due for review — and says plainly that it has not read it.

This is the honest majority case at first connection, and it is enough to find a
policy with no owner or a review date that has passed.

## References tolerate arrival order

A completions export may arrive before the roster, or the people may live in a
different system entirely. Completions store both the internal id (resolved when
possible) and the external id (always). `resolve_pending_links()` runs after
every sync and repairs references whose other side has since arrived. Dropping
rows over timing would lose data for no reason.

## Findings and review

```
findings   type · severity · statement · missing · scope · confidence · provenance
   ├─ finding_evidence   (which spans support it)
   ├─ finding_entities   (what it is about)
   └─ reviews            append-only: accept · reject · assign · comment · resolve
```

Reviews are a log, not an overwrite. `status` on the target is a projection of
the latest decisive action. Who decided what, and when, is organizational
knowledge.
