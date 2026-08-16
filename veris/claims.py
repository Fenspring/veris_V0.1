"""Claim extraction.

Both sides of this corpus publish their own atomic units, so extraction is
structural rather than inferred:

- Joint Commission standards are already decomposed into Elements of
  Performance, each individually citable and individually surveyable, and most
  carry an "Attributes: Documentation" tag declaring that evidence must exist
  (Discovery 0003).
- Hospital policies are decomposed into numbered provisions under section
  headings.

Using the documents' own structure rather than a chunker means claim boundaries
are meaningful, stable, and reproducible without a model. It also means a weak
or absent model cannot corrupt grounding: spans come from a parser, and every
one is verified against the canonical text before it is returned.

Roles: standards REQUIRE. Policies either COMMIT (a rule) or OPERATIONALIZE (a
step-level procedure), taken from the document's own class line — see
policy_role. Everything the organization authored counts as its own evidence for
coverage purposes, so POLICY_ROLES is the pool to search.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from .ingest import load_canonical


@dataclass(frozen=True)
class Claim:
    claim_id: str
    doc_id: str
    locator: str          # human-readable citation, e.g. "EM.15.01.01 EP 2"
    role: str             # REQUIRES | COMMITS
    quote: str            # verbatim, must match canonical[char_start:char_end]
    char_start: int
    char_end: int
    expects_document: bool
    crosswalk: list[str]  # CFR references asserted by the source
    title: str


POLICY_ROLES = ("COMMITS", "OPERATIONALIZES")

CFR_RE = re.compile(r"§+\s?(482\.\d+(?:\([a-z0-9]+\))*)")
EP_RE = re.compile(r"^### EP (\d+)\s*$", re.M)
# A heading is a line in caps that is not a bare enumerator.
HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 &,\-—/()'.#]{3,}$")
ENUM_RE = re.compile(r"^(\d{1,2})$")


def _cfr(text: str) -> list[str]:
    return sorted(set(CFR_RE.findall(text)))


def standard_claims(doc_id: str, standard: str, text: str) -> list[Claim]:
    """One claim per Element of Performance."""
    claims: list[Claim] = []
    matches = list(EP_RE.finditer(text))
    # EPs run to the next EP, or to the trailing "## Related" navigation block.
    tail = text.find("\n---\n\n## Related")
    if tail == -1:
        tail = len(text)

    for i, m in enumerate(matches):
        start = m.end() + 1
        end = matches[i + 1].start() if i + 1 < len(matches) else tail
        body = text[start:end]

        # Trim trailing blank lines so the span covers only substantive text.
        trimmed = body.rstrip()
        end = start + len(trimmed)
        if not trimmed:
            continue

        attrs = re.search(r"\*\*Attributes:\*\*\s*(.+)", trimmed)
        claims.append(
            Claim(
                claim_id=f"{doc_id}::ep{m.group(1)}",
                doc_id=doc_id,
                locator=f"{standard} EP {m.group(1)}",
                role="REQUIRES",
                quote=text[start:end],
                char_start=start,
                char_end=end,
                expects_document=bool(attrs and "Documentation" in attrs.group(1)),
                crosswalk=_cfr(trimmed),
                title=standard,
            )
        )
    return claims


def policy_role(text: str) -> str:
    """COMMITS or OPERATIONALIZES, from the document's own class line.

    These policies label themselves: a step-level procedure carries
    "HOSPITAL CLINICAL POLICY — DETAILED / PROCEDURE" in its header, while a
    rule-level policy carries "HOSPITAL COMPLIANCE POLICY". Matching on the word
    "PROCEDURE" alone is too loose — it appears in ordinary titles such as
    "Procedures for Safe Drug Dispensing" — so the class line is the signal.

    This distinction was deliberately not built until a finding needed it. The
    clinician brief needs it: "what our rule is" and "how to actually do it" are
    different questions and belong in different sections.
    """
    return "OPERATIONALIZES" if "DETAILED" in text[:200].upper() else "COMMITS"


def policy_claims(doc_id: str, policy_title: str, text: str) -> list[Claim]:
    """One claim per numbered provision.

    Policy PDFs render as: an ALL-CAPS section heading, then repeating
    (bare number, provision title, body lines). Provisions are the unit a
    policy owner would recognise and cite, which is the same reason EPs are
    the unit on the standards side.
    """
    role = policy_role(text)
    lines = text.split("\n")
    # Character offset of the start of each line, for exact spans.
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    section = ""
    claims: list[Claim] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if HEADING_RE.match(line) and not ENUM_RE.match(line):
            section = line.title()
            i += 1
            continue

        if ENUM_RE.match(line) and i + 1 < len(lines):
            number = line
            title = lines[i + 1].strip()
            start = offsets[i + 1]
            j = i + 2
            while j < len(lines):
                nxt = lines[j].strip()
                if ENUM_RE.match(nxt) or (HEADING_RE.match(nxt) and len(nxt) > 3):
                    break
                j += 1
            end = offsets[j] - 1 if j < len(lines) else len(text)
            end = min(end, len(text))
            quote = text[start:end].rstrip()
            end = start + len(quote)

            if len(quote) > 40:
                claims.append(
                    Claim(
                        claim_id=f"{doc_id}::p{number}",
                        doc_id=doc_id,
                        locator=f"{policy_title} §{number} {title}"[:120],
                        role=role,
                        quote=quote,
                        char_start=start,
                        char_end=end,
                        expects_document=False,
                        crosswalk=_cfr(quote),
                        title=title,
                    )
                )
            i = j
            continue

        i += 1

    return claims


def extract_all(data: Path) -> list[Claim]:
    manifest = json.loads((data / "manifest.json").read_text())
    out: list[Claim] = []
    for doc in manifest:
        text = load_canonical(data, doc["doc_id"])
        if not text.strip():
            continue
        if doc["genre"] == "standard":
            out.extend(standard_claims(doc["doc_id"], doc["title"], text))
        else:
            out.extend(policy_claims(doc["doc_id"], doc["title"], text))

    # The trust primitive: nothing that fails span verification survives.
    verified, rejected = [], []
    for c in out:
        if load_canonical(data, c.doc_id)[c.char_start : c.char_end] == c.quote:
            verified.append(c)
        else:
            rejected.append(c)
    if rejected:
        raise AssertionError(
            f"{len(rejected)} claims failed span verification, e.g. {rejected[0].claim_id}"
        )

    (data / "claims.json").write_text(
        json.dumps([asdict(c) for c in verified], indent=2), encoding="utf-8"
    )
    return verified


def load_claims(data: Path) -> list[Claim]:
    return [Claim(**c) for c in json.loads((data / "claims.json").read_text())]


if __name__ == "__main__":
    import sys
    from collections import Counter

    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    claims = extract_all(data)
    roles = Counter(c.role for c in claims)
    print(f"extracted {len(claims)} claims (all spans verified)")
    for r, n in roles.most_common():
        print(f"  {r}: {n}")
    print(f"  REQUIRES with expects_document: "
          f"{sum(1 for c in claims if c.expects_document)}")
    print(f"  claims asserting a CFR crosswalk: "
          f"{sum(1 for c in claims if c.crosswalk)}")
