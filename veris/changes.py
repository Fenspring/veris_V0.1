"""Version change detection.

Given two versions of the same authoritative source, determine what actually
changed at the level of individual requirements.

The diff is **computed, not inferred**. Entities are matched by locator, and
modified text is compared token by token, so the added and removed phrases are
extracted from the documents rather than described by a model. That makes every
change record a VERIS_INTERPRETATION — derived by rules we can explain and
re-run — instead of a MODEL_INFERENCE that a reader has to take on trust.

A model may later write a more fluent summary, but it can only ever restate a
difference that was already established structurally. When no model is
available the templated summary is used, and the product still works. That is
deliberate: the North Star capability must not be the part that breaks when
inference is unavailable.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .store import Store

WORD_RE = re.compile(r"\S+")
CITATION_RE = re.compile(r"((?:EP|Element)\s+\S+|§\S+)")


def citation_key(locator: str) -> str:
    """The stable part of a locator across versions: "EP 3", "§4"."""
    found = CITATION_RE.findall(locator)
    return found[-1] if found else locator


@dataclass
class Delta:
    change_type: str
    locator: str
    from_entity_id: str | None
    to_entity_id: str | None
    summary: str
    added: list[str]
    removed: list[str]


def _norm(text: str) -> str:
    """Strip structural noise so formatting churn is not reported as change."""
    text = re.sub(r"\*\*Attributes:\*\*.*", "", text)
    text = re.sub(r"\*\*Crosswalk:\*\*.*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def phrase_diff(before: str, after: str) -> tuple[list[str], list[str]]:
    """Token-level diff returning contiguous added and removed phrases.

    Phrases, not individual words: "within 24 hours" → "within 4 hours" should
    read as one substantive change, not three word-level edits a human has to
    reassemble.
    """
    a, b = WORD_RE.findall(before), WORD_RE.findall(after)
    added, removed = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag in ("replace", "delete") and a[i1:i2]:
            removed.append(" ".join(a[i1:i2]))
        if tag in ("replace", "insert") and b[j1:j2]:
            added.append(" ".join(b[j1:j2]))
    return added, removed


def _summarise(locator: str, change_type: str, added: list[str], removed: list[str]) -> str:
    if change_type == "ADDED":
        return f"{locator} is new in this version."
    if change_type == "REMOVED":
        return f"{locator} no longer appears in this version."
    parts = []
    if removed:
        parts.append("no longer says " + "; ".join(f"“{r}”" for r in removed[:3]))
    if added:
        parts.append("now says " + "; ".join(f"“{a}”" for a in added[:3]))
    return f"{locator} " + ", and ".join(parts) if parts else f"{locator} was reworded."


def diff_documents(store: Store, from_doc_id: str, to_doc_id: str) -> list[Delta]:
    """Match entities by locator suffix and classify each as added, removed,
    modified or unchanged."""
    def load(doc_id: str) -> dict[str, dict]:
        rows = store.q("SELECT * FROM entities WHERE document_id = ?", (doc_id,))
        # Key on the citation suffix ("EP 3", "§4"), never the whole locator:
        # the locator carries the document title, which carries a version, so
        # matching on it whole would make every requirement look new.
        return {citation_key(r["locator"]): r for r in rows}

    before, after = load(from_doc_id), load(to_doc_id)
    deltas: list[Delta] = []

    for key in sorted(set(before) | set(after), key=_sort_key):
        b, a = before.get(key), after.get(key)
        if b and not a:
            deltas.append(Delta("REMOVED", key, b["id"], None,
                                _summarise(key, "REMOVED", [], []), [], []))
        elif a and not b:
            deltas.append(Delta("ADDED", key, None, a["id"],
                                _summarise(key, "ADDED", [], []), [], []))
        else:
            nb, na = _norm(b["statement"]), _norm(a["statement"])
            if nb == na:
                deltas.append(Delta("UNCHANGED", key, b["id"], a["id"],
                                    f"{key} is unchanged.", [], []))
            else:
                added, removed = phrase_diff(nb, na)
                deltas.append(Delta("MODIFIED", key, b["id"], a["id"],
                                    _summarise(key, "MODIFIED", added, removed),
                                    added, removed))
    return deltas


def _sort_key(locator: str):
    nums = [int(n) for n in re.findall(r"\d+", locator)]
    return (nums or [0], locator)


def record_changes(store: Store, from_doc_id: str, to_doc_id: str,
                   include_unchanged: bool = False) -> list[str]:
    """Persist the diff. Unchanged requirements are skipped by default but can
    be recorded when a reviewer needs to see that they were examined."""
    ids = []
    for d in diff_documents(store, from_doc_id, to_doc_id):
        if d.change_type == "UNCHANGED" and not include_unchanged:
            continue
        ids.append(store.add_change(
            from_document_id=from_doc_id, to_document_id=to_doc_id,
            from_entity_id=d.from_entity_id, to_entity_id=d.to_entity_id,
            change_type=d.change_type, locator=d.locator, summary=d.summary,
            detail={"added": d.added, "removed": d.removed},
            provenance_class="VERIS_INTERPRETATION",
        ))
    store.log("changes_detected",
              f"{len(ids)} changes between {from_doc_id} and {to_doc_id}")
    store.commit()
    return ids


def find_version_pairs(store: Store) -> list[tuple[dict, dict]]:
    """Documents sharing a title across two source versions, ordered by
    effective date. This is how the demo locates 'the standard that changed'
    without anyone naming the pair by hand."""
    docs = store.q("""
        SELECT d.*, s.version AS source_version, s.effective_date AS source_effective
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE d.document_type IN ('STANDARD','REGULATION')
        ORDER BY d.title, s.effective_date""")
    pairs = []
    by_title: dict[str, list[dict]] = {}
    for d in docs:
        by_title.setdefault(d["title"], []).append(d)
    for versions in by_title.values():
        for older, newer in zip(versions, versions[1:]):
            pairs.append((older, newer))
    return pairs


def superseded_documents(store: Store) -> set[str]:
    """Document ids replaced by a newer version of the same source.

    A superseded requirement is still knowledge — the graph keeps it so a change
    can be explained against what it replaced — but it must never be presented
    as current. Showing a clinician the previous version of a rule alongside the
    one in force is worse than showing nothing.
    """
    return {older["id"] for older, _ in find_version_pairs(store)}
