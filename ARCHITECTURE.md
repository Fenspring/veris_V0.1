# Veris architecture

```
        Systems the hospital already runs
   LMS · Policy management · Standards sources · Exports
                        │  read-only
                        ▼
              ┌───────────────────┐
              │  Connector layer  │   one interface, vendor code confined inside
              └─────────┬─────────┘
                        ▼
                   Sync engine        discovery · checkpointing · backoff · audit
                        ▼
              ┌───────────────────┐
              │  Normalized model │   knowledge → documents · facts → records
              └─────────┬─────────┘
                        ▼
                 Knowledge graph      entities · evidence · relationships · changes
                        ▼
                     Agents           change · alignment · drift · gaps · readiness
                        ▼
        API  ──▶  Dashboard · Ask Veris · third-party systems
```

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Connectors | `veris/connectors/` | Speak to external systems. Read-only. |
| Sync | `veris/sync.py` | Move records in reliably and resumably. |
| Extraction | `veris/extract.py`, `veris/pipeline.py` | Turn documents into verified-span entities. |
| Graph | `veris/store.py` | Sources, documents, evidence, entities, relationships, changes, findings, reviews. |
| Change | `veris/changes.py` | Diff two versions of a source. Computed, not inferred. |
| Analysis | `veris/analyze.py` | Relationships and findings, with provenance. |
| Agents | `veris/agents.py` | Modular reasoning over everything connected. |
| Intelligence | `veris/ask.py` | Natural language over the same graph. |
| Model | `veris/model.py` | Provider-agnostic inference. No vendor SDK. |
| API | `veris/api.py` | The product boundary. |
| UI | `web/` | A client of the API, not a privileged path into it. |

## Invariants

These hold everywhere, and most of the sharp edges in this codebase exist to
protect them.

**1. Every claim resolves to readable text.** An entity's statement must match
its cited span byte-for-byte, or it is discarded before it becomes knowledge.
This is why operational facts are kept out of the evidence tables: "12,842
people completed this" is true, but no document says it.

**2. Inference is never presented as fact.** Every relationship and finding
carries `SOURCE_FACT`, `VERIS_INTERPRETATION`, `MODEL_INFERENCE`, or
`HUMAN_REVIEW`. The evaluation fails if any relationship or finding is labelled
`SOURCE_FACT`.

**3. Absence is always scoped.** A gap finding names the corpus it was
established over. "Nothing in the 6 connected documents addresses this" is a
statement Veris can defend; "you have no policy for this" is not.

**4. Veris never writes to a customer system.** The connector interface has no
write method and the registry rejects a connector that declares one.

**5. Nothing degrades silently.** Where a mechanism can fail — retrieval floors,
relevance judgment, disconfirmation — failure keeps the candidate and records a
flag, because a quiet failure manufactures a false absence.

## Deployment shapes

The core is one process with a SQLite file. That makes all four shapes the same
software:

- **Local** — desktop app, everything on the workstation.
- **On-prem** — container inside the hospital network, local model.
- **Hybrid** — connectors on-prem, graph and dashboard hosted.
- **Cloud** — everything hosted, customer-chosen model provider.

The customer controls where processing happens by choosing the model provider;
`veris/model.py` makes a local model a configuration change rather than a
different product.

## Reading the reasoning

`docs/decisions/` — why each significant choice was made, what was rejected, and
what would cause reconsideration. `docs/discoveries/` — what was learned while
building, including two negative results worth more written down than quietly
fixed.
