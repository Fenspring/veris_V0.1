# Decision 0003 — Architecture Shortlist, and What We Are Deliberately Not Building

**Date:** 2026-08-15
**Status:** **Proposed** — scoped to Experiment 0001 only
**Addresses:** CONSTITUTION §3 (do not assume the architecture), §4 (simplicity)

---

## Approaches considered

### A. Vector database + RAG + chat interface
The default 2020s answer. **Rejected.** It has no representation of absence, so
it structurally cannot produce the product's headline output (Discovery 0002),
and it is the generic application MISSION explicitly excludes. Retained only as
baseline B1 — as something to beat, not to build.

### B. Knowledge graph with a formal ontology (Neo4j + OWL/SHACL, or similar)
The "correct" enterprise answer, and where this probably ends up in some form.
**Rejected for now**, on timing rather than merit: it requires committing to a
relationship taxonomy *before* we have evidence for which relationships matter,
which is precisely the over-specification the founder identified as the failure
of previous attempts. Adopting a graph database is a reversible decision that
gets easier with evidence; adopting an ontology is a decision that gets harder.

### C. Exhaustive LLM pairwise adjudication
Ask a model about every claim pair. **Rejected:** O(n²) LLM calls is uneconomic
and, worse, it adjudicates mostly-irrelevant pairs, so precision degrades as the
corpus grows. The opposite of what a knowledge layer must do.

### D. Long-context LLM over the whole corpus
**Rejected as architecture, adopted as baseline B2.** It cannot scale past
context limits, is not reproducible run to run, cannot show its work at the span
level, and cannot answer "what changed since last week" without re-reading
everything. But it may well be *accurate* at small scale, which makes it the
honest baseline rather than a strawman — see Decision 0002.

### E. Claim extraction → cheap candidate generation → LLM adjudication →
### coverage analysis over a relational store — **selected**

```
documents
   │  extract (LLM, verbatim-span constrained)
   ▼
claims ────────────────────────────────► rejected if quote ∉ source
   │  candidate generation (cheap, high recall: lexical + embedding)
   ▼
candidate groupings
   │  adjudication (LLM, expensive, high precision, per-thread not per-pair)
   ▼
obligation threads  ──►  coverage vectors  ──►  findings
                                                  │  disconfirmation pass
                                                  ▼
                                          findings that survived
```

## Why E

- **It separates recall from precision.** Cheap methods generate candidates
  (optimizing recall, where being wrong is survivable); expensive judgment
  confirms them (optimizing precision, where being wrong is expensive). Every
  rejected approach conflates these.
- **Similarity discovers, it never decides.** CONSTITUTION §6 states this almost
  verbatim: a relationship must not exist merely because a model assigned a high
  score. In E, an embedding can only nominate; a reasoned, evidence-citing
  adjudication decides. This is an architectural expression of a constitutional
  requirement.
- **Adjudication is per-thread, not per-pair**, which is what makes it affordable.
- **Every stage is independently measurable**, so when the system is wrong we can
  say which stage was wrong. This is what makes CONSTITUTION §12 achievable.

## Storage: SQLite. Not a graph database, not a vector database. Yet.

At 12 documents and a few hundred claims, graph traversal is a two-line SQL join
and vector search is a numpy dot product over a matrix that fits in L2 cache.
Introducing Neo4j or a vector store now would add operational surface and
migration cost while answering no question the experiment poses.

SQLite is a single file, needs no service, diffs and versions in git, and is
already present in the Python standard library.

**This is explicitly a deferral, not a rejection.** The trigger to revisit: when
traversal depth exceeds ~3 hops, when relationship types stabilize enough to be
worth indexing, or when the corpus exceeds roughly 10⁵ claims. Choosing SQLite
now costs one migration later; choosing Neo4j now costs a premature ontology.

## The LLM: swappable by construction

There is currently **no LLM API key in this environment** (the Anthropic API
returns 401), so model calls cannot yet run programmatically.

This is turned into a design constraint rather than a blocker: the model sits
behind a single narrow interface with two operations — `extract_claims` and
`adjudicate_thread` — and every model input and output is written to disk as a
versioned artifact. Consequences:

- The experiment can be run now with the agent acting as the model, producing
  checked-in artifacts.
- Evaluation runs over stored artifacts, so it is reproducible without API access.
- Swapping models, or comparing two, is a config change and a re-run.
- CONSTITUTION §3's instruction not to assume a specific LLM is satisfied
  structurally rather than by intention.

## No frontend in Experiment 0001

The experiment's output is a scored evaluation report, not a screen. Building UI
before knowing whether the findings are trustworthy would optimize for visual
impressiveness — explicitly excluded by the founder brief and by
CONSTITUTION §4. Interface design begins once there is something true to show,
and DESIGN_PRINCIPLES §7 (progressive disclosure: insight → relationship →
evidence → source) already describes its shape when that time comes.
