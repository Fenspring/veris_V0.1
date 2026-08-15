# Discovery 0003 — Requirements Declare Their Own Expected Evidence

**Date:** 2026-08-15
**Status:** Confirmed in the supplied corpus
**Confidence:** High. This is an observation about real data, not a hypothesis.

---

## The problem this solves

Discovery 0001 proposed that obligations move through a lifecycle and that gaps
are missing stages. It left a hole I did not have an answer for:

> How does Veris know that a competency *should* have existed?

The tempting answer is a template — "every obligation should have a policy, an
education, and a competency." That answer is wrong and dangerous. It makes
Veris's findings rest on Veris's own opinion about how a hospital ought to be
organized, which is exactly the kind of unearned authority CONSTITUTION §5 and
§7 forbid. A hospital could reasonably reject the premise, and it would have no
evidence to argue with.

## The observation

The supplied Joint Commission corpus answers this itself.

Standards are already decomposed into **Elements of Performance** — atomic,
individually citable, individually surveyable normative statements. And a
majority of them carry a structured attribute tag:

```
### EP 2
The hospital provides initial education and training in emergency management to
all new and existing staff ... Documentation is required.

**Attributes:** Documentation

**Crosswalk:** §482.15(d)(1)(i)
```

Measured across the corpus:

| | Count |
|---|---|
| Elements of Performance | 94 |
| EPs tagged `Attributes: Documentation` | **61 (65%)** |
| EPs tagged `ESP-1` | 67 |
| EPs carrying a `Crosswalk` to 42 CFR | 40+ |

`Attributes: Documentation` is the standard stating, in a machine-readable
field, **that a document must exist for this requirement.**

## The insight

**The requirement declares its own expected evidence. Veris does not have to
assume what a hospital should possess — the standard says so, and says so in a
structured field.**

This changes the epistemic status of a gap finding completely:

> ~~"You appear to be missing a competency, because obligations usually have
> competencies."~~ — Veris's opinion. Rejectable.

> "EM.15.01.01 EP 1 requires a written education and training program and is
> tagged as requiring documentation. Across the 34 policy documents supplied,
> no claim was found that establishes one." — the standard's demand, plus a
> scoped search result. Both halves are evidence.

The second is defensible in front of a compliance officer. The first is not.

## Why this also improves the architecture

The expected-artifact type comes from the requirement rather than from a
lifecycle template Veris imposes. So Discovery 0001's role taxonomy needs one
refinement, which the data forced:

**A claim's role and the artifact it demands are two different fields.**

`EM.15.01.01 EP 1` does not *teach* anything. It **requires that teaching
exist**. Modeling this as a single role conflates "what this claim does" with
"what this claim demands," and the conflation would have produced nonsense —
classifying an accreditation standard as TEACHES because it talks about
education. The 84 education mentions in this corpus are almost entirely
*requirements about* education, not education itself.

```
role             what this claim does          REQUIRES
expects_artifact what it demands exist         education/training program
expects_document whether documentary evidence  true  (from Attributes tag)
                 is explicitly required
```

Gap detection then becomes: *for each claim with `expects_document = true`,
does any claim in the supplied corpus satisfy what it expects?*

## What the corpus does and does not contain

Worth stating plainly, because it bounds what can be tested:

- **Present:** 28 Joint Commission standards (EM, IC, LD, NR) and 34 hospital
  policy documents, several of which contain detailed step-level procedures,
  and a few of which contain measurement definitions (e.g. the CLABSI bundle's
  `SIR ≤ 1.0`, reported to QAPI monthly).
- **Absent:** any actual education module, competency checklist, or audit tool.
  There is a great deal of text *requiring* education and competency, and none
  that *is* education or competency.

So the MISSION's worked example — the gap at the competency stage — cannot be
reproduced with this corpus, because the competency stage is not represented at
all. What *can* be tested, and is arguably a better first experiment, is the
stage that is fully represented on both sides: **requirement → policy
coverage**, across 61 documentation-tagged EPs and 34 policies.

That is not a downgrade. It is the actual accreditation-readiness question, it
is grounded end to end, and every finding it produces requires at least two
independent sources.

## A caution about the citation crosswalk

The corpus also supplies an apparently free join: policies cite CFR sections
(`Regulation: 42 CFR §482.25`), and standards carry `Crosswalk:` fields to the
same CFR sections. Ten CFR sections appear on both sides.

This must be treated as a **candidate generator, not an answer**, and it must be
run as a baseline before anything semantic is built (see Decision 0002, B3).
The buckets are coarse to the point of near-uselessness as findings: §482.13
joins 15 policies to 1 standard, §482.15 joins 1 policy to 13 standards. A
citation join proposes those pairs; it cannot say whether the policy actually
satisfies the EP, which is the entire question.

But it is cheap, deterministic, needs no model at all, and has high recall on
same-topic pairs. That makes it valuable in a way I did not anticipate: it means
candidate generation on this corpus requires **no embedding model**, which
materially helps the model-agnostic requirement (Decision 0004).
