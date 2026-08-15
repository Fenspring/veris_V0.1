# Veris Documentation

`decisions/` — architectural and product decisions: problem, alternatives,
evidence, decision, tradeoffs, and what would cause reconsideration
(CONSTITUTION §14).

`discoveries/` — product and technical insights discovered while building.

Documents are numbered in the order they were written. A decision marked
**Proposed** has not yet been validated by evidence.

## Current state — discovery phase, nothing built yet

| Doc | Subject | Status |
|---|---|---|
| [discoveries/0001](discoveries/0001-the-obligation-lifecycle.md) | Obligations have a lifecycle; failure happens *between* artifacts, not inside them | Proposed insight |
| [discoveries/0002](discoveries/0002-absence-is-the-product.md) | Veris's headline output is a statement about what is *missing*, which inverts the engineering problem | Proposed insight |
| [decisions/0001](decisions/0001-core-primitive.md) | The core primitive is the grounded claim | Proposed |
| [decisions/0002](decisions/0002-experiment-0001-the-alignment-probe.md) | Experiment 0001 — the comparative test of the central thesis | Proposed, awaiting go-ahead |
| [decisions/0003](decisions/0003-architecture-shortlist-and-deferrals.md) | Architecture shortlist; SQLite, no graph DB, no vector DB, no UI yet | Proposed |

Read discoveries 0001 and 0002 first. The decisions follow from them.
