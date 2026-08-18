# VERIS SKILLS

This is the living registry of reusable capabilities discovered during development.

Claude Code may create, improve, consolidate, or remove skills when useful.

## Skill Creation Rule

Create a skill when a capability is:

* Repeated
* Specialized
* Reusable
* Valuable
* Easier to perform consistently when formalized

Do not create skills for trivial one-off tasks.

---

# Skill Format

## Skill: [Name]

**Purpose:**
What it accomplishes.

**When to use:**
When this skill should be applied.

**Inputs:**
Required information.

**Process:**
Reusable procedure.

**Outputs:**
Expected result.

**Constraints:**
Important limitations.

**Dependencies:**
Tools, libraries, or other skills.

**Last Updated:**
YYYY-MM-DD

---

# Active Skills

Three skills, each registered because the capability was used at least three
times and got it wrong at least once before it was written down.

---

## Skill: Evidence-Grounded Extraction

**Purpose:**
Turn a document into citable knowledge whose every statement can be verified
against the source, so that a weak or hostile model cannot introduce a
fabricated quotation.

**When to use:**
Any time text becomes structured knowledge — requirements, provisions, steps,
objectives, criteria.

**Inputs:**
A document, its declared type, and canonical text that has been frozen and
hashed.

**Process:**
1. Extract text once. Write it to a canonical file and hash it. Never rewrite it;
   spans cite into this file, so if the source changes its hash changes and every
   citation is stale by construction.
2. Parse using the document's **own published units** — Elements of Performance,
   numbered provisions, numbered steps. Not a chunker: chunk boundaries are an
   artifact of a retrieval implementation and change when the splitter changes.
3. Record `(char_start, char_end, quote)` for every unit.
4. **Verify by string equality** that `canonical[start:end] == quote`. Discard
   anything that fails. This is the trust primitive, and it is cheap.
5. Take the lifecycle role from the document's declared type. The organization
   already classified the document when it filed it; read that rather than
   inferring it.

**Outputs:**
Entities bound to verified spans, each carrying provenance and a role.

**Constraints:**
Requires no model, which is the point — extraction quality cannot degrade with
model quality. Tables and forms remain the weak case.

**Dependencies:**
`veris/extract.py`, `veris/pipeline.py`, `veris/store.py`.

**Last Updated:** 2026-08-16

---

## Skill: Scoped Absence Claims

**Purpose:**
State that something is missing without overstating it, so a gap finding is
defensible in front of the person who owns the document.

**When to use:**
Whenever the system is about to report that nothing covers, teaches, validates
or measures something. This is the product's most valuable output and its most
dangerous one.

**Inputs:**
The requirement, the corpus actually searched, and the retrieval results.

**Process:**
1. Never assert absolute absence. Every claim names the boundary it was
   established over: *"across the 6 organizational documents connected to
   Veris"*, never *"your organization has no…"*.
2. Before emitting, run a **disconfirmation pass** — search again using
   vocabulary generated from outside the requirement's own wording. A gap arises
   precisely when the covering document uses different words, so queries derived
   from the requirement cannot bridge it.
3. Instrument the disconfirmation pass and report how often it fires. A
   safety mechanism whose activation rate is never measured should be assumed
   inert — it was, for a whole run, and looked fine.
4. Cap confidence by how complete the supplied corpus is known to be.
5. On any failure — no model, parse error, transport error — **keep the
   candidate**. An infrastructure problem must never manufacture a false absence.

**Outputs:**
A finding carrying its scope, its confidence, and whether it survived
disconfirmation.

**Constraints:**
Cannot make an open-world absence claim sound; it makes the claim honest about
what it rests on.

**Dependencies:**
`veris/adjudicate.py`, `veris/analyze.py`, `veris/ask.py`.

**Last Updated:** 2026-08-16

---

## Skill: Conflict-Aware Retrieval

**Purpose:**
Get contradicting statements in front of judgment, which ordinary relevance
ranking systematically fails to do.

**When to use:**
Any retrieval whose purpose includes finding disagreement rather than support.

**Inputs:**
A requirement or question, and a pool of candidate provisions.

**Process:**
1. **Index the document title with the provision text.** A provision inherits
   its subject from the document it lives in; its own body frequently never
   states that subject.
2. **Rank documents first, then take their provisions.** A conflicting provision
   often shares *less* vocabulary with a requirement than a merely related one,
   because the conflict lives in what the texts say differently. Once a document
   is on topic, its other provisions are candidates regardless of their own
   scores.
3. Treat interrogatives and question-frame words as stopwords. "What", "how",
   "our" appear in every question and match any short passage containing them.
4. Expand short queries with vocabulary generated from outside the query.
5. Let a floor control cost, and let judgment decide relevance. A score
   threshold cannot separate weak-but-real from weak-noise.

**Outputs:**
A candidate set that contains the contradictions, not only the confirmations.

**Constraints:**
Measured on two corpora. Document-first ranking assumes documents are
topically coherent, which policy libraries generally are and reference manuals
generally are not.

**Dependencies:**
`veris/retrieve.py`, `veris/analyze.py`.

**Last Updated:** 2026-08-16
