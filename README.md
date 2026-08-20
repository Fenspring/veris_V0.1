# Veris

**The healthcare knowledge operating system.**

**Every hospital already has the knowledge.** It lives in regulations,
accreditation standards, policies, procedures, education modules and
competencies. Each document is written carefully. Each one is read alone. An
organization can be misaligned while every document in it is correct.

Veris holds no knowledge of its own and replaces none of it. It is the
intelligence layer that connects what the organization already owns, and reports
what those connections mean.

> **The hospital owns the knowledge. Veris owns the connections.**

That division is enforced in the product, not only stated. Every claim Veris
makes cites a byte-verified span of a document the organization supplied, and
the *Your Knowledge* view reports what has been connected, what has not, and
what Veris therefore cannot yet tell you.

```
A standard changes  →  what changed  →  what it touches  →  where the gaps
                       and conflicts are  →  on what evidence  →  who reviews it
```

Veris produces evidence-backed findings and recommends human review. It does not
determine regulatory applicability and does not replace compliance judgment.

---

## Quick start

```bash
make install          # virtualenv + dependencies
make seed             # ingest the demo corpus, detect changes, analyse
make eval             # verify the intelligence against ground truth
make serve            # http://127.0.0.1:8000
```

`make demo` runs all three in order.

No API key is required for the demo: the default model provider replays
recorded responses, so the analysis is real and reproducible without inference.

---

## What the demo shows

A synthetic accreditation standard — *Northstar Health, controlled substance
waste* — changes from version 1.0 to 2.0. The organization has a policy, a
procedure, an education module and an RN competency. Veris finds:

| | |
|---|---|
| **3 changes** | one requirement added, two modified — computed by diffing the versions, not described by a model |
| **4 potential conflicts** | including a procedure that lets the administering nurse witness their own waste when the new standard forbids it, and a 24-hour reconciliation window against a new 4-hour requirement |
| **3 potential gaps** | including the newly added quarterly audit requirement, which nothing in the organization's knowledge establishes |
| **0 false links** | two decoy documents, deliberately attractive to a keyword matcher, are correctly rejected |

Every finding names the evidence it rests on, the scope it was established over,
and whether it is a source fact, a Veris interpretation, a model inference, or a
human decision.

### The surfaces

- **Connections** — connect the systems you already use. Built entirely from
  connector registry metadata, so adding an integration adds no dashboard code.
  Vendors that are not implemented yet are listed, explained, and refuse to
  connect: showing a Connect button that silently does nothing would be worse
  than omitting the vendor.
- **Agents** — modular reasoning over everything connected. Agents depend on
  *capabilities* rather than vendors, so connecting a different system that
  supplies the same knowledge makes them run with no code change — and an agent
  that lacks something says which knowledge is missing and what its absence
  costs, rather than guessing.
- **Your Knowledge** — what the organization has connected, across the six roles
  knowledge plays: what requires, commits, operationalizes, teaches, validates
  and measures. Unfilled roles are shown with what their absence costs — the
  demo corpus has no audit tooling connected, so Veris says plainly that it
  cannot tell you whether any of this is working in practice. The thesis made
  measurable: what Veris adds is proportional to how much of what you already
  own has been connected.
- **Investigation** — a change, what it touches, findings ranked by severity,
  evidence on every claim, and a review decision.
- **Knowledge Explorer** — select any item and see its neighbourhood: what
  connects to it, why, and what is unresolved around it.
- **Ask Veris** — questions resolve against the same graph. Answers cite their
  sources *and carry the findings recorded against the knowledge they touch* —
  the part no single document contains.
- **Findings** — everything awaiting a human decision.

---

## Connect your systems

```bash
python -m veris.cli seed      # connects the demo LMS, policy system and standards feed
```

Veris ships with demo connectors so the whole product is demonstrable before any
vendor credential exists. They are labelled `demo data` everywhere and implement
the same interface as real connectors, passing the same contract tests.

**eCFR** — the federal Code of Federal Regulations — is implemented as real
code against a public, credential-free API, and marked `unverified`: it has
never been executed against the live service, because this build environment's
network policy blocks it. Veris says so in the registry, the API and the
Connection Center rather than calling it available because the code compiles.
`make verify-connector CONNECTOR=ecfr` runs it against the real service from a
networked machine and records exactly what passed — and that record, not the
connector's own declaration, is what promotes it.

Other integrations are declared in the catalogue — HealthStream, Relias,
Cornerstone, PolicyStat, PowerDMS, CMS, The Joint Commission and others — with
what each will need. Until one is implemented, **file import works today**: most
systems can produce an export even when their API is closed, and Veris maps the
columns for you and asks only about what it could not place.

Credentials go to the operating system keychain and nowhere else. There is no
encrypted-file fallback — see `SECURITY.md` for why that is deliberate.

## Desktop

`desktop/` holds a Tauri shell that supervises the core as a packaged sidecar,
so the customer installs one signed binary and no development tools. It is
**scaffolded but not built**: this environment lacks the platform toolchains.
See `desktop/README.md` and `docs/decisions/0007`.

## Configure

```bash
cp .env.example .env
```

Veris is model-agnostic. The knowledge never has to leave the building:

```bash
# On-premises (Ollama, vLLM, LM Studio, llama.cpp — anything OpenAI-compatible)
VERIS_MODEL_PROVIDER=ollama
VERIS_MODEL=llama3.1:8b
VERIS_MODEL_BASE_URL=http://localhost:11434/v1
```

```bash
# Or a hosted provider
VERIS_MODEL_PROVIDER=anthropic
VERIS_MODEL=claude-sonnet-5
ANTHROPIC_API_KEY=...
```

The pipeline assumes the weakest plausible model and compensates structurally:
extraction is deterministic and needs no model at all, tasks are small rather
than whole-corpus, model output is line-oriented rather than JSON, and every
quotation is verified byte-for-byte against its source before it can become
knowledge. A model that fabricates a citation produces a record that fails
verification and is discarded.

---

## Add your own documents

```bash
python -m veris.cli ingest path/to/policy.pdf
python -m veris.cli ingest path/to/folder/
```

PDF, DOCX, Markdown and plain text. Structure survives ingestion — headings and
section numbers become citations like *Medication Wasting Procedure §4 Document
the Waste*. Front matter, where present, supplies provenance:

```yaml
---
document_type: POLICY
title: "Controlled Substance Management Policy"
publisher: "Northstar Health"
authority: ORGANIZATIONAL
version: "4.2"
effective_date: 2024-11-15
owner: "Director of Pharmacy"
department: "Pharmacy"
---
```

Then detect changes and analyse:

```bash
python -m veris.cli changes                # finds version pairs automatically
python -m veris.cli analyze <document_id>
```

---

## API

The UI is a client of the API, not a privileged path into it.

```
GET  /api/v1/health
GET  /api/v1/overview
POST /api/v1/documents                     upload and ingest
GET  /api/v1/documents  ·  /{id}
GET  /api/v1/knowledge?q=&role=
GET  /api/v1/knowledge/{id}  ·  /{id}/relationships
GET  /api/v1/capabilities                  what Veris can and cannot assess
GET  /api/v1/connections/{id}/health  ·  /health/connections
GET  /api/v1/changes  ·  /{id}/impact
POST /api/v1/changes/detect
GET  /api/v1/findings  ·  /{id}
POST /api/v1/findings/{id}/reviews
POST /api/v1/relationships/{id}/reviews
POST /api/v1/intelligence/query
POST /api/v1/analysis
```

Interactive documentation at `/docs`.

---

## Test and evaluate

```bash
make test    # connector contract tests + migrations + retrieval regression
make verify-connector CONNECTOR=ecfr   # run a connector against the real thing
make eval    # 49 checks across ten intelligence capabilities
```

Every connector — mock or real — must pass the same 29 contract tests: read-only
by construction, idempotent re-sync, checkpointing, retry and backoff, per-record
failure isolation, credential safety, error redaction, tolerance of records
arriving out of order, capabilities drawn from a shared vocabulary, one health
shape for every vendor, and external identity preserved on every row without the
vendor's id ever becoming the Veris id.

The evaluation covers extraction, lifecycle roles, change detection, conflict
detection, gap detection, false positives on decoys, citation accuracy,
effective-date handling, source authority, and provenance labelling — including
a check that no relationship or finding is ever labelled `SOURCE_FACT`, because
inference must never be presented as what a source says.

Two suites, deliberately different in kind:

- `eval/northstar_cases.json` — planted findings on the synthetic demo corpus.
  Exercises the mechanics end to end. It cannot show the system works on
  knowledge nobody designed for it.
- `eval/gold.json` — 20 hand-labelled Joint Commission Elements of Performance
  against a real 34-policy library. Harder, and honest about its own
  limitations (see `docs/decisions/0002`).

---

## Deploy

```bash
docker build -t veris:0.1 .
docker run -p 8000:8000 --env-file .env -v veris-data:/app/data veris:0.1
```

The image runs unprivileged, exposes a health check at `/api/v1/health`, and
writes only to the mounted data volume.

Before exposing an instance:

1. Set `VERIS_API_TOKEN`. Without it every mutating route is open — correct for
   a local demo, wrong for anything else. `/api/v1/health` reports which posture
   is in effect.
2. Terminate TLS in front of the container.
3. Mount `/app/data` on durable storage. It holds the graph, the frozen
   canonical text every citation points into, and the original artifacts.

The schema creates itself on first connection and records its version in
`schema_meta`; migrations are additive and applied at startup.

**No PHI is required or expected.** Veris connects organizational knowledge —
policies, standards, education — not patient records. Nothing here constitutes
a claim of HIPAA compliance.

---

## Documentation

`ARCHITECTURE.md` · `CONNECTORS.md` · `DATA_MODEL.md` · `SECURITY.md` ·
`AGENTS.md` · `DEVELOPMENT.md` · `docs/connectors/`

## How it is built

```
desktop/        Tauri shell (scaffolded) — installer, keychain, supervises the core
web/            static workspace — no build step, one bundle for both deployments
  ↕ HTTP
veris/api.py    FastAPI — the product boundary
  ↕
veris/agents · ask · analyze · changes        intelligence services
  ↕
veris/store.py  domain model: sources, documents, entities, evidence,
                relationships, changes, findings, reviews, connections, records
  ↕
veris/sync.py · connectors/                   connection layer, read-only
  ↕
veris/model.py  provider-agnostic inference · credentials.py OS keychain
```

Relationships are rows, not inferences recomputed per request, which is what
lets them be reviewed, corrected, cited, and compared against their own past
state. Analysis runs offline into the store; the API and UI read it. Two people
asking the same question differently see the same findings.

### Reading the reasoning

`docs/decisions/` records architectural decisions with alternatives, evidence,
tradeoffs, and what would cause each to be reconsidered. `docs/discoveries/`
records product and technical insights found while building — including two
negative results, because a mechanism that looked correct and did nothing is
worth more written down than quietly fixed.

Start with `docs/discoveries/0001` (obligations have a lifecycle; failure
happens *between* artifacts) and `docs/discoveries/0002` (Veris's headline
output is a statement about what is *missing*, which inverts the engineering
problem).

---

## Status

Working MVP on a synthetic corpus and a partial real one. Known limits:

- The Joint Commission corpus contains no education or competency documents, so
  the full obligation lifecycle can only be demonstrated on the synthetic one.
- Coverage findings on the real corpus have been scored against a gold set the
  same agent authored. That result is documented as provisional for exactly
  that reason; independent review is the control that is missing.
- Scale is untested beyond ~10³ entities. SQLite is a deliberate deferral with
  a stated trigger to revisit, not a permanent choice.
