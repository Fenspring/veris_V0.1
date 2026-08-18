"""Relationship detection and impact analysis.

For each requirement in the current version of an authoritative source, find the
organizational knowledge that bears on it, classify the relationship, and record
what a change to that requirement would put at risk.

Retrieval nominates and judgment decides (CONSTITUTION §6). Candidate
organizational entities are proposed lexically — cheap, no embedding service —
and a model decides what each one actually is in relation to the requirement.
Relationships are written as MODEL_INFERENCE and start life PROPOSED, never
ACCEPTED: nothing here is authoritative until a person says so (§8, §17).

Severity is assigned from the *shape* of the situation rather than from the
model's enthusiasm:

- HIGH   a direct contradiction, or a newly added requirement nothing covers
- MEDIUM partial coverage, or coverage that predates a modified requirement
- LOW    contextual or already-aligned

A finding is never phrased as a compliance failure. It is a potential impact
that names its evidence and its scope, and asks a human to look (§32).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Model, ModelError
from .retrieve import BM25
from .store import Store

RELATION_MAP = {
    "CONFLICTS": ("POTENTIALLY_CONFLICTS_WITH", "POTENTIAL_CONFLICT"),
    "PARTIAL":   ("PARTIALLY_ADDRESSES", "REQUIRES_HUMAN_REVIEW"),
    "ALIGNED":   ("DIRECTLY_ADDRESSES", "LIKELY_ALIGNED"),
    "UNRELATED": (None, None),
}

# Which department should look at a finding, by the document type it lands on.
REVIEWER_BY_TYPE = {
    "POLICY": "Policy Owner",
    "PROCEDURE": "Nursing / Procedure Owner",
    "EDUCATION": "Nursing Professional Development",
    "COMPETENCY": "Nursing Professional Development",
    "STANDARD": "Compliance",
}

SYSTEM = (
    "You analyse whether a healthcare organization's own knowledge satisfies an "
    "external requirement, and what a change to that requirement puts at risk.\n\n"
    "Judge only what the supplied text actually says. Sharing vocabulary with the "
    "requirement is not the same as addressing it; addressing it in different "
    "words counts as addressing it.\n\n"
    "CONFLICTS means the organizational text instructs something the requirement "
    "forbids, or states a different threshold, timeframe, or condition.\n"
    "PARTIAL means it addresses the subject but leaves part of the requirement "
    "unmet.\nALIGNED means it satisfies the requirement.\n"
    "UNRELATED means it is about a different subject.\n\n"
    "Prefer PARTIAL over CONFLICTS unless the texts genuinely cannot both be "
    "followed. Overstating a conflict is worse than reporting one cautiously."
)

TEMPLATE = """REQUIREMENT [{locator}] — {source_title} version {version}{effective}
{statement}
{change_block}
ORGANIZATIONAL KNOWLEDGE:
{candidates}

For each item above, reply with one line:
<id>: <CONFLICTS|PARTIAL|ALIGNED|UNRELATED> | <HIGH|MEDIUM|LOW> | one-sentence reason

Then one final line:
GAP: what this requirement demands that none of the items above provides, or NONE"""


@dataclass
class _Candidate:
    """Adapter giving store rows the interface BM25 expects."""
    claim_id: str
    locator: str
    quote: str
    row: dict


def _candidates(store: Store) -> list[_Candidate]:
    rows = store.q("""
        SELECT e.*, d.title AS document_title, d.document_type, d.department, d.owner
        FROM entities e JOIN documents d ON d.id = e.document_id
        WHERE e.role != 'REQUIRES'""")
    # The locator already carries the document title; prefixing it again reads
    # as "Policy — Policy §3".
    return [_Candidate(r["id"], r["locator"], r["statement"], r) for r in rows]


def retrieve_candidates(bm: BM25, pool: list[_Candidate], query: str,
                        top_docs: int = 3, limit: int = 10) -> list[_Candidate]:
    """Rank documents first, then take their provisions.

    Ranking provisions directly misses conflicts. A conflicting provision often
    shares *less* vocabulary with a requirement than a merely related one, because
    a conflict is a statement about the same subject that says the opposite — not
    a close lexical match. Measured on the demo corpus, the procedure step that
    contradicts the new witnessing rule ranked tenth, below an unrelated sharps
    disposal step.

    Relevance is a property of documents first and provisions second. Once a
    document is clearly on topic, its other provisions are candidates regardless
    of their own scores, which is what brings a contradicting sibling into view.
    """
    scored = bm.search(query, max(limit * 3, 20))
    best_by_doc: dict[str, float] = {}
    for cand, score in scored:
        doc = cand.row["document_id"]
        best_by_doc[doc] = max(best_by_doc.get(doc, 0.0), score)

    chosen_docs = sorted(best_by_doc, key=lambda d: -best_by_doc[d])[:top_docs]
    by_doc: dict[str, list[_Candidate]] = {}
    for c in pool:
        by_doc.setdefault(c.row["document_id"], []).append(c)

    ordered: list[_Candidate] = []
    seen: set[str] = set()
    for doc in chosen_docs:
        for c in by_doc.get(doc, []):
            if c.claim_id not in seen:
                seen.add(c.claim_id)
                ordered.append(c)
    # Then any remaining high scorers from documents that did not make the cut.
    for cand, _ in scored:
        if cand.claim_id not in seen:
            seen.add(cand.claim_id)
            ordered.append(cand)
    return ordered[:limit]


def parse_verdicts(raw: str) -> tuple[dict[str, tuple[str, str, str]], str]:
    """Tolerant line parser. Small local models drop formatting long before they
    drop meaning, so anything resembling `id: RELATION | SEVERITY | reason` is
    accepted, and a missing severity defaults rather than failing the line."""
    verdicts: dict[str, tuple[str, str, str]] = {}
    gap = ""
    for line in raw.splitlines():
        line = line.strip().lstrip("-*# ").strip()
        if not line:
            continue
        if line.upper().startswith("GAP:"):
            value = line.split(":", 1)[1].strip()
            gap = "" if value.upper() in ("NONE", "N/A", "") else value
            continue
        m = re.match(r"\[?(O\d+)\]?\s*[:.\-]\s*(.+)", line, re.I)
        if not m:
            continue
        parts = [p.strip() for p in m.group(2).split("|")]
        relation = next((r for r in RELATION_MAP if r in parts[0].upper()), None)
        if not relation:
            continue
        severity = next((s for s in ("HIGH", "MEDIUM", "LOW")
                         if len(parts) > 1 and s in parts[1].upper()), "MEDIUM")
        reason = parts[2] if len(parts) > 2 else ""
        verdicts[m.group(1).upper()] = (relation, severity, reason)
    return verdicts, gap


def _change_block(store: Store, entity_id: str) -> tuple[str, dict | None]:
    """If this requirement changed, show the model what changed and how."""
    chg = store.one(
        "SELECT * FROM changes WHERE to_entity_id = ? AND change_type != 'UNCHANGED'",
        (entity_id,))
    if not chg:
        return "", None
    import json
    detail = json.loads(chg["detail"] or "{}")
    lines = [f"\nTHIS REQUIREMENT {chg['change_type']} IN THIS VERSION."]
    if detail.get("removed"):
        lines.append("The previous version said: " + "; ".join(detail["removed"][:4]))
    if detail.get("added"):
        lines.append("This version adds: " + "; ".join(detail["added"][:4]))
    lines.append("")
    return "\n".join(lines), chg


def analyze_requirement(store: Store, requirement: dict, bm: BM25,
                        pool: list[_Candidate], model: Model,
                        top_k: int = 10) -> dict:
    """One model call produces the relationships and the findings for one
    requirement, because they are the same judgment seen from two angles."""
    hits = retrieve_candidates(bm, pool, requirement["statement"], limit=top_k)
    ids = {f"O{i+1}": c for i, c in enumerate(hits)}

    rendered = []
    for key, c in ids.items():
        r = c.row
        meta = f"{r['document_type']}"
        if r.get("department"):
            meta += f", {r['department']}"
        body = re.sub(r"[ \t]+", " ", c.quote)
        rendered.append(f"[{key}] {c.locator}  ({meta})\n{body}")

    doc = store.one("""SELECT d.*, s.title AS source_title, s.version AS source_version,
                              s.effective_date AS source_effective
                       FROM documents d JOIN sources s ON s.id = d.source_id
                       WHERE d.id = ?""", (requirement["document_id"],))
    change_block, change = _change_block(store, requirement["id"])

    prompt = TEMPLATE.format(
        locator=requirement["locator"],
        source_title=doc["source_title"],
        version=doc["source_version"] or "-",
        effective=f", effective {doc['source_effective']}" if doc["source_effective"] else "",
        statement=re.sub(r"[ \t]+", " ", requirement["statement"]),
        change_block=change_block,
        candidates="\n\n".join(rendered) or "(none retrieved)",
    )

    raw = model.complete(SYSTEM, prompt, max_tokens=700)
    verdicts, gap = parse_verdicts(raw)

    scope = (f"{len({c.row['document_id'] for c in pool})} organizational documents "
             f"connected to Veris")
    created = {"relationships": 0, "findings": 0}

    for key, (relation, severity, reason) in verdicts.items():
        cand = ids.get(key)
        if not cand:
            continue  # a verdict for an item never shown cannot be resolved
        rel_type, finding_type = RELATION_MAP[relation]
        if not rel_type:
            continue

        rel_id = store.add_relationship(
            from_entity_id=requirement["id"], to_entity_id=cand.claim_id,
            relationship_type=rel_type,
            confidence={"HIGH": 0.85, "MEDIUM": 0.6, "LOW": 0.4}[severity],
            rationale=reason, provenance_class="MODEL_INFERENCE",
            created_by=model.info.name, status="PROPOSED",
            evidence_ids=[e for e in (requirement.get("evidence_id"),
                                      cand.row.get("evidence_id")) if e],
        )
        created["relationships"] += 1

        # A finding is warranted when something is at risk: a contradiction, or
        # partial coverage of a requirement that has just changed. Alignment on
        # an unchanged requirement is a relationship, not a finding — reporting
        # it would bury the reader in confirmations.
        if finding_type == "POTENTIAL_CONFLICT" or (change and relation == "PARTIAL"):
            ftype = ("POTENTIAL_CONFLICT" if relation == "CONFLICTS"
                     else "REQUIRES_HUMAN_REVIEW")
            sev = "HIGH" if relation == "CONFLICTS" else (
                "MEDIUM" if change else "LOW")
            store.add_finding(
                finding_type=ftype, severity=sev,
                title=(f"{cand.row['document_title']} may conflict with "
                       f"{requirement['locator']}" if relation == "CONFLICTS"
                       else f"{cand.row['document_title']} may need review after "
                            f"{requirement['locator']} changed"),
                statement=reason,
                subject_entity_id=requirement["id"],
                change_id=change["id"] if change else None,
                confidence={"HIGH": 0.85, "MEDIUM": 0.6, "LOW": 0.4}[severity],
                provenance_class="MODEL_INFERENCE", scope=scope,
                recommended_reviewer=REVIEWER_BY_TYPE.get(
                    cand.row["document_type"], "Compliance"),
                evidence=[(e, r) for e, r in (
                    (requirement.get("evidence_id"), "requirement"),
                    (cand.row.get("evidence_id"), "organizational")) if e],
                entity_ids=[requirement["id"], cand.claim_id],
            )
            created["findings"] += 1

    # An absence claim: nothing among the retrieved organizational knowledge
    # provides what the requirement demands. Scoped, never absolute (§7).
    if gap:
        newly_added = bool(change and change["change_type"] == "ADDED")
        store.add_finding(
            finding_type="POTENTIAL_GAP",
            severity="HIGH" if newly_added else "MEDIUM",
            title=f"No connected knowledge found for {requirement['locator']}"
                  if newly_added else
                  f"{requirement['locator']} may not be fully covered",
            statement=gap, missing=gap,
            subject_entity_id=requirement["id"],
            change_id=change["id"] if change else None,
            confidence=0.6, provenance_class="MODEL_INFERENCE", scope=scope,
            recommended_reviewer="Compliance",
            evidence=[(requirement["evidence_id"], "requirement")]
                     if requirement.get("evidence_id") else [],
            entity_ids=[requirement["id"]],
        )
        created["findings"] += 1

    store.commit()
    return created


def analyze_source_version(store: Store, document_id: str, model: Model) -> dict:
    """Analyse every requirement in one version of a source."""
    pool = _candidates(store)
    if not pool:
        return {"relationships": 0, "findings": 0, "requirements": 0, "skipped": 0}
    bm = BM25(pool)
    reqs = store.q("SELECT * FROM entities WHERE document_id = ? AND role = 'REQUIRES'"
                   " ORDER BY locator", (document_id,))

    totals = {"relationships": 0, "findings": 0, "requirements": 0, "skipped": 0}
    for r in reqs:
        try:
            got = analyze_requirement(store, r, bm, pool, model)
        except ModelError:
            totals["skipped"] += 1
            continue
        totals["relationships"] += got["relationships"]
        totals["findings"] += got["findings"]
        totals["requirements"] += 1
    store.log("analysis_complete", f"{document_id}: {totals}")
    store.commit()
    return totals


def impact_of_change(store: Store, change_id: str) -> dict:
    """Everything a single change touches: the requirement, its relationships,
    and the findings raised against it. This is the Investigation Workspace's
    query (§9, §16)."""
    change = store.one("SELECT * FROM changes WHERE id = ?", (change_id,))
    if not change:
        return {}
    entity_id = change["to_entity_id"] or change["from_entity_id"]
    related = store.neighbourhood(entity_id) if entity_id else []
    findings = store.q("""
        SELECT f.*, e.locator AS subject_locator
        FROM findings f LEFT JOIN entities e ON e.id = f.subject_entity_id
        WHERE f.change_id = ?
        ORDER BY CASE f.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END""",
        (change_id,))
    affected_docs = {r["document_title"]: r["document_type"] for r in related}
    return {
        "change": change,
        "requirement": store.entity(entity_id) if entity_id else None,
        "related": related,
        "findings": findings,
        "affected_documents": affected_docs,
    }
