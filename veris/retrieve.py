"""Candidate generation.

Two cheap, deterministic, model-free retrievers. Neither decides anything —
they nominate policy provisions for a requirement, and judgment happens later
(CONSTITUTION §6: similarity may discover a relationship, it may not establish
one).

Both are also baselines in their own right (Decision 0002, B0 and B3). Running
them before building anything semantic establishes what semantics has to beat.

No embedding model is used or required. That is deliberate: requiring embeddings
would mean requiring a second model deployment inside the hospital, doubling
setup burden for a customer who may be running one local model with difficulty
(Decision 0004).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .claims import Claim

STOP = set(
    """the a an and or of to in for on at by with from as is are be been must shall
    may can will that this these those it its their his her they them such other
    any all each per not no if when where which who whom whose than then there
    here into onto over under about above below between during within without
    hospital patient patients staff care provides provide provided including
    include includes ensure ensures required requirement requirements""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 2]


class BM25:
    """Standard BM25 over policy provisions. Pure stdlib — no index server."""

    def __init__(self, docs: list[Claim], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        # Index the locator with the body. A provision inherits its subject from
        # the document it lives in: "Infection Control and Prevention §4
        # Surveillance & Reporting" is about infection control even though its
        # text never says so. Indexing the body alone made the correct policy
        # unreachable for IC.04.01.01 EP 3 and produced a false gap.
        self.toks = [tokenize(f"{d.locator} {d.quote}") for d in docs]
        self.lens = [len(t) for t in self.toks]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        self.tf = [Counter(t) for t in self.toks]
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(docs)
        self.idf = {
            w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()
        }

    def search(self, query: str, top_k: int = 5) -> list[tuple[Claim, float]]:
        q = tokenize(query)
        scored: list[tuple[Claim, float]] = []
        for i, doc in enumerate(self.docs):
            score = 0.0
            for w in q:
                if w not in self.tf[i]:
                    continue
                f = self.tf[i][w]
                denom = f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg)
                score += self.idf.get(w, 0.0) * f * (self.k1 + 1) / denom
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


def citation_join(requirement: Claim, policies: list[Claim],
                  policy_doc_cfr: dict[str, set[str]]) -> list[Claim]:
    """Baseline B3: link where requirement and policy cite the same CFR section.

    Compares at section level (482.15), not subsection, because the two sides
    cite at different depths — a standard crosswalks to §482.15(d)(1)(i) while a
    policy cites §482.15. Requiring exact match would score this baseline
    unfairly low and make the comparison dishonest.
    """
    want = {c.split("(")[0] for c in requirement.crosswalk}
    if not want:
        return []
    return [p for p in policies if policy_doc_cfr.get(p.doc_id, set()) & want]


def policy_cfr_index(policies: list[Claim], canonical: dict[str, str]) -> dict[str, set[str]]:
    """CFR sections cited anywhere in a policy document.

    Document-level rather than provision-level on purpose: policies cite their
    regulation once in the header, so a provision-level index would be empty for
    almost every provision and would understate the baseline.
    """
    from .claims import CFR_RE

    idx: dict[str, set[str]] = {}
    for p in policies:
        if p.doc_id not in idx:
            idx[p.doc_id] = {
                m.split("(")[0] for m in CFR_RE.findall(canonical.get(p.doc_id, ""))
            }
    return idx
