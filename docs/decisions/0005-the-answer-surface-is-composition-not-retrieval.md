# Decision 0005 — The Answer Surface Is Composition Over Precomputed Connections

**Date:** 2026-08-16
**Status:** Accepted — implemented in `veris/brief.py`
**Driver:** Founder requirement — a clinician asks a question in a chat-like
surface and gets policy, linked education, and regulatory intelligence in one
answer.

---

## The risk this decision exists to avoid

MISSION forbids building a chatbot or a generic RAG application, and the founder
has asked for a question-and-answer surface. These are not in conflict, but the
distance between them is one design decision wide, and getting it wrong
produces exactly the product the mission excludes.

| | Generic RAG assistant | Veris brief |
|---|---|---|
| What a question does | retrieves passages | resolves a subject |
| What the answer is | a summary of retrieved text | a composition over connections established beforehand |
| Structure | relevance rank | what each source *does* — commits, operationalizes, requires |
| Two clinicians, same question, different words | different answers | same underlying findings |
| What it cannot say | anything not in a document | — |
| What it does with what it lacks | silently omits it | reports it as a finding |

The last two rows are where the value is. A RAG answer can never contain a
regulatory gap, because no document contains one. And a RAG answer that finds no
education silently returns no education, which is how a clinician concludes the
training simply does not exist.

## The decision

**The chat surface is a way in, not the place where intelligence happens.**

Connections and coverage findings are computed offline over the whole corpus.
A question retrieves and composes them; it does not derive them. Consequences:

- **Consistency.** The regulatory finding a clinician sees is the same object
  the compliance officer sees in the coverage report. One computation, two
  audiences, no divergence.
- **Auditability.** The finding was produced by a run that can be replayed, not
  by a conversational turn that cannot.
- **Latency and cost.** Question time is retrieval and one summarisation, not
  corpus analysis — which matters when the deployment target is a local model
  (Decision 0004).

## Structure: sections by role, including the empty ones

The brief is organised by what each source does, not by relevance:

1. **What our policy requires** — COMMITS
2. **How it is performed** — OPERATIONALIZES
3. **What regulators and accreditors require** — REQUIRES
4. **Education** — TEACHES
5. **Competency validation** — VALIDATES
6. **Regulatory intelligence** — precomputed findings

This forced the COMMITS/OPERATIONALIZES split that Decision 0001 deliberately
deferred. A clinician asking about blood products needs "what is the rule" and
"what do I do right now" as separate answers, and the corpus supplies the
distinction itself: procedures label themselves `HOSPITAL CLINICAL POLICY —
DETAILED / PROCEDURE`. The split was built when a finding needed it, which is
the intended discipline.

**Empty sections are rendered, not hidden.** For the blood-products question the
corpus has no education and no competency material, so the brief says so
explicitly, and says it as a statement about what has been *connected to Veris*
rather than about what the organization possesses. This is Discovery 0002's
scoping requirement reaching the clinician-facing surface: an absence is only
ever reported against a named scope.

## Grounding

The model writes prose only over extracts it is handed and must cite extract ids.
Citations are resolved against the claim store; a citation to an extract that was
never supplied is stripped and flagged. This is the same principle as span
verification (Decision 0001) applied one layer up: the model is never asked to
reproduce a quotation, so it cannot fabricate one.

## Query expansion is required here, not optional

Clinician questions are short and carry little vocabulary. Retrieving on
"What is our policy on giving blood products?" directly put *Pain Assessment*
provisions in the top four. Expanding the question into the vocabulary a policy
and a standard would use fixed it — all six extracts on topic.

This is Discovery 0004's lesson recurring: query terms have to come from
somewhere other than the text you are matching from. It is now load-bearing in
two places, which suggests it is a property of the problem rather than of either
implementation.

## What this surfaced on the first real question

Asked "What is our policy on giving blood products?", the brief returned the
four policy provisions, the four transfusion-reaction procedure steps, the
standards that touch the topic — and:

> **LD.13.01.01 EP 7 — no coverage found.** Records of the source and
> disposition of all blood units must be retained for at least 10 years in a
> manner permitting prompt retrieval, with a funded plan to transfer them if the
> hospital ceases operation. Nothing in the connected policies establishes this.

A clinician asked a routine question and the organization learned about a
regulatory gap. That fact appears in no document in the corpus; it exists only
in the relation between the standard and the policy library. It is the clearest
demonstration so far of CONSTITUTION §16.

## Known weaknesses

- **Retrieval noise in the standards section.** `EM.11.01.01 EP 3` (hazard
  vulnerability analysis) appeared for a blood-products question. Harmless in a
  collapsed section, but it will need a relevance floor.
- **No topic identity.** Subjects are resolved per question rather than stored,
  which was chosen to avoid a premature ontology. The cost is that Veris cannot
  yet say "show me everything connected to transfusion" as a durable object, and
  cannot track how that topic changes over time. Revisit when change detection
  is built — that is the feature that will need stable topic identity.
- **Untested with clinicians.** The section ordering and the decision to show
  empty sections are hypotheses about what a clinician wants at the bedside.
  DESIGN_PRINCIPLES §6 warns against overstating; showing five sections when the
  clinician wanted one sentence is its own kind of overstatement.
