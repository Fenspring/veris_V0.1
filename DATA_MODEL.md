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
  ├─ people         external_id · job_role · department · facility
  ├─ courses        external_id · title · content_updated_at · required
  ├─ completions    person · course · status · completed_at · due_at
  └─ evidence_records  attestations, acknowledgements, audit results
```

`courses.content_updated_at` is what makes policy/training drift detectable, and
is the single most valuable field the LMS supplies.

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
