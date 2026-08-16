"""Coverage adjudication, and the disconfirmation pass.

Retrieval nominates; this module decides. The split matters: cheap methods
optimise recall, where being wrong is survivable, and judgment optimises
precision, where being wrong is expensive (Decision 0003).

Two properties are load-bearing for the model-agnostic requirement:

- Output is line-oriented, not JSON. Small local models fail strict JSON far
  more often than they fail "one field per line", and the parser is tolerant of
  case, whitespace, and extra commentary.
- Evidence is referenced by candidate id, never quoted back by the model. A
  model cannot fabricate a citation it is not asked to reproduce; ids either
  resolve to a real claim or are discarded. This is the same principle as span
  verification, applied one layer up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .claims import Claim
from .model import Model
from .retrieve import BM25

VERDICTS = ("COVERED", "PARTIAL", "NOT_COVERED")

SYSTEM = (
    "You assess whether a hospital's own policies satisfy an accreditation "
    "requirement. You are shown the requirement and a numbered list of candidate "
    "policy provisions retrieved from the hospital's policy library.\n\n"
    "Judge only what the provisions actually say. A provision that shares "
    "vocabulary with the requirement but addresses a different obligation does "
    "not satisfy it. A provision that satisfies the requirement in different "
    "words does satisfy it.\n\n"
    "Answer NOT_COVERED only if none of the provisions addresses the "
    "requirement. Prefer PARTIAL when something related is present but "
    "incomplete: overstating a gap is worse than reporting one cautiously."
)

TEMPLATE = """REQUIREMENT [{locator}]:
{requirement}

CANDIDATE POLICY PROVISIONS:
{candidates}

Reply with exactly these four lines and nothing else:
VERDICT: one of COVERED, PARTIAL, NOT_COVERED
EVIDENCE: candidate ids that support the verdict (e.g. C1, C3), or NONE
MISSING: what the requirement demands that no candidate provides, or NONE
REASON: one sentence citing what the provisions say"""


@dataclass
class Finding:
    ep: str
    verdict: str
    evidence: list[str]          # claim_ids, resolved from candidate ids
    evidence_locators: list[str]
    missing: str
    reason: str
    scope: str                   # what was searched — required for absence claims
    confidence: str              # high | medium | low
    disconfirmed: bool = False   # did the gap survive an adversarial re-search
    candidates_considered: int = 0
    flags: list[str] = field(default_factory=list)


def build_prompt(req: Claim, candidates: list[Claim]) -> str:
    parts = []
    for i, c in enumerate(candidates):
        body = re.sub(r"[ \t]+", " ", c.quote)
        parts.append(f"[C{i+1}] {c.locator}\n{body}")
    rendered = "\n\n".join(parts)
    return TEMPLATE.format(
        locator=req.locator,
        requirement=re.sub(r"[ \t]+", " ", req.quote),
        candidates=rendered or "(none retrieved)",
    )


def parse_response(text: str) -> dict:
    """Tolerant line parser. A model that adds preamble, changes case, or uses
    different bullet characters still produces a usable answer; only a missing
    VERDICT is fatal."""
    out = {"verdict": "", "evidence": [], "missing": "", "reason": ""}
    for line in text.splitlines():
        line = line.strip().lstrip("-*# ").strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().upper(), value.strip()
        if key == "VERDICT":
            for v in VERDICTS:
                # NOT_COVERED must be tested before COVERED, since it contains it.
                if v in value.upper().replace(" ", "_"):
                    out["verdict"] = v
                    break
            if "NOT" in value.upper():
                out["verdict"] = "NOT_COVERED"
        elif key == "EVIDENCE":
            if value.upper() != "NONE":
                out["evidence"] = re.findall(r"C(\d+)", value.upper())
        elif key == "MISSING":
            out["missing"] = "" if value.upper() == "NONE" else value
        elif key == "REASON":
            out["reason"] = value
    return out


EXPANSION_SYSTEM = (
    "You help search a hospital's policy library. Given an accreditation "
    "requirement, name the words a HOSPITAL POLICY on this subject would "
    "actually use — which are usually not the words the requirement uses."
)

EXPANSION_TEMPLATE = """REQUIREMENT:
{requirement}

List 4 short search phrases, one per line, no numbering or commentary.
Use the vocabulary a hospital policy or procedure would use, including the
likely title of such a policy. Do not simply repeat the requirement's wording."""


def structural_queries(req: Claim) -> list[str]:
    """Sub-requirements searched individually, in case a requirement is covered
    piecemeal across several policies when no single provision matches it whole."""
    text = re.sub(r"\s+", " ", req.quote)
    out = [b.strip() for b in re.findall(r"-\s+([^-\n]{20,120})", text)]
    return [q for q in out if len(q) > 12][:4]


def disconfirmation_queries(req: Claim, model: Model | None = None) -> list[str]:
    """Queries used to try to disprove a gap before asserting it.

    The first version of this derived every query from the requirement's own
    text and searched the same index, so it never escaped the lexical
    neighbourhood that produced the original candidates and surfaced zero new
    provisions on all eight gaps in the first run — the mechanism was inert
    exactly where it was needed. See docs/discoveries/0004.

    A gap arises precisely when the policy uses different words, so queries must
    come from outside the requirement's vocabulary. The model supplies that
    vocabulary; structural sub-requirement queries supplement it and still work
    if the expansion call fails.
    """
    queries = structural_queries(req)

    if model is not None:
        try:
            raw = model.complete(
                EXPANSION_SYSTEM,
                EXPANSION_TEMPLATE.format(
                    requirement=re.sub(r"\s+", " ", req.quote)[:1200]
                ),
                max_tokens=120,
            )
            for line in raw.splitlines():
                line = line.strip().lstrip("-*0123456789. ").strip()
                if 3 < len(line) < 120:
                    queries.append(line)
        except Exception:
            # Expansion is an enhancement to recall. If it fails, the gap is
            # still tested structurally rather than asserted untested.
            pass

    seen, out = set(), []
    for q in queries:
        k = q.lower()[:60]
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[:8]


def adjudicate(
    req: Claim,
    policies: list[Claim],
    bm: BM25,
    model: Model,
    top_k: int = 6,
    corpus_description: str = "",
) -> Finding:
    candidates = [c for c, _ in bm.search(req.quote, top_k)]
    by_id = {f"{i+1}": c for i, c in enumerate(candidates)}

    raw = model.complete(SYSTEM, build_prompt(req, candidates), max_tokens=400)
    parsed = parse_response(raw)

    flags: list[str] = []
    verdict = parsed["verdict"]
    if verdict not in VERDICTS:
        # Unparseable output is recorded, not guessed at. Defaulting to a gap
        # would manufacture false positives out of parser failures.
        verdict = "UNPARSEABLE"
        flags.append("unparseable_model_output")

    resolved = [by_id[i] for i in parsed["evidence"] if i in by_id]
    dropped = len(parsed["evidence"]) - len(resolved)
    if dropped:
        flags.append(f"dropped_{dropped}_unresolvable_evidence_ids")

    # A verdict of COVERED with no resolvable evidence is not a coverage claim,
    # it is an assertion. Downgrade it rather than publish it.
    if verdict in ("COVERED", "PARTIAL") and not resolved:
        verdict = "PARTIAL"
        flags.append("no_resolvable_evidence_downgraded")

    confidence = "high"
    disconfirmed = False

    if verdict == "NOT_COVERED":
        # The disconfirmation pass. Try to prove the gap wrong before asserting
        # it: re-search on independently derived formulations, and if anything
        # substantive surfaces that the first pass never saw, the gap does not
        # stand unchallenged.
        seen = {c.claim_id for c in candidates}
        surfaced: list[Claim] = []
        for q in disconfirmation_queries(req, model):
            for c, sc in bm.search(q, 3):
                if c.claim_id not in seen and sc > 10:
                    seen.add(c.claim_id)
                    surfaced.append(c)
        if surfaced:
            extra = surfaced[:5]
            raw2 = model.complete(
                SYSTEM, build_prompt(req, candidates + extra), max_tokens=400
            )
            p2 = parse_response(raw2)
            if p2["verdict"] in ("COVERED", "PARTIAL"):
                by_id2 = {
                    f"{i+1}": c for i, c in enumerate(candidates + extra)
                }
                verdict = p2["verdict"]
                resolved = [by_id2[i] for i in p2["evidence"] if i in by_id2]
                parsed = p2
                flags.append("gap_overturned_by_disconfirmation")
            else:
                disconfirmed = True
            candidates = candidates + extra
        else:
            disconfirmed = True
        confidence = "medium" if disconfirmed else "high"

    scope = corpus_description or f"{len({p.doc_id for p in policies})} policy documents supplied"

    return Finding(
        ep=req.locator,
        verdict=verdict,
        evidence=[c.claim_id for c in resolved],
        evidence_locators=[c.locator for c in resolved],
        missing=parsed["missing"],
        reason=parsed["reason"],
        scope=f"searched {scope}",
        confidence=confidence,
        disconfirmed=disconfirmed,
        candidates_considered=len(candidates),
        flags=flags,
    )


def save(findings: list[Finding], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(f) for f in findings], indent=2), encoding="utf-8"
    )
