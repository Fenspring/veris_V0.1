# Decision 0002 — Experiment 0001: The Coverage Probe

**Date:** 2026-08-15
**Status:** **Revised** — original synthetic-corpus design superseded by the
real corpus supplied on 2026-08-15.
**Addresses:** CONSTITUTION §11 (discovery through building), §12 (evaluation),
§16 (the ultimate test); PRODUCT_THESIS Hypothesis 8

---

## What changed and why

The first version of this experiment proposed a synthetic 12-document corpus
with planted findings, because no real documents were available. Real documents
are now available: **34 hospital policy PDFs and 28 Joint Commission standards**
across four chapters (EM, IC, LD, NR).

That replaces the synthetic corpus entirely. A synthetic corpus written by the
same agent that writes the extractor can only ever demonstrate that the
extractor understands its own vocabulary. Two things also became true that
change the experiment's shape:

1. **Findings can no longer be planted — they must be found.** This is harder
   and much more informative. The gold set is now a hand-labeled judgment about
   real documents rather than a list of things I hid on purpose.
2. **The corpus contains no education, competency, or audit artifacts** — only
   text *requiring* them (Discovery 0003). The MISSION's worked example, whose
   gap sits at the competency stage, therefore cannot be reproduced here.

## The thesis under test

> Connecting knowledge across independent sources produces intelligence that no
> individual source can provide, and that ordinary search cannot produce.

Both clauses matter. CONSTITUTION §16 says a system that merely provides better
search has not demonstrated the thesis, so the experiment is **comparative**.

## The question

Reframed onto the stage this corpus fully represents on both sides:

> For each Joint Commission Element of Performance that explicitly requires
> documentation, what in the hospital's 34 policies covers it — and what
> appears not to be covered at all?

The corpus makes this a well-posed question rather than an arbitrary one:

| | Count |
|---|---|
| Elements of Performance | 94 |
| **EPs tagged `Attributes: Documentation`** | **61** |
| Hospital policy documents | 34 |

Per Discovery 0003, the `Documentation` attribute is the standard declaring that
evidence must exist. So each of those 61 EPs is a grounded question, not a
question Veris invented. This is also, not incidentally, the actual
accreditation-readiness workflow — which makes it a candidate answer to
PRODUCT_THESIS Hypothesis 8 (the first killer workflow), rather than a lab task.

## Why this qualifies as a test of the thesis

- **Cross-source by necessity.** Every finding requires a standard *and* a
  policy. Nothing derivable from one document counts.
- **Not a retrieval question.** "Which of these 61 requirements has no
  corresponding commitment anywhere in 34 documents?" has no answer in any
  document. It exists only in the relation between them.
- **Absence is the output**, which is what Discovery 0002 argues no
  retrieval-shaped system can produce.

## Baselines — what the pipeline must beat

| ID | Baseline | Purpose |
|---|---|---|
| **B0** | Keyword / BM25, EP text as query over policies | The floor. |
| **B1** | Embedding similarity over chunks, top-k + threshold | The generic RAG strawman: what a competent team ships in a week. |
| **B2** | Long-context model, whole corpus in one prompt | Honest and dangerous at this size. Note it **cannot run on the deployment target at all** (Decision 0004), so even if it wins on accuracy it does not win as architecture. |
| **B3** | **CFR citation join** — link policy to EP where both cite the same §482.x | **New, and the most dangerous baseline.** Deterministic, free, needs no model. If this is good enough, most of Veris is unnecessary. |

B3 was not in the original design and only became visible from the real data. My
prediction is that it delivers high recall and poor precision — §482.13 joins 15
policies to 1 standard, §482.15 joins 1 policy to 13 standards — so it will
propose the right pair among many wrong ones but cannot say whether the policy
actually *satisfies* the EP. That prediction is recorded here so it can be
wrong. If B3 alone produces defensible coverage findings, that is a major
negative result about the value of semantic connection and will be reported as
such.

## The gold set

Hand-labeled by me, **before the pipeline is run**, and frozen by hash. For a
stratified sample of **20 of the 61** documentation-tagged EPs, each label
records:

- `COVERED` — with the policy document and the exact span that covers it.
- `PARTIAL` — covered in part, with the span and what is missing.
- `NOT_COVERED` — with the search terms tried, so the label is auditable.

Every `COVERED` label must cite a span. A label without evidence is not a label.

### The honesty problem, stated plainly

I am writing the gold set, the extractor, and — for now — acting as the model.
That is three roles that should not be held by one party, and it is the single
largest threat to this experiment's validity. Mitigations:

- Labels are frozen and hashed before extraction logic exists.
- Every label cites evidence, so a domain expert can audit it in minutes.
- The stratified sample includes EPs I expect to be uncovered *and* EPs I expect
  to be covered only in unrelated vocabulary — the cases most likely to embarrass
  the pipeline.

None of these fully solve it. **Founder review of the 20 labels is the real
control**, and it is cheap: 20 judgments in a domain the founder knows. Until
that review happens, results should be read as "the pipeline agrees with me,"
which is weaker than "the pipeline is right."

## The decoys — now found rather than planted

The corpus supplies natural adversarial cases, which is better than planted ones
because I did not choose them:

| # | Case | Correct behavior |
|---|---|---|
| N1 | **Empty standards.** `LD.12.01.01`, `LD.13.01.07`, `LD.13.03.01` have zero content. | Must **not** yield findings. A gap "found" here is manufactured by ingestion, not observed. These are retained and flagged rather than dropped for exactly this reason. |
| N2 | **Duplicate-numbered policy pairs** — `01` Hand Hygiene / Infection Control, `18` Blood Administration / Transfusion Reaction, `27` Pain Assessment ×2. | Overlapping scope. Genuine drift should be reported; mere restatement must not be called a conflict. |
| N3 | **Vocabulary distance.** "Two patient identifiers" appears in 4 policies; EPs describing the same obligation use entirely different phrasing. | Coverage must be found across the vocabulary gap (defeats B0). |
| N4 | **Coarse citation buckets.** §482.13 → 15 policies. | Must not emit 15 relationships (defeats B3 and B1). |
| N5 | **Requirement-about-X vs. X.** 84 mentions of education, none of which *is* education. | Must not classify an EP as TEACHES because it discusses training (Discovery 0003). |

## Metrics

1. **False-positive gaps** (primary) — findings of absence where the gold set
   says covered. Target 0 on N1.
2. **Coverage precision / recall** against the 20-EP gold set.
3. **Evidence validity** — % of claims whose quote appears byte-for-byte at the
   cited offsets. **Pass/fail at 100%**, not a score to tune.
4. **Cross-source necessity** — % of findings requiring ≥2 sources. Anything
   less than ~100% means the experiment is not testing the thesis.
5. **Uplift over B3** — the number that decides whether semantic connection is
   earning its complexity.

## Kill criteria

- **B3 matches the pipeline** → semantic connection is not earning its keep on
  this corpus; report it and reconsider the product.
- **Evidence validity below 100%** → the grounded-claim primitive (Decision 0001)
  is wrong and must be fixed before anything is built on it.
- **Gaps reported against the three empty standards** → the ingestion-level
  false-gap problem is real and unsolved, which is disqualifying regardless of
  every other score.

## Deliberate limitations

- 62 documents says nothing about 12,000.
- Policies here are compliance-summary documents citing CFR sections; a real
  hospital's policy library is longer, messier, inconsistently formatted, and
  frequently uncited. Success here is necessary, not sufficient.
- One customer's corpus. Generalization is untested.
