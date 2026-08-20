"""Seed the demo: ingest, detect change, analyse, leave a reviewable graph.

Deterministic and idempotent — running it twice produces the same graph, so a
demo can be reset without drift.
"""

from __future__ import annotations

from pathlib import Path

from .agents import run_all
from .analyze import analyze_source_version
from .changes import find_version_pairs, record_changes
from .connectors.catalog import register_catalog
from .connectors.ecfr import register_ecfr
from .connectors.mock import register_mocks
from .model import Model
from .pipeline import ingest_directory
from .store import Store
from .sync import SyncEngine


def seed(store: Store, corpus: Path, data_dir: Path, model: Model,
         reset: bool = True, connect_demo_systems: bool = True) -> dict:
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

    # Connect the demo systems. These are labelled as mocks everywhere they
    # appear; a mock is never presented as a live integration.
    if connect_demo_systems:
        register_mocks()
        register_catalog()
        register_ecfr()
        engine = SyncEngine(store)
        connections = []
        for connector_id in ("mock_lms", "mock_policy", "mock_regulatory"):
            outcome = engine.connect(connector_id)
            sync = engine.run(outcome["connection_id"], "FULL")
            connections.append({
                "connector": connector_id,
                "discovered": outcome["discovery"]["total"] if outcome["discovery"] else 0,
                "synced": sync.synced, "status": sync.status,
            })
        report["connections"] = connections

    # Agents run last: they reason across everything now connected, including
    # comparisons no single system could make on its own.
    report["agents"] = [r.as_dict() for r in run_all(store)]

    report["stats"] = store.stats()
    return report
