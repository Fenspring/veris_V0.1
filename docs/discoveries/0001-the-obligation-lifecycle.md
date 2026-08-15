# Discovery 0001 — The Obligation Lifecycle

**Date:** 2026-08-15
**Status:** Proposed insight, not yet validated
**Confidence:** Medium-high. Derived from reasoning about the domain and from
corroborating structure already present in the governing documents. Not yet
tested against a real corpus.

---

## The question

MISSION describes the value of Veris with a worked example:

> A requirement demands medication safety controls.
> A policy requires barcode medication administration.
> Education teaches barcode medication administration.
> A competency does not address it.
> → Potential alignment gap.

The obvious reading of this example is "semantic similarity across documents."
That reading is wrong, or at least incomplete, and following it leads directly
to the generic RAG application the mission forbids.

Similarity alone cannot produce that finding. Similarity tells you the policy
and the education are *about the same thing*. It cannot tell you that a
competency *should have existed and did not*. Something else is doing the work
in that example.

## The observation

What is actually doing the work is that each of those four artifacts plays a
**different functional role in a chain of organizational accountability**:

| Role | What it does | Typical source |
|---|---|---|
| **REQUIRES** | Imposes an external obligation | Regulation, accreditation standard, state rule |
| **COMMITS** | States what the organization will do about it | Policy |
| **OPERATIONALIZES** | States how it is actually done | Procedure, protocol, workflow, order set |
| **TEACHES** | Transfers the capability to a person | Education, training, orientation |
| **VALIDATES** | Verifies a person can actually do it | Competency, skills validation, credentialing |
| **MEASURES** | Verifies it is actually happening | Audit tool, quality metric, tracer, dashboard |
| **DESCRIBES** | Provides context without obligation | Reference, guideline, department documentation |

These are not an ontology invented for Veris. They are the labels the hospital
*already uses on its own filing cabinets*. Hospitals already separate policy
from procedure from education from competency, because those artifacts are owned
by different people, approved through different committees, and audited by
different surveyors. The organization has already done this classification work;
it just has never used it as an analytical structure.

## The insight

**An obligation has a lifecycle, and organizational failure is usually a broken
link in that lifecycle rather than a defect in any single artifact.**

The reason each artifact "may be individually correct while the organization is
still misaligned" (MISSION) is precisely this: correctness is evaluated
*within* a role, but failure occurs *between* roles.

This reframes what Veris computes. It is not computing similarity between
documents. It is computing, for each obligation the organization is subject to,
**which lifecycle stages have evidence and which do not.**

Every finding type named in DESIGN_PRINCIPLES §5 falls out of that one
structure as a simple query:

| Finding | Shape in the lifecycle |
|---|---|
| **Gap** | A stage is missing from the chain |
| **Conflict** | Two claims at the same stage are incompatible |
| **Drift** | Adjacent stages no longer express the same specifics |
| **Dependency** | Claims sharing a chain are mutually affected |
| **Change impact** | A change at one stage propagates down the chain |
| **Orphan** | A COMMITS with no REQUIRES — a rule serving no obligation |

That last row was not in any governing document. It emerged from the structure
rather than being designed in. That is weak evidence the structure is real: a
good primitive generates findings you did not think to ask for.

## Corroboration in the existing documents

DESIGN_PRINCIPLES §4 lists candidate relationship types without committing to
them: *Implements, Supports, Requires, Teaches, Validates, Depends on, Conflicts
with, Supersedes*. Four of those seven — Requires, Teaches, Validates,
Implements — are lifecycle *roles*, not relationships. The founder had already
half-identified this structure and filed it under the wrong heading.

This matters for the architecture: **role is a property of the claim, not of the
edge between claims.** Modeling it as an edge type forces you to decide the
relationship before you understand either side. Modeling it as a property of the
claim means relationships can be *derived* rather than asserted.

## The important correction

Role is a property of the **claim**, not of the **document**. A policy document
routinely contains procedural steps; a competency checklist routinely restates
the policy it validates. Classifying at the document level is the failure mode
of every existing policy-mapping and GRC crosswalk tool, and it is why those
tools produce link tables that nobody trusts: they connect a 40-page policy to
a 300-page standard and call it a mapping.

The document type is a strong *prior* for the role of the claims inside it.
It is not the answer.

## What would falsify this

- A real corpus where useful findings do not decompose into lifecycle stages.
- Roles that cannot be assigned reliably (high inter-annotator disagreement, or
  a majority of claims landing in DESCRIBES).
- Findings that require a role vocabulary substantially larger than ~7, which
  would indicate we are rebuilding an ontology and should stop.
- Customers who find lifecycle gaps obvious or already-solved.

## Consequence

If this holds, the fundamental unit of Veris is not the document, not the chunk,
and not the concept. See `docs/decisions/0001-core-primitive.md`.
