# Decision 0002 — Experiment 0001: The Alignment Probe

**Date:** 2026-08-15
**Status:** **Proposed** — awaiting founder go-ahead before implementation
**Addresses:** CONSTITUTION §11 (discovery through building), §12 (evaluation),
§16 (the ultimate test); PRODUCT_THESIS Hypothesis 8

---

## The thesis under test

> Connecting knowledge across independent sources produces intelligence that no
> individual source can provide, and that ordinary search cannot produce.

Note the second clause. Proving the first alone is not enough — CONSTITUTION §16
says a system that merely provides better search has not demonstrated the
thesis, and PRODUCT_THESIS lists "most useful relationships can be produced by
ordinary search" as a falsifier. So the experiment must be **comparative**, not
demonstrative. A demo that produces impressive findings and beats nothing proves
nothing.

## Design

One narrow clinical domain — **medication administration and patient
identification** — because it is where regulation, policy, procedure, education,
competency, and audit all genuinely converge in a real hospital, and it is the
domain of the MISSION's own worked example.

A corpus of **10–12 short documents** spanning six source genres, with findings
and non-findings planted *before* any extraction logic is designed.

### Legal constraint on the corpus

Joint Commission standards, CMS interpretive guidance, and most accreditation
content are copyrighted and licensed. **No verbatim external standard text will
be committed to this repository.** Corpus documents are synthetic, written in
the style and structure of the genre, clearly labeled fictional, for a fictional
"Northbrook Regional Medical Center." This is consistent with CONSTITUTION §8:
the hospital supplies its own licensed knowledge; Veris never redistributes it.

### Planted findings (the positives)

| # | Type | Design |
|---|---|---|
| P1 | **Gap** | Requirement + policy + education all address independent barcode scanning; no competency validates it. The MISSION example, reproduced faithfully. |
| P2 | **Gap** | A policy commits to a two-nurse verification for high-alert medications; no procedure operationalizes it and no audit measures it. Tests a gap at a different lifecycle stage than P1. |
| P3 | **Conflict** | Policy requires two patient identifiers (name + DOB); the unit procedure instructs name + room number. Same role, incompatible content. |
| P4 | **Drift** | Policy was revised to require scanning *before* administration; education still teaches scanning *during* the pass. Same direction, diverged specifics. |
| P5 | **Orphan** | A standing policy commitment tied to a requirement that has been superseded — a rule with no live obligation behind it. |
| P6 | **Change impact** | A revised requirement lands; the system must identify every downstream claim in its thread as candidate-for-review. |

### Planted non-findings (the decoys — the real test)

| # | Type | Design | Must produce |
|---|---|---|---|
| D1 | **Vocabulary decoy** | A competency *does* validate barcode scanning but never uses the phrase — "scans the patient's wristband and the medication label prior to administration." | **No gap.** Tests semantic recall; a lexical system fails here. |
| D2 | **Lexical decoy** | Two documents share heavy "medication" vocabulary but address unrelated obligations (controlled substance waste vs. allergy documentation). | **No relationship.** Tests precision; an embedding-similarity system fails here. |
| D3 | **Supersession decoy** | Two policy versions in the corpus, one explicitly superseding the other, stating different things. | **No conflict** — a supersession, correctly identified. |
| D4 | **Incorporation decoy** | A competency covers an obligation only by reference: "administers medications in accordance with hospital policy." | Either correct coverage or an *explicitly flagged uncertainty*. Silently calling this a gap is a failure; silently calling it full coverage is also a failure. |
| D5 | **Out-of-scope decoy** | An obligation whose implementing evidence genuinely lives in a system not supplied (the LMS). | A gap **scoped and confidence-capped**, not an absolute claim. |

D1–D3 are the metrics that matter. Any system can find planted gaps; only a
correct one declines to find the absent ones.

## Baselines — what the pipeline must beat

| ID | Baseline | Purpose |
|---|---|---|
| **B0** | Keyword / BM25 search over documents | The floor. Establishes that the problem is not trivially lexical. |
| **B1** | Embedding similarity over chunks, top-k, threshold | The generic RAG strawman. This is what a competent team would ship in a week; if it wins, Veris as conceived is unnecessary. |
| **B2** | **Long-context LLM given the entire corpus at once, asked to find gaps and conflicts** | The honest, dangerous baseline. |

B2 deserves emphasis. At 12 documents, a frontier model can read the whole
corpus and may well match or beat a structured pipeline. **I predict B2 will
score competitively on the positives and worse on the decoys.** If B2 wins
outright, the correct conclusion is not "keep building the pipeline anyway" — it
is that the pipeline's justification is **scale, auditability, reproducibility,
and incremental change detection**, not raw accuracy, and the roadmap should be
rewritten around those instead. That conclusion will be reported plainly if the
evidence supports it.

## Metrics

1. **Decoy false-positive count** (primary). Target: 0 on D1–D3.
2. **Finding recall / precision** against the planted set P1–P6.
3. **Evidence validity rate** — % of emitted claims whose quote appears
   byte-for-byte in the cited source at the cited offsets. Target: 100%. This is
   pass/fail, not a score; anything below 100% is a defect, not a tuning knob.
4. **Cross-source necessity** — % of findings that provably require ≥2 sources.
   A finding derivable from one document does not count toward the thesis.
5. **Scope correctness** — every absence finding names the corpus subset
   searched.

## Pre-registered predictions

Recorded now so the result cannot be rationalized after the fact:

- B0 finds P3 (lexically visible conflict) and nothing else. Fails D1.
- B1 finds no gaps at all — similarity search has no representation of absence —
  and produces false links on D2.
- B2 finds most of P1–P4, is inconsistent run to run, and fails D3 or D4.
- The claim pipeline finds P1–P4 reliably, struggles with P5/P6, and passes D1–D3.
- **The most likely way the pipeline fails is D4** (incorporation by reference),
  because "per hospital policy" defeats claim atomization by design.

## Kill criteria

Reported honestly rather than worked around:

- If B1 matches the pipeline on the positives **and** the decoys, the semantic
  connection layer is not earning its complexity.
- If evidence validity cannot reach 100%, the grounded-claim primitive
  (Decision 0001) is wrong and must be reconsidered before anything is built on it.
- If the pipeline cannot pass D1–D3, the false-positive problem named in
  PRODUCT_THESIS is real and unsolved, and no amount of UI work matters.

## Deliberate limitations

- **I am writing both the corpus and the system**, which risks an unconsciously
  easy test. Mitigations: findings and decoys are frozen and hashed *before*
  extraction logic exists; documents are written in genre voice rather than in
  the vocabulary the extractor will use; D1 and D2 exist specifically to punish
  the shortcuts I would be tempted to take.
- **A synthetic corpus is a smoke test, not proof.** It can only falsify, not
  validate. Passing it means "keep going," never "this works." The real test
  requires real hospital documents — see the ask in the founder report.
- 12 documents says nothing about behavior at 12,000. Scale is deliberately out
  of scope for Experiment 0001.
