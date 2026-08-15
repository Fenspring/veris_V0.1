# Decision 0001 — The Core Primitive Is the Grounded Claim

**Date:** 2026-08-15
**Status:** **Proposed** — awaiting validation by Experiment 0001
**Addresses:** CONSTITUTION §9 ("Discover the Core Primitive")

---

## The problem

CONSTITUTION §9 asks for the fundamental unit of Veris and warns that choosing
it prematurely is a risk, while noting the right choice "should make the rest of
the architecture simpler." That is the test I applied: not which primitive is
most expressive, but which one makes the most downstream problems disappear.

## Alternatives considered

### A. The Document
Every existing policy-management and GRC crosswalk tool works here. Rejected:
a 40-page policy addresses dozens of obligations, so document-to-document links
are too coarse to carry evidence. "These two documents are related" is not an
insight; it is the reason nobody trusts existing crosswalk tables.

### B. The Chunk / Passage
Rejected: a chunk is an artifact of a retrieval implementation, not a unit of
meaning. Chunk boundaries are arbitrary, they change when you change the
splitter, and a primitive that changes when you swap infrastructure is not a
primitive. Choosing this is how a product becomes a RAG app by accident.

### C. The Concept ("barcode medication administration")
Genuinely tempting, and concepts will exist inside the system. Rejected as *the*
primitive because a concept carries no evidence and no source. Two documents
sharing a concept is the weakest possible relationship and generates enormous
false-positive volume. It also cannot express the gap in the MISSION example:
the gap is not about the concept, it is about what a specific source *does* with
the concept. Concepts are the join key, not the unit.

### D. The Relationship / Edge
Rejected: an edge cannot be the primitive because an edge presupposes both of
its endpoints. Making relationships primary forces you to assert them; the
better design derives them.

### E. The Grounded Claim — **selected**

An atomic statement extracted from exactly one span of exactly one source,
carrying the functional role it plays.

```
Claim
  id
  source_id            which document
  char_start, char_end exact span within that document
  quote                verbatim text, must appear byte-for-byte in the source
  role                 REQUIRES | COMMITS | OPERATIONALIZES | TEACHES
                       | VALIDATES | MEASURES | DESCRIBES
  subject              normalized statement of what the claim is about
  actor                who is obligated, where stated (role, department, committee)
```

## Why this one

**1. It makes grounding mechanically verifiable rather than a matter of trust.**
Because `quote` must appear byte-for-byte in the source at `[char_start,
char_end)`, fabricated evidence is caught by string equality — not by prompting,
not by a judge model, not by human spot-check. CONSTITUTION §5 forbids
fabricated evidence and citations; this primitive turns that prohibition into an
assertion the pipeline can enforce on every record. Any claim failing the check
is dropped before it can reach a user. That property alone justifies the choice.

**2. It carries its role, so relationships can be derived instead of asserted.**
Per Discovery 0001, the role is what generates the intelligence. Attaching it to
the claim means a gap is a *query over a structure* rather than a judgment call
per pair.

**3. It collapses five finding types into one structure.** Gap, conflict, drift,
dependency, and orphan all become queries over claims grouped by subject and
partitioned by role. This is the CONSTITUTION §9 test being satisfied
explicitly: the architecture got simpler, not more expressive.

**4. It is sub-document, which is where the real relationships live**, without
being arbitrary like a chunk. Its boundaries are semantic and its provenance is
exact.

## The derived structure

Claims addressing the same underlying obligation form an **Obligation Thread**.
A thread is not stored as a hand-authored object; it is the result of grouping
claims by subject. Its **coverage vector** across the seven roles is the object
findings are computed from.

Threads are **anchored on REQUIRES claims** rather than free-clustered. External
requirements form a natural spine, this is how hospitals already reason about
crosswalks, and anchoring is dramatically cheaper and more stable than
unsupervised clustering. Claims with no REQUIRES anchor are not discarded — they
are the orphan candidates, handled by a second pass.

## Tradeoffs accepted

- **Extraction becomes the quality bottleneck.** If claims are extracted badly,
  everything downstream is wrong. Accepted deliberately: this concentrates risk
  in one measurable stage instead of diffusing it.
- **Lossy.** Nuance living in the surrounding paragraph can be lost. Mitigated
  by retaining the span offsets, so full context is always one lookup away —
  which DESIGN_PRINCIPLES §3 and §7 require anyway.
- **Implicit obligations are hard.** "Administers medications per hospital
  policy" is an incorporation by reference, not a claim with content. This is a
  known unsolved case and is deliberately planted as a decoy in Experiment 0001.
- **Seven roles may be wrong.** Treated as a hypothesis with a stated
  falsification condition, not as an ontology.

## What would cause this to be reconsidered

- Role assignment proves unreliable, or most claims land in DESCRIBES.
- Real documents resist atomization into single-span claims (heavy tables,
  matrices, and forms are the likely offenders — competency checklists are often
  tables, which is a real risk to this primitive).
- A useful class of finding turns out to be inexpressible over claims.
- Extraction cost per document proves uneconomic at hospital corpus sizes.
