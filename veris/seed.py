"""Seed the demo: ingest, detect change, analyse, leave a reviewable graph.

Deterministic and idempotent — running it twice produces the same graph, so a
demo can be reset without drift.
"""

from __future__ import annotations

from pathlib import Path

from .analyze import analyze_source_version
from .changes import find_version_pairs, record_changes
from .model import Model
from .pipeline import ingest_directory
from .store import Store


def seed(store: Store, corpus: Path, data_dir: Path, model: Model,
         reset: bool = True) -> dict:
    if reset:
        store.reset()

    results = ingest_directory(store, corpus, data_dir)
    report: dict = {
        "documents": len(results),
        "entities": sum(r.entities for r in results),
        "changes": 0,
        "analysis": {},
    }

    latest_standard = None
    for older, newer in find_version_pairs(store):
        report["changes"] += len(record_changes(store, older["id"], newer["id"]))
        latest_standard = newer

    # Analyse the current version. Requirements that changed carry their diff
    # into the prompt, so relationships and impact come out of one pass.
    if latest_standard is None:
        standards = store.q(
            "SELECT * FROM documents WHERE document_type IN ('STANDARD','REGULATION')")
        latest_standard = standards[-1] if standards else None
    if latest_standard:
        report["analysis"] = analyze_source_version(store, latest_standard["id"], model)
        report["analysed_document"] = latest_standard["title"]

    report["stats"] = store.stats()
    return report
