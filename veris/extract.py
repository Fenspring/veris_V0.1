"""Structural knowledge extraction.

Documents are parsed into knowledge entities using the units the documents
themselves publish — Elements of Performance in a standard, numbered provisions
in a policy, numbered steps in a procedure, learning objectives in an education
module, criteria in a competency.

Two consequences worth being explicit about:

- **No model is required to extract.** Boundaries come from a parser, so they are
  deterministic, reproducible, and cannot be corrupted by a weak or absent model.
  A model is used later, for judgment, where judgment is actually needed.
- **Every entity carries a verified span.** The statement must appear
  byte-for-byte in the canonical text at the recorded offsets, or it is rejected
  before it can become knowledge (Decision 0001).

The lifecycle role comes from the document's declared type rather than from
inference: a competency validates, an education module teaches, a standard
requires. The organization already made that classification when it filed the
document; Veris reads it rather than guessing at it (Discovery 0001).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# document_type -> (entity_type, lifecycle role)
TYPE_ROLE = {
    "STANDARD":   ("REQUIREMENT", "REQUIRES"),
    "REGULATION": ("REQUIREMENT", "REQUIRES"),
    "POLICY":     ("POLICY_STATEMENT", "COMMITS"),
    "PROCEDURE":  ("PROCEDURE_STEP", "OPERATIONALIZES"),
    "EDUCATION":  ("EDUCATION_OBJECTIVE", "TEACHES"),
    "COMPETENCY": ("COMPETENCY_CRITERION", "VALIDATES"),
    "ORIENTATION": ("EDUCATION_OBJECTIVE", "TEACHES"),
    "AUDIT":      ("POLICY_STATEMENT", "MEASURES"),
}

EP_RE = re.compile(r"^### EP (\S+)\s*$", re.M)
SECTION_RE = re.compile(r"^##\s+§(\S+)\s+(.+?)\s*$", re.M)
CROSSWALK_RE = re.compile(r"§+\s?(\d+\.\d+(?:\([a-z0-9]+\))*)")


@dataclass(frozen=True)
class Extracted:
    locator: str
    title: str
    statement: str
    char_start: int
    char_end: int
    expects_document: bool
    crosswalk: list[str]


def _blocks(text: str, pattern: re.Pattern, end_marker: str | None
            ) -> list[tuple[str, int, int]]:
    """Split text on a heading pattern, returning (heading-groups, start, end)."""
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    tail = len(text)
    if end_marker:
        i = text.find(end_marker)
        if i != -1:
            tail = i
    out = []
    for n, m in enumerate(matches):
        start = m.end() + 1
        end = matches[n + 1].start() if n + 1 < len(matches) else tail
        body = text[start:end].rstrip()
        if body:
            out.append((m, start, start + len(body)))
    return out


def extract_standard(text: str, standard_id: str) -> list[Extracted]:
    """One entity per Element of Performance."""
    out = []
    for m, start, end in _blocks(text, EP_RE, "\n---\n\n## Related"):
        body = text[start:end]
        attrs = re.search(r"\*\*Attributes:\*\*\s*(.+)", body)
        out.append(Extracted(
            locator=f"{standard_id} EP {m.group(1)}",
            title=f"EP {m.group(1)}",
            statement=body,
            char_start=start,
            char_end=end,
            # The standard declaring that documentary evidence must exist is what
            # gives a later gap finding its authority (Discovery 0003).
            expects_document=bool(attrs and "Documentation" in attrs.group(1)),
            crosswalk=sorted(set(CROSSWALK_RE.findall(body))),
        ))
    return out


def extract_sections(text: str, doc_title: str) -> list[Extracted]:
    """One entity per numbered provision, step, objective or criterion."""
    out = []
    for m, start, end in _blocks(text, SECTION_RE, None):
        body = text[start:end]
        out.append(Extracted(
            locator=f"{doc_title} §{m.group(1)} {m.group(2)}"[:140],
            title=m.group(2),
            statement=body,
            char_start=start,
            char_end=end,
            expects_document=False,
            crosswalk=sorted(set(CROSSWALK_RE.findall(body))),
        ))
    return out


def extract(text: str, document_type: str, title: str) -> list[Extracted]:
    """Dispatch on document type, then fall back across parsers.

    A document that declares itself a standard but contains no Elements of
    Performance still gets parsed by section rather than yielding nothing —
    silently extracting zero entities would make the document invisible to
    coverage analysis and manufacture false gaps downstream.
    """
    if document_type in ("STANDARD", "REGULATION"):
        found = extract_standard(text, title)
        if found:
            return found
    found = extract_sections(text, title)
    if found:
        return found
    return extract_standard(text, title)


def verify(extracted: list[Extracted], canonical: str) -> tuple[list[Extracted], list[Extracted]]:
    """The trust primitive: split into (verified, rejected) by exact span match."""
    ok, bad = [], []
    for e in extracted:
        (ok if canonical[e.char_start:e.char_end] == e.statement else bad).append(e)
    return ok, bad
