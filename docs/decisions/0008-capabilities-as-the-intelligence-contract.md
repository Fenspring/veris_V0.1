# Decision 0008 — Capabilities Are the Contract Between Connectors and Intelligence

**Date:** 2026-08-20
**Status:** Accepted
**Driver:** Agents were gated on connector *categories* (`requires=("POLICY", "LMS")`).
That is a statement about vendors, and the product's claims are statements about
knowledge.

---

## The problem the category gate hides

An agent that requires an "LMS" is really asking a different question: *can I
find out when the training was last revised?* Those two questions come apart
immediately:

- A hospital connects an LMS that returns a course catalogue but no completion
  records. Category satisfied; the agent runs; it reports on training completion
  it cannot see.
- A hospital has no LMS but exports a course list to CSV. Category unsatisfied;
  the agent refuses; the customer is told to connect a system whose data they
  already gave us.
- A hospital drags in forty policy PDFs and connects nothing. Veris quotes those
  policies on screen while telling them it cannot read their policies.

Each of these is the product lying about itself in a different direction. The
category is a proxy, and every proxy eventually disagrees with the thing it
stands for.

## The decision

**A connector declares capabilities from a shared, validated vocabulary. Agents
declare the capabilities they require. Nothing in the intelligence layer names a
connector or a category.**

Each capability carries two sentences, not one:

```python
Capability(
    "completion_records", "Training completions", "operational",
    enables="Who completed what, and what is overdue.",
    without_it="Veris cannot tell you whether required training was actually done.")
```

`without_it` is the load-bearing half. Discovery 0002 established that Veris's
headline output is a statement about what is *missing*; the same inversion
applies one level up, to the product's account of itself. "What Veris cannot
assess" is a first-class output, and it has to come from the same declaration
that powers "what Veris can assess" or the two will drift apart.

## Declared is not delivered

`ConnectorInfo.capabilities` is a claim about a vendor. What matters to an agent
is what *this customer's connection actually produced*, which Veris computes
from the rows it holds.

The gap between the two is reported as `degraded_capabilities`. It is a distinct
failure mode from being down, and the more dangerous one: a connection can be
reachable, authenticated, green, and quietly no longer returning completions.
Only the intelligence layer notices, because only it knows what it was depending
on.

Uploaded documents count toward capabilities alongside connections. A capability
model that counted only API connections would produce the third failure above.

## What this buys

- Connecting a different vendor that supplies the same knowledge makes agents
  run with no code change. The catalogue can grow to fifty connectors without
  touching a line of reasoning.
- An agent that cannot run says which knowledge is missing and what its absence
  costs, in the customer's language. Their next action is to connect something;
  "requirements not met" does not tell them which.
- `GET /api/v1/capabilities` pairs every absence with the connectors that could
  fill it, so "Veris cannot tell you X" is always followed by "connect Y and it
  can".
- Overclaiming becomes visible. Applying the vocabulary caught the demo policy
  connector declaring `acknowledgments` and policy text while returning neither.

## Costs and what we accept

- The vocabulary is a coupling point. Adding a capability touches the vocabulary,
  the connectors that provide it, and the agents that want it. That is the
  intended cost: it is the same list the UI renders and the same list the
  contract tests check, so it cannot rot silently.
- Capability delivery is inferred from stored rows rather than declared by the
  connector. A connector that legitimately returns an empty result set — a
  hospital with no acknowledgement records at all — is indistinguishable from one
  that broke. We accept this: both mean Veris cannot assess the thing, which is
  the sentence the product needs to say either way.

## What would cause this to be reconsidered

- If capabilities start needing parameters ("policy text, but only for the
  Pharmacy department"), the flat vocabulary is the wrong shape and this becomes
  a scoped declaration.
- If a customer needs an agent to run on partial capability — degraded but
  useful — the binary gate becomes a confidence input instead.

---

## Related: external identity (§7)

Decided together because they answer the same question — *where did this come
from?* Every normalized row carries `source_system`, `source_record_type`,
`source_id`, `source_updated_at` and `imported_at` as columns.

The Veris id is minted in Veris's own namespace and is never the vendor's key.
It is *derived* from the vendor's key so a re-sync updates a row rather than
duplicating it, but derivation is not adoption: two systems that number their
people identically produce two rows, a vendor that renumbers does not rewrite
Veris's history, and nothing downstream can parse a Veris id back into a vendor
id and act on it.

A record arriving with no identifier of its own is rejected and isolated as a
failed record. There is no correct guess — the next sync would either duplicate
it or overwrite something else.
