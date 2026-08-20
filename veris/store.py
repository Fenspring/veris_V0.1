"""The Veris domain model.

Relationships are rows, not inferences recomputed per request. That is the
whole point: the organization's knowledge graph is a durable object that can be
reviewed, corrected, cited, and compared against its own past state.

Four things are deliberately explicit in the schema rather than implied:

**Provenance class.** Every relationship and finding records whether it is a
SOURCE_FACT (the document says so), a VERIS_INTERPRETATION (derived structurally
by rules we can explain), a MODEL_INFERENCE (a model judged it), or a
HUMAN_REVIEW (a person decided). CONSTITUTION §5 forbids presenting inference as
fact; a schema column is how that stops depending on anyone's discipline.

**Evidence as its own table.** A piece of evidence is a citable span of a
specific document — not a string copied onto a finding. Entities, relationships
and findings all point at the same evidence rows, so a passage cited in three
places is one row and stays consistent.

**Review as an append-only log.** Decisions are not overwrites. The history of
who decided what, and when, is organizational knowledge (§17).

**Sources carry authority.** Publisher, jurisdiction, authority level, effective
date and version live on the source, because "which of these two conflicting
statements governs?" is unanswerable without them.

SQLite, per Decision 0003: at this corpus size graph traversal is a two-line
join, and the deferral's stated trigger — ~10^5 entities — is three orders of
magnitude away.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# --- Controlled vocabularies -------------------------------------------------

PROVENANCE_CLASSES = (
    "SOURCE_FACT",           # the document states this
    "VERIS_INTERPRETATION",  # derived by explainable structural rules
    "MODEL_INFERENCE",       # a model judged it
    "HUMAN_REVIEW",          # a person decided it
)

ENTITY_TYPES = (
    "REQUIREMENT", "POLICY_STATEMENT", "PROCEDURE_STEP",
    "EDUCATION_OBJECTIVE", "COMPETENCY_CRITERION", "DEFINITION", "GUIDELINE",
)

# What the entity does in the obligation lifecycle (Discovery 0001).
LIFECYCLE_ROLES = (
    "REQUIRES", "COMMITS", "OPERATIONALIZES",
    "TEACHES", "VALIDATES", "MEASURES", "DESCRIBES",
)

RELATIONSHIP_TYPES = (
    "DIRECTLY_ADDRESSES", "PARTIALLY_ADDRESSES", "POTENTIALLY_CONFLICTS_WITH",
    "APPEARS_ALIGNED_WITH", "IMPLEMENTS", "TEACHES", "VALIDATES",
    "DEPENDS_ON", "SUPERSEDES", "AFFECTED_BY",
)

FINDING_TYPES = (
    "POTENTIAL_GAP", "POTENTIAL_CONFLICT", "LIKELY_ALIGNED",
    "INSUFFICIENT_EVIDENCE", "REQUIRES_HUMAN_REVIEW",
)

SEVERITIES = ("HIGH", "MEDIUM", "LOW")
REVIEW_STATUSES = ("PROPOSED", "ACCEPTED", "REJECTED", "NEEDS_REVIEW", "RESOLVED")
CHANGE_TYPES = ("ADDED", "REMOVED", "MODIFIED", "UNCHANGED")

CONNECTION_STATES = (
    "DISCONNECTED", "AUTHENTICATION_REQUIRED", "CONNECTED",
    "SYNCING", "SYNCED", "WARNING", "ERROR",
)
CONNECTOR_CATEGORIES = ("LMS", "POLICY", "REGULATORY", "IDENTITY", "DOCUMENT", "EVIDENCE")

SCHEMA_VERSION = 4

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL
);

-- Where knowledge comes from, and what authority it carries.
CREATE TABLE IF NOT EXISTS sources (
    id               TEXT PRIMARY KEY,
    source_type      TEXT NOT NULL,      -- REGULATION | ACCREDITATION_STANDARD | ORGANIZATIONAL | GUIDELINE
    title            TEXT NOT NULL,
    publisher        TEXT,
    authority        TEXT,               -- FEDERAL | STATE | ACCREDITOR | PROFESSIONAL | ORGANIZATIONAL
    jurisdiction     TEXT,
    publication_date TEXT,
    effective_date   TEXT,
    version          TEXT,
    retrieval_date   TEXT,
    reference_url    TEXT,
    content_hash     TEXT,
    -- External identity (§7). Preserved verbatim, never used as the key.
    source_system    TEXT,               -- connector id the record came from
    source_record_type TEXT,             -- the vendor's own name for it
    source_id        TEXT,               -- the vendor's identifier, unaltered
    source_updated_at TEXT,              -- when the vendor last changed it
    imported_at      TEXT,               -- when Veris read it
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    document_type  TEXT NOT NULL,        -- STANDARD | POLICY | PROCEDURE | EDUCATION | COMPETENCY | ORIENTATION
    title          TEXT NOT NULL,
    version        TEXT,
    effective_date TEXT,
    status         TEXT DEFAULT 'ACTIVE',
    owner          TEXT,
    department     TEXT,
    text_sha256    TEXT NOT NULL,
    char_count     INTEGER NOT NULL,
    storage_path   TEXT,                 -- the original artifact, retained
    canonical_path TEXT,                 -- frozen extracted text; spans index into this
    metadata       TEXT,                 -- JSON
    source_system  TEXT,
    source_record_type TEXT,
    source_ref     TEXT,                 -- the vendor's id ('source_id' is taken
                                         -- here by the FK to sources)
    source_updated_at TEXT,
    imported_at    TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_source ON documents(source_id);

-- A citable span. Evidence is always a location in a document, never a
-- free-floating string.
CREATE TABLE IF NOT EXISTS evidence (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    char_start     INTEGER NOT NULL,
    char_end       INTEGER NOT NULL,
    quote          TEXT NOT NULL,
    location_label TEXT,                 -- human-readable: "§4 Waste Documentation"
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_document ON evidence(document_id);

-- The unit of knowledge. Generalised from Claim (Decision 0001): still bound to
-- a verified span, now carrying provenance and a lifecycle role.
CREATE TABLE IF NOT EXISTS entities (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    evidence_id      TEXT REFERENCES evidence(id) ON DELETE SET NULL,
    entity_type      TEXT NOT NULL,
    role             TEXT NOT NULL,
    locator          TEXT NOT NULL,      -- "NS-CS.02.01 EP 3"
    statement        TEXT NOT NULL,      -- verbatim; must match its evidence span
    subject          TEXT,               -- normalized topic
    actor            TEXT,               -- who is obligated, where stated
    expects_document INTEGER DEFAULT 0,  -- source declares evidence must exist
    crosswalk        TEXT,               -- JSON list of external references
    provenance_class TEXT NOT NULL DEFAULT 'SOURCE_FACT',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entities_document ON entities(document_id);
CREATE INDEX IF NOT EXISTS ix_entities_role ON entities(role);

-- First-class relationships (§5). Reviewable, evidenced, and provenance-tagged.
CREATE TABLE IF NOT EXISTS relationships (
    id                TEXT PRIMARY KEY,
    from_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    confidence        REAL,
    rationale         TEXT,
    provenance_class  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'PROPOSED',
    created_by        TEXT,              -- model name, rule name, or user
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rel_from ON relationships(from_entity_id);
CREATE INDEX IF NOT EXISTS ix_rel_to ON relationships(to_entity_id);

CREATE TABLE IF NOT EXISTS relationship_evidence (
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    evidence_id     TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    PRIMARY KEY (relationship_id, evidence_id)
);

-- A version-to-version difference in an authoritative source.
CREATE TABLE IF NOT EXISTS changes (
    id               TEXT PRIMARY KEY,
    from_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    to_document_id   TEXT REFERENCES documents(id) ON DELETE SET NULL,
    from_entity_id   TEXT REFERENCES entities(id) ON DELETE SET NULL,
    to_entity_id     TEXT REFERENCES entities(id) ON DELETE SET NULL,
    change_type      TEXT NOT NULL,
    locator          TEXT,
    summary          TEXT,               -- grounded description of what changed
    detail           TEXT,               -- JSON: added/removed phrases
    provenance_class TEXT NOT NULL DEFAULT 'VERIS_INTERPRETATION',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_changes_to_doc ON changes(to_document_id);

CREATE TABLE IF NOT EXISTS findings (
    id               TEXT PRIMARY KEY,
    finding_type     TEXT NOT NULL,
    severity         TEXT NOT NULL,
    title            TEXT NOT NULL,
    statement        TEXT NOT NULL,
    missing          TEXT,
    subject_entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
    change_id        TEXT REFERENCES changes(id) ON DELETE CASCADE,
    confidence       REAL,
    provenance_class TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PROPOSED',
    scope            TEXT NOT NULL,      -- what was searched; required for absence claims
    disconfirmed     INTEGER DEFAULT 0,
    recommended_reviewer TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_change ON findings(change_id);
CREATE INDEX IF NOT EXISTS ix_findings_status ON findings(status);

CREATE TABLE IF NOT EXISTS finding_evidence (
    finding_id  TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    role        TEXT,                    -- 'requirement' | 'organizational'
    PRIMARY KEY (finding_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS finding_entities (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (finding_id, entity_id)
);

-- Append-only. A review decision is organizational knowledge (§17), so it is
-- recorded rather than overwritten; `status` on the target is the projection.
CREATE TABLE IF NOT EXISTS reviews (
    id          TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,           -- FINDING | RELATIONSHIP
    target_id   TEXT NOT NULL,
    action      TEXT NOT NULL,           -- ACCEPT | REJECT | NEEDS_REVIEW | COMMENT | ASSIGN | RESOLVE | RETYPE
    reviewer    TEXT NOT NULL,
    comment     TEXT,
    assigned_to TEXT,
    due_date    TEXT,
    new_value   TEXT,                    -- for RETYPE: the corrected relationship type
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_target ON reviews(target_type, target_id);


-- ---------------------------------------------------------------------------
-- Connected external systems.
--
-- Two kinds of external record are kept deliberately apart (Decision 0007):
-- knowledge that makes a normative claim becomes a document with verified-span
-- entities, exactly like an uploaded file; an operational fact about the world
-- becomes a normalized record below. A policy can be cited. "12,842 people
-- completed this course" cannot — it is true, but no document says it, and
-- letting it into the evidence tables would break the property that every
-- citation resolves to text a human can read.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS connections (
    id             TEXT PRIMARY KEY,
    connector_id   TEXT NOT NULL,       -- registry key: 'healthstream', 'mock_lms'
    name           TEXT NOT NULL,       -- what the customer calls it
    category       TEXT NOT NULL,       -- LMS | POLICY | REGULATORY | IDENTITY | DOCUMENT | EVIDENCE
    status         TEXT NOT NULL,       -- see CONNECTION_STATES
    auth_method    TEXT,                -- oauth | api_key | sftp | file | none
    config         TEXT,                -- JSON. NEVER credentials — those live in the OS keychain.
    is_mock        INTEGER DEFAULT 0,   -- surfaced in the UI; a mock is never shown as live
    last_sync_at   TEXT,
    next_sync_at   TEXT,
    cursor         TEXT,                -- checkpoint for resumable incremental sync
    last_error     TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_connections_category ON connections(category);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,        -- DISCOVERY | FULL | INCREMENTAL
    status        TEXT NOT NULL,        -- RUNNING | SUCCEEDED | FAILED | PARTIAL
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    discovered    INTEGER DEFAULT 0,
    synced        INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    attempts      INTEGER DEFAULT 1,
    cursor_before TEXT,
    cursor_after  TEXT,
    error         TEXT,                 -- redacted before storage
    detail        TEXT                  -- JSON summary, no secrets
);
CREATE INDEX IF NOT EXISTS ix_sync_runs_conn ON sync_runs(connection_id, started_at DESC);

-- Operational facts. Linked to the graph, never a source of citations.

CREATE TABLE IF NOT EXISTS people (
    -- `id` is a Veris identifier and always will be. The vendor's identifier
    -- lives in source_id, unaltered, so it can be shown, matched and exported —
    -- but nothing in Veris depends on the vendor's key space staying stable,
    -- and the same person arriving from two systems does not become one row by
    -- accident of shared numbering.
    id            TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    source_system TEXT,
    source_record_type TEXT,
    source_id     TEXT NOT NULL,
    source_updated_at TEXT,
    imported_at   TEXT,
    name          TEXT,
    job_role      TEXT,
    department    TEXT,
    facility      TEXT,
    active        INTEGER DEFAULT 1,
    updated_at    TEXT,
    UNIQUE (connection_id, source_id)
);
CREATE INDEX IF NOT EXISTS ix_people_role ON people(job_role);

CREATE TABLE IF NOT EXISTS courses (
    id            TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    source_system TEXT,
    source_record_type TEXT,
    source_id     TEXT NOT NULL,
    source_updated_at TEXT,
    imported_at   TEXT,
    title         TEXT NOT NULL,
    description   TEXT,
    category      TEXT,
    content_updated_at TEXT,            -- what makes policy/training drift detectable
    required      INTEGER DEFAULT 0,
    updated_at    TEXT,
    UNIQUE (connection_id, source_id)
);

CREATE TABLE IF NOT EXISTS completions (
    id            TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    source_system TEXT,
    source_record_type TEXT,
    source_id     TEXT NOT NULL,
    source_updated_at TEXT,
    imported_at   TEXT,
    -- Internal ids are resolved when the other side of the reference exists.
    -- They stay NULL until then: a completions export may arrive before the
    -- roster, or the people may live in a different system entirely, and
    -- refusing the row would discard data over arrival order.
    person_id     TEXT REFERENCES people(id) ON DELETE SET NULL,
    course_id     TEXT REFERENCES courses(id) ON DELETE SET NULL,
    person_source_id TEXT,
    course_source_id TEXT,
    status        TEXT,                 -- COMPLETED | ASSIGNED | OVERDUE | EXEMPT
    completed_at  TEXT,
    due_at        TEXT,
    UNIQUE (connection_id, source_id)
);
CREATE INDEX IF NOT EXISTS ix_completions_course ON completions(course_id);
CREATE INDEX IF NOT EXISTS ix_completions_status ON completions(status);

-- Evidence that something happened, as distinct from evidence of what a
-- document says (the `evidence` table above). Both are provenance; these are
-- attestations rather than quotations.
CREATE TABLE IF NOT EXISTS evidence_records (
    id            TEXT PRIMARY KEY,
    connection_id TEXT REFERENCES connections(id) ON DELETE CASCADE,
    source_system TEXT,
    source_record_type TEXT,
    source_id     TEXT,
    source_updated_at TEXT,
    imported_at   TEXT,
    evidence_type TEXT NOT NULL,        -- TRAINING_COMPLETION | ACKNOWLEDGMENT | AUDIT | ATTESTATION
    subject       TEXT,
    source        TEXT,
    occurred_at   TEXT,
    owner         TEXT,
    status        TEXT,
    entity_id     TEXT REFERENCES entities(id) ON DELETE SET NULL,
    detail        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_records_entity ON evidence_records(entity_id);

-- A course teaches a requirement or reinforces a policy. Courses are not
-- entities, so this link lives beside the graph rather than inside it.
CREATE TABLE IF NOT EXISTS course_entity_links (
    course_id        TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    entity_id        TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    link_type        TEXT NOT NULL,     -- REINFORCES | TEACHES | VALIDATES
    confidence       REAL,
    rationale        TEXT,
    provenance_class TEXT NOT NULL,
    status           TEXT DEFAULT 'PROPOSED',
    created_at       TEXT NOT NULL,
    PRIMARY KEY (course_id, entity_id, link_type)
);

-- Audit-friendly event log (§26). No PHI, no secrets.
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


# Indexes over columns that migrations introduce. Run after migration rather
# than with the schema: on an existing database the column does not exist yet
# when the schema script runs.
POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_documents_origin ON documents(source_system, source_ref);
CREATE INDEX IF NOT EXISTS ix_people_origin ON people(source_system, source_id);
CREATE INDEX IF NOT EXISTS ix_courses_origin ON courses(source_system, source_id);
"""

# --- Migrations --------------------------------------------------------------
#
# Additive and applied at startup. A hospital that has been running Veris for a
# month has connected systems and reviewed findings; an upgrade that asked them
# to start again would be asking them to discard organizational knowledge.
#
# Each step is a list of statements applied in order inside one transaction. A
# statement that has already been applied is skipped rather than failing, so a
# database that was created fresh at the current version and one that arrived
# here by migration end up identical.

MIGRATIONS: dict[int, list[str]] = {
    # 3 → 4: external identity as first-class columns (§7). Before this the
    # vendor's identifier was a bare `external_id` and the connector that
    # supplied it was recoverable only by joining through connections, which
    # made "where did this row come from?" a query nobody wrote.
    4: [
        "ALTER TABLE people RENAME COLUMN external_id TO source_id",
        "ALTER TABLE courses RENAME COLUMN external_id TO source_id",
        "ALTER TABLE completions RENAME COLUMN external_id TO source_id",
        "ALTER TABLE completions RENAME COLUMN person_external_id TO person_source_id",
        "ALTER TABLE completions RENAME COLUMN course_external_id TO course_source_id",
        "ALTER TABLE evidence_records RENAME COLUMN external_id TO source_id",
        "ALTER TABLE people ADD COLUMN source_system TEXT",
        "ALTER TABLE people ADD COLUMN source_record_type TEXT",
        "ALTER TABLE people ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE people ADD COLUMN imported_at TEXT",
        "ALTER TABLE courses ADD COLUMN source_system TEXT",
        "ALTER TABLE courses ADD COLUMN source_record_type TEXT",
        "ALTER TABLE courses ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE courses ADD COLUMN imported_at TEXT",
        "ALTER TABLE completions ADD COLUMN source_system TEXT",
        "ALTER TABLE completions ADD COLUMN source_record_type TEXT",
        "ALTER TABLE completions ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE completions ADD COLUMN imported_at TEXT",
        "ALTER TABLE evidence_records ADD COLUMN source_system TEXT",
        "ALTER TABLE evidence_records ADD COLUMN source_record_type TEXT",
        "ALTER TABLE evidence_records ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE evidence_records ADD COLUMN imported_at TEXT",
        "ALTER TABLE documents ADD COLUMN source_system TEXT",
        "ALTER TABLE documents ADD COLUMN source_record_type TEXT",
        "ALTER TABLE documents ADD COLUMN source_ref TEXT",
        "ALTER TABLE documents ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE documents ADD COLUMN imported_at TEXT",
        "ALTER TABLE sources ADD COLUMN source_system TEXT",
        "ALTER TABLE sources ADD COLUMN source_record_type TEXT",
        "ALTER TABLE sources ADD COLUMN source_id TEXT",
        "ALTER TABLE sources ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE sources ADD COLUMN imported_at TEXT",
        "CREATE INDEX IF NOT EXISTS ix_documents_origin"
        " ON documents(source_system, source_ref)",
    ],
}

# Tables carrying external identity, and how a row in each is described. Used by
# `record_origin` and by the contract tests that assert nothing arrives from a
# connected system without its origin recorded.
EXTERNAL_IDENTITY_TABLES = ("people", "courses", "completions",
                            "evidence_records", "documents", "sources")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """Thin typed wrapper over SQLite. All SQL lives here."""

    def __init__(self, path: Path | str = "data/veris.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.executescript(POST_MIGRATION_INDEXES)
        self.db.commit()

    # --- schema --------------------------------------------------------------

    def _migrate(self) -> None:
        row = self.db.execute(
            "SELECT version FROM schema_meta ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            # Fresh database: SCHEMA above already describes the current shape.
            self.db.execute("INSERT INTO schema_meta VALUES (?,?)",
                            (SCHEMA_VERSION, now()))
            return
        current = row["version"]
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS.get(version, []):
                try:
                    self.db.execute(statement)
                except sqlite3.OperationalError as e:
                    # Already applied — a column that exists, an index that
                    # exists, a rename already done. Anything else is a real
                    # failure and must not be swallowed.
                    if "duplicate column" in str(e) or "no such column" in str(e):
                        continue
                    raise
            self.db.execute("INSERT INTO schema_meta VALUES (?,?)", (version, now()))
        self.db.commit()

    def schema_version(self) -> int:
        row = self.db.execute(
            "SELECT version FROM schema_meta ORDER BY version DESC LIMIT 1").fetchone()
        return row["version"] if row else 0

    # --- writes --------------------------------------------------------------

    def _insert(self, table: str, row: dict[str, Any]) -> str:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})",
                        tuple(row.values()))
        return row["id"]

    def add_source(self, **kw) -> str:
        kw.setdefault("id", new_id("src"))
        kw.setdefault("created_at", now())
        return self._insert("sources", kw)

    def add_document(self, **kw) -> str:
        kw.setdefault("id", new_id("doc"))
        kw.setdefault("created_at", now())
        if isinstance(kw.get("metadata"), dict):
            kw["metadata"] = json.dumps(kw["metadata"])
        return self._insert("documents", kw)

    def add_evidence(self, document_id: str, char_start: int, char_end: int,
                     quote: str, location_label: str = "") -> str:
        return self._insert("evidence", {
            "id": new_id("ev"), "document_id": document_id,
            "char_start": char_start, "char_end": char_end, "quote": quote,
            "location_label": location_label, "created_at": now(),
        })

    def add_entity(self, **kw) -> str:
        kw.setdefault("id", new_id("ent"))
        kw.setdefault("created_at", now())
        kw.setdefault("provenance_class", "SOURCE_FACT")
        if isinstance(kw.get("crosswalk"), (list, tuple)):
            kw["crosswalk"] = json.dumps(list(kw["crosswalk"]))
        kw["expects_document"] = int(bool(kw.get("expects_document", 0)))
        return self._insert("entities", kw)

    def add_relationship(self, evidence_ids: Iterable[str] = (), **kw) -> str:
        kw.setdefault("id", new_id("rel"))
        kw.setdefault("created_at", now())
        kw.setdefault("status", "PROPOSED")
        rid = self._insert("relationships", kw)
        for ev in evidence_ids:
            self.db.execute(
                "INSERT OR IGNORE INTO relationship_evidence VALUES (?,?)", (rid, ev))
        return rid

    def add_change(self, **kw) -> str:
        kw.setdefault("id", new_id("chg"))
        kw.setdefault("created_at", now())
        if isinstance(kw.get("detail"), (dict, list)):
            kw["detail"] = json.dumps(kw["detail"])
        return self._insert("changes", kw)

    def add_finding(self, evidence: Iterable[tuple[str, str]] = (),
                    entity_ids: Iterable[str] = (), **kw) -> str:
        kw.setdefault("id", new_id("fnd"))
        kw.setdefault("created_at", now())
        kw.setdefault("status", "PROPOSED")
        kw["disconfirmed"] = int(bool(kw.get("disconfirmed", 0)))
        fid = self._insert("findings", kw)
        for ev_id, role in evidence:
            self.db.execute("INSERT OR IGNORE INTO finding_evidence VALUES (?,?,?)",
                            (fid, ev_id, role))
        for ent in entity_ids:
            self.db.execute("INSERT OR IGNORE INTO finding_entities VALUES (?,?)", (fid, ent))
        return fid

    def add_review(self, target_type: str, target_id: str, action: str,
                   reviewer: str, **kw) -> str:
        rid = self._insert("reviews", {
            "id": new_id("rev"), "target_type": target_type, "target_id": target_id,
            "action": action, "reviewer": reviewer,
            "comment": kw.get("comment"), "assigned_to": kw.get("assigned_to"),
            "due_date": kw.get("due_date"), "new_value": kw.get("new_value"),
            "created_at": now(),
        })
        # Project the decision onto the target's status. The log is the record;
        # status is a cached view of the latest decisive action.
        status = {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED",
                  "NEEDS_REVIEW": "NEEDS_REVIEW", "RESOLVE": "RESOLVED"}.get(action)
        table = "findings" if target_type == "FINDING" else "relationships"
        if status:
            self.db.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, target_id))
        if action == "RETYPE" and target_type == "RELATIONSHIP" and kw.get("new_value"):
            self.db.execute(
                "UPDATE relationships SET relationship_type=?, provenance_class=? WHERE id=?",
                (kw["new_value"], "HUMAN_REVIEW", target_id))
        return rid

    def log(self, kind: str, detail: str = "") -> None:
        self._insert("events", {"id": new_id("evt"), "kind": kind,
                                "detail": detail, "created_at": now()})

    def commit(self) -> None:
        self.db.commit()

    # --- reads ---------------------------------------------------------------

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def entity(self, entity_id: str) -> dict | None:
        return self.one("""
            SELECT e.*, d.title AS document_title, d.document_type, d.department,
                   d.owner, d.version AS document_version, d.effective_date,
                   s.title AS source_title, s.publisher, s.authority, s.jurisdiction
            FROM entities e
            JOIN documents d ON d.id = e.document_id
            JOIN sources   s ON s.id = d.source_id
            WHERE e.id = ?""", (entity_id,))

    def neighbourhood(self, entity_id: str) -> list[dict]:
        """One hop out, in both directions — the Knowledge Explorer's query (§10)."""
        return self.q("""
            SELECT r.id AS relationship_id, r.relationship_type, r.confidence,
                   r.rationale, r.status, r.provenance_class, 'OUT' AS direction,
                   e.id AS entity_id, e.locator, e.statement, e.role, e.entity_type,
                   d.title AS document_title, d.document_type, d.department
            FROM relationships r
            JOIN entities e ON e.id = r.to_entity_id
            JOIN documents d ON d.id = e.document_id
            WHERE r.from_entity_id = ?
            UNION ALL
            SELECT r.id, r.relationship_type, r.confidence,
                   r.rationale, r.status, r.provenance_class, 'IN',
                   e.id, e.locator, e.statement, e.role, e.entity_type,
                   d.title, d.document_type, d.department
            FROM relationships r
            JOIN entities e ON e.id = r.from_entity_id
            JOIN documents d ON d.id = e.document_id
            WHERE r.to_entity_id = ?
            ORDER BY relationship_type""", (entity_id, entity_id))

    def finding_detail(self, finding_id: str) -> dict | None:
        f = self.one("SELECT * FROM findings WHERE id = ?", (finding_id,))
        if not f:
            return None
        f["evidence"] = self.q("""
            SELECT ev.*, fe.role AS evidence_role, d.title AS document_title,
                   d.document_type
            FROM finding_evidence fe
            JOIN evidence ev ON ev.id = fe.evidence_id
            JOIN documents d ON d.id = ev.document_id
            WHERE fe.finding_id = ?""", (finding_id,))
        f["entities"] = self.q("""
            SELECT e.id, e.locator, e.statement, e.role, e.entity_type,
                   d.title AS document_title, d.document_type, d.department, d.owner
            FROM finding_entities fx
            JOIN entities e ON e.id = fx.entity_id
            JOIN documents d ON d.id = e.document_id
            WHERE fx.finding_id = ?""", (finding_id,))
        f["reviews"] = self.q(
            "SELECT * FROM reviews WHERE target_type='FINDING' AND target_id=? "
            "ORDER BY created_at", (finding_id,))
        return f

    def record_origin(self, table: str, row_id: str) -> dict | None:
        """Where a row came from, in the vendor's own terms.

        Answers the question an auditor asks and a support engineer asks: this
        number on the screen — which system said it, what did that system call
        it, when did it last change there, and when did we read it.
        """
        if table not in EXTERNAL_IDENTITY_TABLES:
            raise ValueError(f"{table} does not carry external identity")
        ref = "source_ref" if table == "documents" else "source_id"
        row = self.one(
            f"SELECT id, source_system, source_record_type, {ref} AS source_id,"
            f" source_updated_at, imported_at FROM {table} WHERE id = ?", (row_id,))
        if not row:
            return None
        row["veris_id"] = row.pop("id")
        row["origin"] = ("Uploaded or ingested locally" if not row["source_system"]
                         else f"{row['source_system']} · {row['source_record_type']}"
                              f" · {row['source_id']}")
        return row

    def stats(self) -> dict:
        def n(t: str) -> int:
            return self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return {t: n(t) for t in
                ("sources", "documents", "entities", "evidence",
                 "relationships", "changes", "findings", "reviews",
                 "connections", "people", "courses", "completions")}

    def reset(self) -> None:
        for t in ("course_entity_links", "evidence_records", "completions",
                  "courses", "people", "sync_runs", "connections",
                  "reviews", "finding_entities", "finding_evidence", "findings",
                  "changes", "relationship_evidence", "relationships",
                  "entities", "evidence", "documents", "sources", "events"):
            self.db.execute(f"DELETE FROM {t}")
        self.db.commit()
