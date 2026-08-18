# Discovery 0005 — Conflicts Hide From Lexical Retrieval

**Date:** 2026-08-16
**Status:** Confirmed by measurement, fixed, verified by evaluation
**Confidence:** High on the mechanism. Measured on one corpus.

---

## The observation

The Northstar demo corpus contains a planted contradiction, and it is the single
most important finding in the whole demo:

> **Standard NS-CS.02.01 EP 2 (v2.0):** waste is witnessed by a second licensed
> person *who did not administer the dose*.
>
> **Medication Wasting Procedure §2:** *any* licensed staff member present may
> witness, *expressly including the nurse who administered the dose*.

Retrieving the top candidates for that requirement by BM25 ranked the
contradicting procedure step **tenth** — below an unrelated sharps-disposal step
about biohazard waste. With a top-six candidate window, the most consequential
finding in the corpus was invisible.

## Why this happens, and why it generalizes

The instinct is that a conflict must be lexically close to the requirement it
contradicts, since both are about the same subject. That instinct is wrong.

**A conflicting statement often shares *less* vocabulary with a requirement than
a merely related one does**, because the conflict lives in what each text says
*differently*. The requirement says "did not administer"; the procedure says
"including the nurse who administered". A term-frequency model sees a shared
word stem and a lot of unshared context. Meanwhile a policy statement that
merely restates the requirement's topic in its own vocabulary scores highly
while adding nothing.

So the ranking is not just noisy — it is **biased against exactly the finding
type the product exists to surface**. A gap is an absence and has no text to
rank at all (Discovery 0002); a conflict has text that ranks badly. Both of the
findings that matter most are the ones retrieval is worst at reaching.

## The fix: rank documents, then take their provisions

Relevance is a property of documents first and provisions second.

Once a document is clearly on topic — the Medication Wasting Procedure ranked
first overall for this requirement — its *other* provisions are candidates
regardless of their own scores, because a contradiction usually sits in the same
document as the passages that match well. The contradicting step moved from
tenth to second.

This is the same insight as Discovery 0004's title indexing, taken one step
further. There, a provision could not be found because its text did not contain
its own subject. Here, a provision cannot be found because its text does not
resemble what it contradicts. Both are cases of **a provision's meaning
depending on the document it lives in**, and both are fixed by treating the
document as the unit of relevance and the provision as the unit of citation.

## Why this is not solved by embeddings

Worth stating, because "use embeddings" is the reflex. Semantic similarity would
rank the contradicting step higher than BM25 does — the sentences are about the
same act — but similarity still measures *closeness*, and a contradiction is
defined by a difference in a small, decisive span of text. An embedding of
"witness may include the administering nurse" and one of "witness must not be
the administering nurse" are near neighbours; that nearness is why similarity
finds them, and also why similarity cannot tell you which is which.

Retrieval's job is to put the pair in front of judgment. Deciding that the two
cannot both be followed is judgment's job. Document-first ranking is a cheaper
way to get the pair in front of judgment than adding an embedding service, and
it requires no second model deployment inside the hospital (Decision 0004).

## What this cost, and how it was caught

It was caught by checking whether the demo actually surfaced the finding it was
designed around — not by reading the retrieval code, which looked correct, and
not by the eval, which did not yet exist. The evaluation suite now contains the
check (`eval/northstar_cases.json`, `conflicts`), so a regression here fails
loudly.

The general lesson matches Discovery 0004's: **verify that the pipeline finds
the specific things you built it to find**, by name, in a test. A retrieval
stage that returns plausible results is not evidence that it returns the
necessary ones.
