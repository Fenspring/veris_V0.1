"""Ask Veris — natural language over the knowledge graph.

This is a way into the graph, not a second system beside it. A question resolves
to entities that already exist, and the answer is assembled from their
relationships and the findings already recorded against them. Nothing is
computed here that a reviewer could not also reach by clicking through the
Knowledge Explorer, and the findings surfaced are the same rows the Investigation
Workspace shows.

That constraint is what keeps this from being a chatbot bolted onto a database.
A retrieval-and-summarise assistant can only ever restate what some document
says. This can answer "what does our organization say about X, and what is wrong
with it" — because the second half lives in the graph, not in any document.

Grounding: the model writes prose only over extracts it is handed and cites them
by id. A citation that does not resolve is stripped and flagged, so a fabricated
reference cannot reach the reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analyze import _Candidate
from .changes import superseded_documents
from .model import Model
from .retrieve import BM25
from .store import Store

ROLE_SECTIONS = [
    ("REQUIRES", "What we are required to do"),
    ("COMMITS", "What our policy says"),
    ("OPERATIONALIZES", "How it is performed"),
    ("TEACHES", "What we teach"),
    ("VALIDATES", "How competency is validated"),
    ("MEASURES", "How it is measured"),
]

EXPAND_SYSTEM = (
    "You turn a question into search terms for a hospital's own policies, "
    "procedures, education, competencies and the accreditation standards it is "
    "held to."
)
EXPAND_TEMPLATE = """QUESTION: {question}

List 5 short search phrases, one per line, no numbering or commentary. Include
the clinical or operational terms, the likely document title, and the formal
wording an accreditation standard would use for the same subject."""

ANSWER_SYSTEM = (
    "You answer a question for a healthcare professional using only the numbered "
    "extracts provided.\n\n"
    "Every sentence must be supported by an extract, cited as [E2]. Never state "
    "anything the extracts do not say. If the extracts do not answer part of the "
    "question, say so plainly rather than filling the gap.\n\n"
    "Write 2-4 sentences of plain prose. No headings, no bullet points. Do not "
    "mention gaps or conflicts — those are reported separately."
)
ANSWER_TEMPLATE = """QUESTION: {question}

EXTRACTS:
{extracts}

Write the answer now, citing extract ids."""


@dataclass
class Answer:
    question: str
    summary: str = ""
    sections: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    scope: str = ""
    flags: list[str] = field(default_factory=list)


def _pool(store: Store) -> list[_Candidate]:
    """Current knowledge only. Superseded versions stay in the graph so changes
    can be explained against what they replaced, but they are never answered
    with — a question about the rule must return the rule in force."""
    stale = superseded_documents(store)
    rows = store.q("""
        SELECT e.*, d.title AS document_title, d.document_type, d.department,
               d.owner, d.version AS document_version, s.publisher, s.authority,
               s.effective_date
        FROM entities e
        JOIN documents d ON d.id = e.document_id
        JOIN sources   s ON s.id = d.source_id
        WHERE d.status = 'ACTIVE'""")
    return [_Candidate(r["id"], r["locator"], r["statement"], r)
            for r in rows if r["document_id"] not in stale]


def _expand(question: str, model: Model | None) -> list[str]:
    """Questions are short and carry little vocabulary; the terms have to come
    from somewhere other than the question."""
    queries = [question]
    if model is None:
        return queries
    try:
        raw = model.complete(EXPAND_SYSTEM,
                             EXPAND_TEMPLATE.format(question=question), max_tokens=120)
        for line in raw.splitlines():
            line = line.strip().lstrip("-*0123456789. ").strip()
            if 3 < len(line) < 120:
                queries.append(line)
    except Exception:
        pass
    return queries[:6]


def resolve(store: Store, question: str, model: Model | None,
            per_role: int = 4) -> dict[str, list[_Candidate]]:
    """Resolve a question to entities, grouped by what each one does."""
    pool = _pool(store)
    if not pool:
        return {}
    queries = _expand(question, model)

    by_role: dict[str, list[_Candidate]] = {}
    for role, _ in ROLE_SECTIONS:
        subset = [c for c in pool if c.row["role"] == role]
        if not subset:
            by_role[role] = []
            continue
        bm = BM25(subset)
        best: dict[str, tuple[_Candidate, float]] = {}
        for q in queries:
            for c, score in bm.search(q, per_role):
                if c.claim_id not in best or score > best[c.claim_id][1]:
                    best[c.claim_id] = (c, score)
        ranked = sorted(best.values(), key=lambda x: -x[1])[:per_role]
        # Relative floor only: this surface shows sections, so an off-topic item
        # in a collapsed section is cheap, while wrongly emptying a section
        # tells the reader the organization has nothing on the subject.
        if ranked:
            cut = ranked[0][1] * 0.45
            by_role[role] = [c for c, s in ranked if s >= cut]
        else:
            by_role[role] = []
    return by_role


def findings_for(store: Store, entity_ids: list[str]) -> list[dict]:
    """Findings already recorded against these entities.

    This is the part no document contains. It is read from the graph, not
    derived at question time, so the reader sees exactly what a reviewer sees.
    """
    if not entity_ids:
        return []
    marks = ",".join("?" for _ in entity_ids)
    return store.q(f"""
        SELECT DISTINCT f.id, f.finding_type, f.severity, f.title, f.statement,
               f.missing, f.status, f.scope, f.recommended_reviewer,
               f.provenance_class, f.confidence
        FROM findings f
        JOIN finding_entities fe ON fe.finding_id = f.id
        WHERE fe.entity_id IN ({marks}) AND f.status != 'REJECTED'
        ORDER BY CASE f.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END
        """, tuple(entity_ids))


def _summarise(question: str, cands: list[_Candidate], model: Model
               ) -> tuple[str, list[str]]:
    extracts, ids = [], {}
    for i, c in enumerate(cands):
        key = f"E{i+1}"
        ids[key] = c
        body = re.sub(r"[ \t]+", " ", c.quote)
        extracts.append(f"[{key}] {c.locator}\n{body}")
    raw = model.complete(
        ANSWER_SYSTEM,
        ANSWER_TEMPLATE.format(question=question, extracts="\n\n".join(extracts)),
        max_tokens=400)
    flags: list[str] = []
    cited = set(re.findall(r"\[E(\d+)\]", raw))
    unresolved = [c for c in cited if f"E{c}" not in ids]
    for u in unresolved:
        raw = raw.replace(f"[E{u}]", "")
    if unresolved:
        flags.append(f"dropped_{len(unresolved)}_unresolvable_citations")
    if not cited:
        flags.append("answer_carried_no_citations")
    return raw.strip(), flags


def _interleave(by_role: dict[str, list[_Candidate]], limit: int) -> list[_Candidate]:
    """Round-robin across sections rather than taking the first N in order.

    Taking them in section order let requirements and policy fill the whole
    budget, so a question explicitly about education was summarised from
    extracts containing no education. The answer must be able to draw on every
    kind of knowledge the question actually matched.
    """
    picked: list[_Candidate] = []
    depth = 0
    while len(picked) < limit:
        added = False
        for role, _ in ROLE_SECTIONS:
            items = by_role.get(role, [])
            if depth < len(items) and len(picked) < limit:
                picked.append(items[depth])
                added = True
        if not added:
            break
        depth += 1
    return picked


def ask(store: Store, question: str, model: Model | None) -> Answer:
    by_role = resolve(store, question, model)
    answer = Answer(question=question)

    matched: list[_Candidate] = []
    for role, label in ROLE_SECTIONS:
        cands = by_role.get(role, [])
        matched.extend(cands)
        answer.sections.append({
            "role": role,
            "label": label,
            "items": [{
                "entity_id": c.claim_id,
                "locator": c.locator,
                "statement": c.quote,
                "document_title": c.row["document_title"],
                "document_type": c.row["document_type"],
                "department": c.row.get("department"),
                "publisher": c.row.get("publisher"),
                "authority": c.row.get("authority"),
            } for c in cands],
            "absence_note": "" if cands else
                f"Nothing connected to Veris plays this role for this question.",
        })

    answer.findings = findings_for(store, [c.claim_id for c in matched])
    docs = store.q("SELECT COUNT(*) n FROM documents")[0]["n"]
    answer.scope = f"{docs} documents connected to Veris"

    if matched and model is not None:
        try:
            answer.summary, answer.flags = _summarise(
                question, _interleave(by_role, 6), model)
        except Exception:
            answer.summary = ""
            answer.flags = ["summary_unavailable"]
    if not matched:
        answer.summary = ("No knowledge connected to Veris addresses this question. "
                          "This describes what Veris has been given, not what the "
                          "organization possesses.")
    return answer
