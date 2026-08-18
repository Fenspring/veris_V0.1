# Decision 0006 — Architecture Assessment and the MVP Vertical Slice

**Date:** 2026-08-16
**Status:** Accepted — drives the Knowledge OS build
**Supersedes nothing.** Extends Decisions 0001–0005.

---

## Assessment of what exists

1,951 lines of Python, 62 ingested documents, 306 verified claims, a frozen
20-case gold set, and measured results against three baselines.

### Preserve — this is the load-bearing work

| Component | Why it survives |
|---|---|
| `veris/ingest.py` | Canonical text frozen and hashed; spans verified by string equality. This is the provenance guarantee (§7) already implemented. |
| `veris/model.py` | Provider-agnostic inference over stdlib HTTP: OpenAI-compatible, Anthropic, and a recording provider. This is §12 already done, and the recording provider is what makes evaluation reproducible. |
| `veris/claims.py` | Structural extraction using each document's own units — Elements of Performance, numbered provisions. Claim boundaries are meaningful and model-free. |
| `veris/retrieve.py` | BM25 + CFR citation join, no embedding service required. |
| `veris/adjudicate.py` | Relationship judgment with a disconfirmation pass. |
| `veris/brief.py` | Composition over precomputed connections — the pattern "Ask Veris" must follow (§11). |
| `eval/` + `tests/` | Gold set, three baselines, measured scores. |
| `docs/` | Six decisions, four discoveries, including two negative results. |

### Missing — the gap between what exists and the brief

| Gap | Consequence |
|---|---|
| **No persistent domain model.** Everything is JSON files; relationships are implicit in `findings.json`. | Relationships are not first-class (§5). No provenance fields — no publisher, jurisdiction, effective date, version (§6, §7). |
| **No version awareness or change detection.** | The North Star capability — regulatory change → organizational impact (§3) — is entirely absent. This is the single largest gap. |
| **No impact analysis** (§16). | |
| **No human review** (§17). | Findings are terminal; nothing improves. |
| **No API, no UI** (§9, §10, §23). | |
| **No education or competency documents in the corpus.** | The obligation lifecycle cannot be demonstrated end to end. Discovery 0003 recorded this; it now blocks the demo. |
| **No deployment artifacts** (§25). | |

## The decision: evolve, do not rewrite

The claim-with-verified-span is the crown jewel and everything keeps resting on
it. What changes is that it stops living in a JSON file and becomes a row in a
domain model with provenance, siblings, and relationships.

Concretely, `Claim` is generalised to **Entity**, and the implicit connections
inside `findings.json` become explicit **Relationship** rows carrying evidence,
confidence, provenance and review status.

## The vertical slice

One scenario, built completely, chosen because it exercises every layer:

> **Northstar Health — controlled substance waste witnessing.**
> A synthetic regulatory standard changes between version 1 and version 2. The
> organization has a policy, a procedure, an education module and an RN
> competency. Veris shows what changed, what it touches, where the gaps and
> conflicts are, on what evidence, and what a human should review.

Chosen deliberately over a broader build because it forces every capability at
once: two versions of one source (change detection), four organizational
document types across the full lifecycle (relationships), a planted conflict and
a planted gap (findings), and a reviewer decision (review loop).

**The existing 34-policy / 28-standard real corpus is retained** as the
large-corpus case, so the product is demonstrated at both depth and scale.

## Why synthetic data for the demo

The real corpus contains no education and no competency material, so the
lifecycle chain cannot be shown with it. The demo corpus is written for
"Northstar Health", clearly labelled synthetic, contains no PHI, and paraphrases
rather than reproduces copyrighted standards text (§19, and the licensing
constraint recorded in Decision 0002).

## Layering

```
web/            static SPA — landing, investigation, explorer, ask
  ↕ HTTP
veris/api.py    FastAPI — the product boundary is the API, not the UI (§23)
  ↕
veris/services  change detection · impact · review · ask
  ↕
veris/store.py  SQLite domain model — sources, documents, entities,
                relationships, evidence, findings, reviews, changes
  ↕
veris/ingest · claims · retrieve · adjudicate   (existing, preserved)
  ↕
veris/model.py  provider-agnostic inference
```

SQLite remains the store, per Decision 0003's deferral. Relationship types are
now stabilising, which was the stated trigger to revisit — but the corpus is
still ~10³ entities, three orders of magnitude below the threshold recorded
there, so the deferral holds. Relationships being first-class *rows* is what
§5 actually requires; a graph database is one possible implementation of that
and not yet a necessary one.

## Intelligence is precomputed, not computed per request

Decision 0005 established this for the clinician brief and it now becomes
system-wide. Analysis runs offline into the store; the API and UI read it. This
is what makes the product responsive, auditable, consistent between users, and —
critically — demonstrable without a live model, since the recording provider
replays a real analysis run.

## What would make this assessment wrong

- If relationship traversal starts exceeding three hops routinely, SQLite joins
  stop being the simple option and the graph-database deferral should end.
- If change detection turns out to need semantic diffing beyond the entity level,
  the version model here is too coarse.
