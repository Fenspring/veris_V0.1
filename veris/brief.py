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


# Relevance floor. A hazard-vulnerability-analysis standard surfaced for a
# question about blood products: BM25 always returns its top k, however weak the
# match, so a section with nothing relevant in it still fills up.
#
# The floor is relative to the best hit in the same role, not absolute. BM25
# scores scale with query length and term rarity and are not comparable between
# roles — Elements of Performance are long, policy provisions short — so a fixed
# cutoff would be too strict for one role and useless for another.
#
# Relative alone is not enough: when every hit is noise the best one is still
# 1.0x itself and survives. ABS_FLOOR catches that case.
REL_FLOOR = 0.45   # drop hits scoring below this fraction of the role's best
ABS_FLOOR = 4.0    # ...and drop everything when even the best hit is this weak


def triage(hits: list[tuple[Claim, float]]) -> tuple[list[Claim], int]:
    """Apply the lexical floor. Returns (survivors, dropped-count).

    Survivors go to judgment; the floor does not decide relevance, only which
    candidates are worth the cost of asking about.

    An earlier version reserved a "borderline" score band and judged only that,
    keeping anything above a CLEAR_SCORE without asking. It was removed because
    the boundary could not survive query expansion: scores are the maximum across
    several generated queries, so expansion inflates them and pushes noise above
    any absolute cutoff. An Antibiotic Stewardship provision cleared the
    threshold for a question about central line infections purely because one
    expansion query mentioned "infection prevention and control program".

    Calibrating one more constant would only have moved the failure. Judging
    every survivor costs the same single batched call and removes the constant.
    """
    if not hits:
        return [], 0
    best = hits[0][1]
    if best < ABS_FLOOR:
        return [], len(hits)
    cutoff = max(ABS_FLOOR, REL_FLOOR * best)
    kept = [c for c, s in hits if s >= cutoff]
    return kept, len(hits) - len(kept)


RELEVANCE_SYSTEM = (
    "You filter search results. For each candidate, decide whether it actually "
    "addresses the subject of the clinician's question.\n\n"
    "A candidate that merely shares wording with the question, or that belongs "
    "to an unrelated part of hospital operations, does not address it. A "
    "candidate that addresses the subject in different words does.\n\n"
    "Keep a candidate when genuinely unsure: wrongly discarding relevant "
    "knowledge is worse than showing one extra item."
)

RELEVANCE_TEMPLATE = """QUESTION: {question}

CANDIDATES:
{candidates}

Reply with one line per candidate, its id then YES or NO, nothing else:
C1: YES
C2: NO"""


def judge_relevance(question: str, borderline: list[Claim],
                    model: Model | None) -> tuple[list[Claim], list[str]]:
    """Ask the model which candidates actually address the question.

    One call for every candidate across every section, so the cost is a single
    extra call per question regardless of how many survived the floor.

    On any failure — no model, an error, an unparseable reply, a missing id —
    the candidate is KEPT. Dropping on failure would let an infrastructure
    problem manufacture a false absence, which is the one outcome this system
    must not produce silently.
    """
    if not borderline:
        return [], []
    if model is None:
        return borderline, ["relevance_judgment_skipped_no_model"]

    parts = []
    for i, c in enumerate(borderline):
        body = re.sub(r"\s+", " ", c.quote)[:400]
        parts.append(f"[C{i+1}] {c.locator}\n{body}")
    try:
        raw = model.complete(
            RELEVANCE_SYSTEM,
            RELEVANCE_TEMPLATE.format(question=question, candidates="\n\n".join(parts)),
            max_tokens=200,
        )
    except Exception:
        return borderline, ["relevance_judgment_failed_kept_all"]

    verdicts: dict[int, bool] = {}
    for line in raw.splitlines():
        m = re.match(r"\s*\[?C(\d+)\]?\s*[:.\-]?\s*(YES|NO)\b", line.strip(), re.I)
        if m:
            verdicts[int(m.group(1))] = m.group(2).upper() == "YES"

    kept, flags = [], []
    unjudged = 0
    for i, c in enumerate(borderline):
        if verdicts.get(i + 1, True):      # absent verdict means keep
            kept.append(c)
        if i + 1 not in verdicts:
            unjudged += 1
    if unjudged:
        flags.append(f"relevance_judgment_missing_{unjudged}_verdicts_kept")
    rejected = len(borderline) - len(kept)
    if rejected:
        flags.append(f"relevance_judgment_rejected_{rejected}")
    return kept, flags


def gather(question: str, claims: list[Claim], model: Model | None,
           per_role: int = 4) -> tuple[dict[str, list[Claim]], dict[str, int], list[str]]:
    queries = expand(question, model)

    ranked_by_role: dict[str, list[Claim]] = {}
    filtered: dict[str, int] = {}
    candidates: list[Claim] = []

    for role, _, _ in SECTIONS:
        pool = [c for c in claims if c.role == role]
        if not pool:
            ranked_by_role[role], filtered[role] = [], 0
            continue
        bm = BM25(pool)
        hits: dict[str, tuple[Claim, float]] = {}
        for q in queries:
            for c, s in bm.search(q, per_role):
                # Keep a claim's best score across all query formulations.
                if c.claim_id not in hits or s > hits[c.claim_id][1]:
                    hits[c.claim_id] = (c, s)
        ranked = sorted(hits.values(), key=lambda x: -x[1])[:per_role]
        survived, dropped = triage(ranked)
        ranked_by_role[role] = [c for c, _ in ranked]
        filtered[role] = dropped
        candidates.extend(survived)

    # One judgment call for every candidate that cleared the floor, across all
    # sections. The floor controls cost; the model decides relevance.
    survivors, flags = judge_relevance(question, candidates, model)
    survivor_ids = {c.claim_id for c in survivors}
    rejected_ids = {c.claim_id for c in candidates} - survivor_ids

    kept: dict[str, list[Claim]] = {}
    for role in ranked_by_role:
        allowed = survivor_ids
        # Rebuild from the ranked list so score order is preserved.
        kept[role] = [c for c in ranked_by_role[role] if c.claim_id in allowed]
        filtered[role] += sum(
            1 for c in ranked_by_role[role] if c.claim_id in rejected_ids
        )
    return kept, filtered, flags


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
    by_role, filtered, flags = gather(question, claims, model)

    sections, cited_claims = [], []
    for role, label, absence in SECTIONS:
        got = by_role.get(role, [])
        cited_claims.extend(got)
        note = ""
        if not got:
            # Two different kinds of empty, and saying the stronger one when the
            # weaker is true would overstate (DESIGN_PRINCIPLES 6). Nothing
            # connected at all is a fact about the corpus; nothing above the
            # relevance floor is a fact about this question.
            note = (f"Nothing in the connected {label.lower()} appears relevant "
                    f"to this question." if filtered.get(role) else absence)
        if filtered.get(role):
            flags.append(f"{role.lower()}_relevance_floor_dropped_{filtered[role]}")
        sections.append(Section(
            role=role, label=label,
            claims=[{"locator": c.locator, "quote": c.quote, "claim_id": c.claim_id}
                    for c in got],
            absence_note=note,
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

    summary, sum_flags = summarise(
        question, [c for c in cited_claims if c.role in POLICY_ROLES][:6], model
    )
    flags.extend(sum_flags)

    n_docs = len({c.doc_id for c in claims})
    return Brief(
        question=question, summary=summary, sections=sections, regulatory=reg,
        scope=f"{n_docs} documents connected to Veris",
        flags=flags,
    )


def save(brief: Brief, path: Path) -> None:
    path.write_text(json.dumps(asdict(brief), indent=2), encoding="utf-8")
