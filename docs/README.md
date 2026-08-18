# Veris Documentation

`decisions/` — architectural and product decisions: problem, alternatives,
evidence, decision, tradeoffs, and what would cause reconsideration
(CONSTITUTION §14).

`discoveries/` — product and technical insights found while building, including
negative results. A mechanism that looked correct and did nothing is worth more
written down than quietly fixed.

Documents are numbered in the order they were written.

## Discoveries

| | Subject |
|---|---|
| [0001](discoveries/0001-the-obligation-lifecycle.md) | Obligations have a lifecycle; organizational failure happens *between* artifacts, not inside them |
| [0002](discoveries/0002-absence-is-the-product.md) | Veris's headline output is a statement about what is *missing*, which inverts the engineering problem |
| [0003](discoveries/0003-requirements-declare-their-own-evidence.md) | Requirements declare their own expected evidence, so a gap rests on the standard's demand rather than Veris's opinion |
| [0004](discoveries/0004-the-disconfirmation-pass-was-inert.md) | **Negative result.** The disconfirmation pass surfaced nothing on every gap, because it shared a failure mode with the step it was checking |
| [0005](discoveries/0005-conflicts-hide-from-lexical-retrieval.md) | **Negative result.** Conflicts rank *below* unrelated text, because a contradiction shares less vocabulary than a mere restatement |

## Decisions

| | Subject | Status |
|---|---|---|
| [0001](decisions/0001-core-primitive.md) | The core primitive is the grounded claim | Proposed |
| [0002](decisions/0002-experiment-0001-the-alignment-probe.md) | Experiment 0001 — the comparative test of the central thesis | Revised |
| [0003](decisions/0003-architecture-shortlist-and-deferrals.md) | Architecture shortlist; SQLite, no graph DB, no vector DB yet | Proposed |
| [0004](decisions/0004-model-agnostic-inference.md) | Model-agnostic inference, assuming the weakest plausible model | Accepted |
| [0005](decisions/0005-the-answer-surface-is-composition-not-retrieval.md) | The answer surface is composition over precomputed connections | Accepted |
| [0006](decisions/0006-architecture-assessment-and-the-mvp-slice.md) | Architecture assessment and the MVP vertical slice | Accepted |

Start with discoveries 0001 and 0002. The decisions follow from them.

## Measured results

- `eval/run_eval.py` — 49 checks across ten capabilities on the Northstar demo
  corpus. Planted findings: exercises the mechanics, cannot prove generalization.
- `eval/gold.json` — 20 hand-labelled Joint Commission Elements of Performance
  against a real 34-policy library. Pipeline scored 90% accuracy, 0.83 F1, one
  false gap, against 0.62 for BM25 and 0.00 for a citation join. Provisional:
  the labels and the model responses came from the same agent.
