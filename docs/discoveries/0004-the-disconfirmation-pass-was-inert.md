# Discovery 0004 — The Disconfirmation Pass Was Inert, and Why

**Date:** 2026-08-15
**Status:** Confirmed by measurement, fixed, re-measured
**Type:** Negative result

---

## What was claimed

Discovery 0002 argued that the hardest problem in Veris is asserting absence in
an open world, and proposed a **disconfirmation pass** as the mitigation: before
emitting a gap, actively search for the thing about to be called missing, and
emit the gap only if that search fails. I called it "the clearest candidate for
the piece of engineering that is genuinely Veris-specific."

## What actually happened

On the first full run it surfaced **zero** new candidates on **all eight** gap
verdicts. The mechanism was inert precisely where it was needed.

## Why — and the general lesson

The first implementation derived every disconfirmation query from the
requirement's own text (its noun phrases, its bulleted sub-requirements) and ran
them against the same BM25 index that produced the original candidates.

**A gap arises exactly when the policy uses different words than the
requirement.** So queries built from the requirement's vocabulary search the
same lexical neighbourhood that already failed. The pass could only ever
re-confirm the retrieval it was meant to challenge.

This generalises past this codebase: *a disconfirmation step that shares a
failure mode with the step it is checking provides no assurance while appearing
to.* That is worse than having no check, because the finding now carries a
"survived adversarial search" label it did not earn. Veris would have been
shipping unearned confidence.

## Two fixes

**1. Index the document title with the provision (structural, no model).**
The real defect underneath: a provision's text does not contain its own subject.
"Infection Control and Prevention §4 Surveillance & Reporting" is about
infection control, but its body never says so, so BM25 could not reach it from
an infection-control requirement. For `IC.04.01.01 EP 3` the correct policy did
not appear in the top six at all — the pipeline was about to report a false gap
on a requirement the hospital genuinely covers.

Indexing the locator alongside the body moved the four correct Infection Control
provisions to ranks 1, 2, 3 and 5. A provision inherits its subject from the
document it lives in, and discarding that context was simply a bug.

**2. Get query vocabulary from outside the requirement (model-generated).**
Title indexing does not fix genuine vocabulary distance: `LD.13.01.09 EP 5`
("policies that minimize drug errors") still could not reach the Medication
Administration policy, because neither its title nor its provisions use the word
"drug errors." The fix is to ask the model what words *a hospital policy* on
this subject would use — "medication error prevention", "five rights",
"high-alert medications" — and search those. The vocabulary must come from
outside the requirement, or it cannot bridge a vocabulary gap.

Structural sub-requirement queries are retained as a fallback so the pass still
does something if the expansion call fails. A cheap check that degrades is
better than one that silently does nothing.

## Result after the fix

The pass now fires on 3 of 7 gap candidates and changes one verdict.

Most importantly, on `EM.12.02.11 EP 4` (alternate energy sources) it surfaced
*Environment of Care — Hazardous Materials §3 Utility Systems Management*:

> Critical utility systems (electrical, HVAC, medical gas, water) must be
> maintained, tested, and monitored. **Backup systems must be operational.**
> Procedures for utility failures must be documented.

That is genuinely partial coverage. **My hand-written gold label for that EP
said NOT_COVERED — the disconfirmation pass caught my own labelling error.**
When I labelled it I searched "alternate energy", "generator", "emergency
power"; I never thought of "utility systems management". That is precisely the
human failure mode the mechanism exists to correct, and it corrected a human.

It also correctly declined to overturn two gaps where the surfaced evidence only
looked relevant: nursing competency validation is not emergency-management
education, and returning an implicated blood bag to the blood bank is not
ten-year record retention.

## What this cost, and what it says about the method

This defect was invisible in the design and in the code, which read plausibly.
It only appeared because the run reported how many candidates the pass surfaced.
The lesson for the rest of Veris: **instrument the mechanisms that are supposed
to protect trust, and report their activation rate as a metric.** A safety
mechanism whose firing rate is never measured should be assumed inert.
