# Discovery 0002 — Absence Is the Product

**Date:** 2026-08-15
**Status:** Proposed insight, not yet validated
**Confidence:** High on the problem statement, medium on the proposed mitigation.

---

## The observation

Read the finding types Veris is supposed to produce and notice what they have
in common:

- "Potential **gap**: the competency framework does not appear to address it."
- "Two policies describe the same process **differently**."
- "A procedure implements a requirement that is **no longer** current."
- "A policy has dependencies that are **not obvious** to its owner."

The headline outputs of Veris are statements about things that are **not
there**. The most valuable sentence the product can produce is a sentence about
an absence.

Essentially every AI knowledge product built in the last several years does the
opposite. Retrieval systems, RAG applications, and search engines all answer
"what exists that matches this?" They are optimized for returning something.
They have no concept of, and no way to be held accountable for, *nothing*.

This is the actual differentiation of Veris, and it is not a UI difference or a
prompt difference. It inverts the engineering problem.

## Why this is the hardest technical problem

A gap finding is a claim about the **non-existence** of something in a corpus.
Non-existence claims are only sound under a **closed-world assumption** — you
may conclude "there is no competency for this" only if you know you have seen
all the competencies.

Healthcare knowledge corpora are aggressively open-world:

- The hospital will supply a subset of its documents, not all of them.
- Some knowledge is not in documents at all (it is in Epic, in the LMS database,
  in a binder on a unit, in someone's head).
- A competency may cover the obligation using vocabulary that shares no words
  with it — "scans the patient's wristband and the medication barcode prior to
  administration" never says "barcode medication administration."
- The obligation may be covered by incorporation: "administers medications per
  hospital policy" technically covers everything the policy says.

So the single most dangerous failure mode of Veris is not missing a gap. It is
**confidently announcing a gap that is not a gap** — because that failure is
expensive, embarrassing in front of a compliance officer, and it destroys trust
in every other finding the system has made. PRODUCT_THESIS already names this
as a falsification condition: *"Semantic relationships create too many false
positives."*

An absence finding is unfalsifiable from the inside. The system cannot know what
it has not been given. This is not a model-quality problem that gets better with
a stronger LLM. It is a structural epistemics problem, and it must be solved in
the architecture.

## Proposed mitigation: make absence a scoped, adversarially-tested claim

Three mechanisms, all testable:

### 1. Declared scope — a gap is never absolute

Every absence finding must state the boundary of the search that produced it:

> Across the **3 competency documents supplied for the Med-Surg RN role**, no
> claim was found that validates independent barcode scanning.

Not: *"Your organization has no competency for BCMA."* This is
DESIGN_PRINCIPLES §6 ("Don't Overstate") promoted from a writing-style
guideline to an architectural requirement — the scope has to be *computed and
carried by the finding*, not phrased carefully at the end.

### 2. The disconfirmation pass — the system must try to prove itself wrong

Before a gap is emitted, the system runs a **dedicated adversarial search for
the thing it is about to claim is missing**, using multiple independently
generated query formulations: the obligation's own wording, paraphrases in the
vocabulary of the target role, its component actions, and lexical variants. The
gap is emitted **only if that targeted search fails.**

This inverts the normal burden of proof. A similarity pipeline concludes "no
match above threshold, therefore gap." Veris must conclude "I actively tried
four ways to find this and could not, therefore *potential* gap."

The disconfirmation pass is, at present, the clearest candidate for the piece of
engineering that is genuinely Veris-specific. Everything else in the plausible
architecture is off-the-shelf.

### 3. Confidence bounded by corpus completeness

If the organization indicates it maintains competencies for 40 roles and has
supplied documents covering 3, no gap finding about competencies may carry high
confidence. Completeness is metadata the customer supplies about their own
corpus, and it caps confidence arithmetically rather than rhetorically.

## The evaluation consequence

This changes what "good" means. Standard retrieval metrics reward precision on
things returned. Veris must be evaluated on:

- **Decoy false-positive rate** — planted content that *looks* absent but is
  present in unrelated vocabulary must not be reported as a gap. This is the
  primary metric.
- **Recall of the corpus**, not of the query. Missing a document silently is a
  correctness bug, because it manufactures a false gap downstream.
- **Cross-source necessity** — a finding that could have been produced by
  reading one document alone does not count as evidence for the thesis, no
  matter how correct it is. This directly operationalizes CONSTITUTION §16.

Any evaluation set for Veris must therefore contain **deliberately planted
non-findings**. An eval set made only of true gaps will make any system look
excellent and tell us nothing.

## What would falsify this

- Customers accept unscoped gap claims and find the hedging annoying rather than
  trustworthy. (Possible! Compliance staff may prefer decisive output. Worth
  testing directly with a human.)
- The disconfirmation pass costs more than it saves — i.e. naive thresholding
  already produces an acceptable false-positive rate on real corpora.
