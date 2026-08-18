"""Veris evaluation suite.

Run: python -m eval.run_eval        (or: make eval)

Reports per-capability results and an overall status. Intelligence that cannot
be measured is not acceptable here, so every capability the product claims has
a check, and the checks that matter most are the ones that can fail loudly:
false relationships onto decoys, and citations that do not resolve to a real
span of a real document.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from veris.changes import citation_key, diff_documents, superseded_documents  # noqa: E402
from veris.store import Store  # noqa: E402

CASES = json.loads((Path(__file__).parent / "northstar_cases.json").read_text())


@dataclass
class Result:
    name: str
    passed: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def check(self, ok: bool, note: str) -> None:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.notes.append(note)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"  [{mark}] {self.name:<26} {self.passed} passed, {self.failed} failed"


def _entities(store: Store) -> list[dict]:
    return store.q("""SELECT e.*, d.title AS document_title, d.document_type,
                             d.version AS document_version, s.version AS source_version,
                             s.publisher, s.authority, s.effective_date
                      FROM entities e
                      JOIN documents d ON d.id = e.document_id
                      JOIN sources   s ON s.id = d.source_id""")


def eval_extraction(store: Store) -> Result:
    r = Result("extraction")
    ents = _entities(store)
    counts: dict[str, int] = {}
    for e in ents:
        key = e["document_title"]
        if e["document_type"] == "STANDARD":
            key = f"NS-CS.02.01_v{(e['source_version'] or '').split('.')[0]}"
        counts[key] = counts.get(key, 0) + 1
    for doc, expected in CASES["extraction"]["expected_entities"].items():
        got = counts.get(doc, 0)
        r.check(got == expected, f"{doc}: expected {expected} entities, got {got}")

    # Citation accuracy: the stored statement must be exactly the cited span.
    bad = 0
    for e in ents:
        ev = store.one("SELECT * FROM evidence WHERE id = ?", (e["evidence_id"],))
        if not ev:
            bad += 1
            continue
        doc = store.one("SELECT canonical_path FROM documents WHERE id = ?",
                        (e["document_id"],))
        text = Path(doc["canonical_path"]).read_text(encoding="utf-8")
        if text[ev["char_start"]:ev["char_end"]] != ev["quote"]:
            bad += 1
    r.check(bad == 0, f"{bad} entities whose evidence span does not match the source")
    return r


def eval_roles(store: Store) -> Result:
    r = Result("lifecycle roles")
    ents = _entities(store)
    for doc, expected in CASES["roles"]["expected"].items():
        if doc.startswith("NS-CS"):
            got = {e["role"] for e in ents if e["document_type"] == "STANDARD"}
        else:
            got = {e["role"] for e in ents if e["document_title"] == doc}
        r.check(got == {expected}, f"{doc}: expected role {expected}, got {sorted(got)}")
    return r


def eval_changes(store: Store) -> Result:
    r = Result("change detection")
    pairs = store.q("""SELECT d.id, s.version FROM documents d
                       JOIN sources s ON s.id = d.source_id
                       WHERE d.document_type = 'STANDARD' ORDER BY s.version""")
    if len(pairs) < 2:
        r.check(False, "expected two versions of the standard")
        return r
    deltas = {d.locator: d for d in diff_documents(store, pairs[0]["id"], pairs[1]["id"])}
    for locator, expected in CASES["change_detection"]["expected"].items():
        got = deltas.get(locator)
        r.check(got is not None and got.change_type == expected,
                f"{locator}: expected {expected}, got {got.change_type if got else 'missing'}")
    for locator, phrases in CASES["change_detection"]["expected_phrases"].items():
        d = deltas.get(locator)
        for phrase in phrases.get("added_contains", []):
            r.check(bool(d) and any(phrase in a for a in d.added),
                    f"{locator}: expected added phrase {phrase!r}")
        for phrase in phrases.get("removed_contains", []):
            r.check(bool(d) and any(phrase in x for x in d.removed),
                    f"{locator}: expected removed phrase {phrase!r}")
    return r


def _conflict_pairs(store: Store) -> set[tuple[str, str]]:
    rows = store.q("""
        SELECT ef.locator AS req, et.locator AS org
        FROM relationships r
        JOIN entities ef ON ef.id = r.from_entity_id
        JOIN entities et ON et.id = r.to_entity_id
        WHERE r.relationship_type = 'POTENTIALLY_CONFLICTS_WITH'""")
    return {(citation_key(x["req"]), x["org"]) for x in rows}


def eval_conflicts(store: Store) -> Result:
    r = Result("conflict detection")
    found = _conflict_pairs(store)
    for case in CASES["conflicts"]["expected"]:
        hit = any(req == case["requirement"] and org.startswith(case["organizational"])
                  for req, org in found)
        r.check(hit, f"missed conflict: {case['requirement']} vs {case['organizational']}")
    return r


def eval_gaps(store: Store) -> Result:
    r = Result("gap detection")
    rows = store.q("""SELECT f.severity, e.locator FROM findings f
                      JOIN entities e ON e.id = f.subject_entity_id
                      WHERE f.finding_type = 'POTENTIAL_GAP'""")
    found = {(citation_key(x["locator"]), x["severity"]) for x in rows}
    for case in CASES["gaps"]["expected"]:
        r.check((case["requirement"], case["severity"]) in found,
                f"missed gap: {case['requirement']} at {case['severity']}")
    allowed = {c["requirement"] for c in CASES["gaps"]["expected"]} | set(
        CASES["gaps"]["allowed_additional"])
    for locator, _ in found:
        r.check(locator in allowed, f"unexpected gap reported on {locator}")
    return r


def eval_decoys(store: Store) -> Result:
    """The metric that matters most: a false relationship is the expensive error."""
    r = Result("false positives (decoys)")
    for case in CASES["decoys"]["must_not_relate"]:
        rows = store.q("""
            SELECT r.relationship_type, e.locator FROM relationships r
            JOIN entities e ON e.id = r.to_entity_id
            JOIN documents d ON d.id = e.document_id
            WHERE d.title = ?""", (case["document"],))
        r.check(not rows,
                f"{case['document']} linked {len(rows)}x — {case['why']}")
    return r


def eval_citations(store: Store) -> Result:
    r = Result("citation accuracy")
    rows = store.q("""SELECT ev.*, d.canonical_path FROM finding_evidence fe
                      JOIN evidence ev ON ev.id = fe.evidence_id
                      JOIN documents d ON d.id = ev.document_id""")
    bad = 0
    for ev in rows:
        text = Path(ev["canonical_path"]).read_text(encoding="utf-8")
        if text[ev["char_start"]:ev["char_end"]] != ev["quote"]:
            bad += 1
    r.check(bad == 0, f"{bad} of {len(rows)} finding citations do not match their source")
    r.check(len(rows) > 0, "no finding carried any evidence at all")
    return r


def eval_effective_dates(store: Store) -> Result:
    r = Result("effective dates")
    stale = superseded_documents(store)
    r.check(len(stale) == 1, f"expected 1 superseded document, got {len(stale)}")
    from veris.ask import _pool
    pool = _pool(store)
    leaked = [c for c in pool if c.row["document_id"] in stale]
    r.check(not leaked, f"{len(leaked)} superseded entities reachable by Ask Veris")
    reqs = [c for c in pool if c.row["role"] == "REQUIRES"]
    r.check(len(reqs) == 6, f"expected 6 current requirements, got {len(reqs)}")
    return r


def eval_authority(store: Store) -> Result:
    r = Result("source authority")
    for field_name in CASES["source_authority"]["required_fields"]:
        missing = store.q(
            f"SELECT COUNT(*) n FROM sources WHERE {field_name} IS NULL OR {field_name} = ''")
        r.check(missing[0]["n"] == 0, f"{missing[0]['n']} sources missing {field_name}")
    row = store.one("""SELECT s.authority FROM sources s JOIN documents d ON d.source_id = s.id
                       WHERE d.document_type = 'STANDARD' ORDER BY s.version DESC LIMIT 1""")
    expected = CASES["source_authority"]["expected_authority"]["NS-CS.02.01_v2"]
    r.check(bool(row) and row["authority"] == expected,
            f"standard authority: expected {expected}, got {row and row['authority']}")
    return r


def eval_provenance(store: Store) -> Result:
    """No finding or relationship may present model inference as source fact."""
    r = Result("provenance labelling")
    for table in ("relationships", "findings"):
        rows = store.q(f"SELECT provenance_class, COUNT(*) n FROM {table} GROUP BY 1")
        classes = {x["provenance_class"] for x in rows}
        r.check("SOURCE_FACT" not in classes,
                f"{table} contains rows labelled SOURCE_FACT — inference must not "
                f"be presented as what the source says")
        r.check(bool(classes), f"{table} has no rows to check")
    unscoped = store.q(
        "SELECT COUNT(*) n FROM findings WHERE scope IS NULL OR scope = ''")[0]["n"]
    r.check(unscoped == 0, f"{unscoped} findings assert absence without naming a scope")
    return r


def main() -> int:
    db = Path("data/veris.db")
    if not db.exists():
        print("No database. Run: make seed")
        return 1
    store = Store(db)
    if store.stats()["entities"] == 0:
        print("Database is empty. Run: make seed")
        return 1

    print(f"\nVeris evaluation — {CASES['name']}\n")
    results = [
        eval_extraction(store), eval_roles(store), eval_changes(store),
        eval_conflicts(store), eval_gaps(store), eval_decoys(store),
        eval_citations(store), eval_effective_dates(store),
        eval_authority(store), eval_provenance(store),
    ]
    for res in results:
        print(res.line())
        for note in res.notes:
            print(f"         - {note}")

    total_p = sum(r.passed for r in results)
    total_f = sum(r.failed for r in results)
    ok = total_f == 0
    print(f"\n  {total_p} checks passed, {total_f} failed")
    print(f"  OVERALL: {'PASS' if ok else 'FAIL'}\n")
    print(f"  {CASES['honesty']}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
