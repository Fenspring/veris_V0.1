"""Upgrade tests.

A hospital that has been running Veris for a month has connected systems,
synced records and reviewed findings. An upgrade that asked them to start again
would be asking them to discard organizational knowledge, so the schema must
move forward underneath their data rather than replace it.

These tests build a database in the *previous* shape by hand — not by importing
an old module — so they keep working after the old code is gone.

Run: .venv/bin/python tests/test_migrations.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from veris.store import SCHEMA_VERSION, Store  # noqa: E402

# The v3 shape of the tables migration 4 touches. Only what the migration reads
# or renames; the rest of the schema is created by Store on open.
V3 = """
CREATE TABLE schema_meta (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE connections (
    id TEXT PRIMARY KEY, connector_id TEXT NOT NULL, name TEXT NOT NULL,
    category TEXT NOT NULL, status TEXT NOT NULL, auth_method TEXT,
    config TEXT, is_mock INTEGER DEFAULT 0, last_sync_at TEXT,
    next_sync_at TEXT, cursor TEXT, last_error TEXT, created_at TEXT NOT NULL);
CREATE TABLE people (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, external_id TEXT NOT NULL,
    name TEXT, job_role TEXT, department TEXT, facility TEXT,
    active INTEGER DEFAULT 1, updated_at TEXT, UNIQUE (connection_id, external_id));
CREATE TABLE courses (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, external_id TEXT NOT NULL,
    title TEXT NOT NULL, description TEXT, category TEXT,
    content_updated_at TEXT, required INTEGER DEFAULT 0, updated_at TEXT,
    UNIQUE (connection_id, external_id));
CREATE TABLE completions (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, external_id TEXT NOT NULL,
    person_id TEXT, course_id TEXT, person_external_id TEXT,
    course_external_id TEXT, status TEXT, completed_at TEXT, due_at TEXT,
    UNIQUE (connection_id, external_id));
CREATE TABLE evidence_records (
    id TEXT PRIMARY KEY, connection_id TEXT, external_id TEXT,
    evidence_type TEXT NOT NULL, subject TEXT, source TEXT, occurred_at TEXT,
    owner TEXT, status TEXT, entity_id TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE sources (
    id TEXT PRIMARY KEY, source_type TEXT NOT NULL, title TEXT NOT NULL,
    publisher TEXT, authority TEXT, jurisdiction TEXT, publication_date TEXT,
    effective_date TEXT, version TEXT, retrieval_date TEXT, reference_url TEXT,
    content_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE documents (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, document_type TEXT NOT NULL,
    title TEXT NOT NULL, version TEXT, effective_date TEXT,
    status TEXT DEFAULT 'ACTIVE', owner TEXT, department TEXT,
    text_sha256 TEXT NOT NULL, char_count INTEGER NOT NULL, storage_path TEXT,
    canonical_path TEXT, metadata TEXT, created_at TEXT NOT NULL);
INSERT INTO schema_meta VALUES (3, '2026-01-01T00:00:00+00:00');
INSERT INTO connections VALUES
    ('con1','mock_lms','Demo LMS','LMS','SYNCED','none','{}',1,
     NULL,NULL,NULL,NULL,'2026-01-01T00:00:00+00:00');
INSERT INTO people (id, connection_id, external_id, name, job_role)
    VALUES ('per1','con1','E100001','A Nurse','Registered Nurse');
INSERT INTO courses (id, connection_id, external_id, title)
    VALUES ('crs1','con1','CS-101','Controlled Substance Safety');
INSERT INTO completions (id, connection_id, external_id, person_id, course_id,
                         person_external_id, course_external_id, status)
    VALUES ('cmp1','con1','E100001:CS-101','per1','crs1','E100001','CS-101','COMPLETED');
"""


def _v3_database() -> Path:
    path = Path(tempfile.mkdtemp()) / "veris.db"
    db = sqlite3.connect(path)
    db.executescript(V3)
    db.commit()
    db.close()
    return path


def test_an_existing_database_migrates_forward():
    store = Store(_v3_database())
    assert store.schema_version() == SCHEMA_VERSION


def test_migration_preserves_the_customers_data():
    """The point of migrating rather than recreating."""
    store = Store(_v3_database())
    person = store.one("SELECT * FROM people WHERE id = 'per1'")
    assert person["name"] == "A Nurse"
    assert person["source_id"] == "E100001", "the vendor id survived the rename"
    completion = store.one("SELECT * FROM completions WHERE id = 'cmp1'")
    assert completion["person_source_id"] == "E100001"
    assert completion["course_source_id"] == "CS-101"
    assert completion["person_id"] == "per1", "the resolved link survived"


def test_migration_is_idempotent():
    """Reopening the database must not reapply anything."""
    path = _v3_database()
    Store(path).db.close()
    store = Store(path)
    assert store.schema_version() == SCHEMA_VERSION
    assert store.one("SELECT * FROM people WHERE id = 'per1'")["source_id"] == "E100001"
    versions = [r["version"] for r in store.q("SELECT version FROM schema_meta")]
    assert versions.count(SCHEMA_VERSION) == 1, f"migration reapplied: {versions}"


def test_a_fresh_database_matches_a_migrated_one():
    """Two databases that arrived at the same version by different routes must
    have the same shape, or a bug will only appear for upgrading customers."""
    migrated = Store(_v3_database())
    fresh = Store(Path(tempfile.mkdtemp()) / "fresh.db")
    for table in ("people", "courses", "completions", "evidence_records",
                  "documents", "sources"):
        def columns(store):
            return {r[1] for r in store.db.execute(f"PRAGMA table_info({table})")}
        assert columns(migrated) == columns(fresh), (
            f"{table}: {columns(migrated) ^ columns(fresh)}")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{'all passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)
