"""The clinician-facing brief.

A question comes in; a composed answer goes out. What makes this not a chatbot:

- The answer is assembled from claims that were already connected and already
  adjudicated offline. Two clinicians who phrase the same question differently
  get the same underlying findings, because the intelligence is precomputed and
  the question is only a way in.
- It is organised by what each source *does* — what we commit to, how it is
  actually performed, what regulators require — rather than by relevance rank.
  A ranked list of passages is a search result; this is a briefing.
- It reports the sections that are **empty**. If nothing teaching this topic has
  been connected, the brief says so. A retrieval system silently omits what it
  does not have, which is exactly how a clinician comes to believe the education
  simply does not exist.
- It carries regulatory findings that no document contains, because they exist
  only in the relation between the standards and the policies.

The model writes prose only over claims it is handed, and may cite nothing else.
Citations are resolved against the claim store, so a fabricated one is dropped
rather than displayed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .claims import POLICY_ROLES, Claim, load_claims
from .model import Model
from .retrieve import BM25

# Ordered as a clinician reads: our rule, how to do it, what's required of us.
SECTIONS = [
    ("COMMITS", "What our policy requires",
     "No policy document supplied to Veris states a rule on this topic."),
    ("OPERATIONALIZES", "How it is performed",
     "No step-level procedure has been connected for this topic."),
    ("REQUIRES", "What regulators and accreditors require",
     "No accreditation standard connected to Veris addresses this topic."),
    ("TEACHES", "Education",
     "No education or training material has been connected to Veris."),
    ("VALIDATES", "Competency validation",
     "No competency or skills-validation record has been connected to Veris."),
]

EXPAND_SYSTEM = (
    "You turn a clinician's question into search terms for a hospital's own "
    "policy library and accreditation standards."
)
EXPAND_TEMPLATE = """QUESTION: {question}

List 5 short search phrases, one per line, no numbering or commentary.
Include the clinical terms, the likely policy title, and the formal terms an
accreditation standard would use for the same subject."""

SUMMARY_SYSTEM = (
    "You brief a clinician using only the numbered source extracts provided.\n\n"
    "Every sentence must be supported by an extract, and you cite the extract "
    "id in square brackets, e.g. [S2]. Never state anything the extracts do not "
    "say. If the extracts do not answer part of the question, say so plainly "
    "rather than filling the gap.\n\n"
    "Write 3-5 sentences of continuous prose for a clinician at the bedside: "
    "what the rule is and what they must do. No headings, no bullet points."
)
SUMMARY_TEMPLATE = """QUESTION: {question}

SOURCE EXTRACTS:
{extracts}

Write the summary now, citing extract ids."""


@dataclass
class Section:
    role: str
    label: str
    claims: list[dict]
    absence_note: str = ""


@dataclass
class Brief:
    question: str
    summary: str
    sections: list[Section]
    regulatory: list[dict] = field(default_factory=list)
    scope: str = ""
    flags: list[str] = field(default_factory=list)


def expand(question: str, model: Model | None) -> list[str]:
    """Clinician questions are short and carry little vocabulary, so retrieval
    on the raw question is weak. Same lesson as Discovery 0004: the query terms
    have to come from somewhere other than the question."""
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


def gather(question: str, claims: list[Claim], model: Model | None,
           per_role: int = 4) -> dict[str, list[Claim]]:
    queries = expand(question, model)
    found: dict[str, dict[str, tuple[Claim, float]]] = {}
    for role, _, _ in SECTIONS:
        pool = [c for c in claims if c.role == role]
        if not pool:
            found[role] = {}
            continue
        bm = BM25(pool)
        hits: dict[str, tuple[Claim, float]] = {}
        for q in queries:
            for c, s in bm.search(q, per_role):
                # Keep a claim's best score across all query formulations.
                if c.claim_id not in hits or s > hits[c.claim_id][1]:
                    hits[c.claim_id] = (c, s)
        found[role] = hits
    return {
        role: [c for c, _ in sorted(h.values(), key=lambda x: -x[1])[:per_role]]
        for role, h in found.items()
    }


def summarise(question: str, claims: list[Claim], model: Model) -> tuple[str, list[str]]:
    parts, ids = [], {}
    for i, c in enumerate(claims):
        sid = f"S{i+1}"
        ids[sid] = c
        body = re.sub(r"[ \t]+", " ", c.quote)
        parts.append(f"[{sid}] {c.locator}\n{body}")
    raw = model.complete(
        SUMMARY_SYSTEM,
        SUMMARY_TEMPLATE.format(question=question, extracts="\n\n".join(parts)),
        max_tokens=400,
    )
    flags = []
    cited = set(re.findall(r"\[S(\d+)\]", raw))
    unresolved = [c for c in cited if f"S{c}" not in ids]
    if unresolved:
        # A citation to an extract that was never supplied is fabricated.
        for u in unresolved:
            raw = raw.replace(f"[S{u}]", "")
        flags.append(f"dropped_{len(unresolved)}_fabricated_citations")
    if not cited:
        flags.append("summary_carried_no_citations")
    return raw.strip(), flags


def build(question: str, data: Path, model: Model) -> Brief:
    claims = load_claims(data)
    by_role = gather(question, claims, model)

    sections, cited_claims = [], []
    for role, label, absence in SECTIONS:
        got = by_role.get(role, [])
        cited_claims.extend(got)
        sections.append(Section(
            role=role, label=label,
            claims=[{"locator": c.locator, "quote": c.quote, "claim_id": c.claim_id}
                    for c in got],
            absence_note="" if got else absence,
        ))

    # Regulatory intelligence: precomputed findings for the standards this
    # question touched. These exist in no document — only in the relation
    # between the standards and the policies.
    reg = []
    fpath = data / "findings.json"
    if fpath.exists():
        touched = {c.locator for c in by_role.get("REQUIRES", [])}
        for f in json.loads(fpath.read_text()):
            if f["ep"] in touched and f["verdict"] != "COVERED":
                reg.append({"ep": f["ep"], "verdict": f["verdict"],
                            "missing": f["missing"], "reason": f["reason"]})

    summary, flags = summarise(
        question, [c for c in cited_claims if c.role in POLICY_ROLES][:6], model
    )

    n_docs = len({c.doc_id for c in claims})
    return Brief(
        question=question, summary=summary, sections=sections, regulatory=reg,
        scope=f"{n_docs} documents connected to Veris",
        flags=flags,
    )


def save(brief: Brief, path: Path) -> None:
    path.write_text(json.dumps(asdict(brief), indent=2), encoding="utf-8")
