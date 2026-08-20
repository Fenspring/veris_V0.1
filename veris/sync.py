"""The synchronization engine.

Connectors yield pages of normalized records; this turns them into rows, keeps a
checkpoint, and survives interruption. A hospital's policy system holds tens of
thousands of records and their network will drop mid-sync, so resumability is a
correctness requirement rather than an optimisation.

    connect → authenticate → discover → baseline → incremental → change detection

Retries use exponential backoff with jitter, and a rate-limited vendor is
obeyed rather than hammered — getting a customer's API key revoked is a worse
outcome than a slow sync.

Two record kinds are handled differently on purpose (Decision 0007):

- **Operational facts** (people, courses, completions) become normalized rows.
- **Knowledge** (policies, requirements) becomes a document. When the source
  system gives metadata but no text, the document is recorded as
  *metadata-only*: Veris knows the policy exists, who owns it and when it is
  due for review, and says plainly that it has not read it. That state is not a
  defect — it is the honest majority case at first connection, and it is enough
  to find a policy with no owner or an overdue review.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .connectors.base import (
    HEALTH_STATES, ConnectorError, ConnectorHealth, RateLimited, TransientError,
    registry,
)
from .store import Store, new_id, now

MAX_ATTEMPTS = 4
BASE_BACKOFF = 1.5

# Veris's own identifier namespace. Every normalized row gets an id minted here,
# never the vendor's key. The vendor's key is kept verbatim in `source_id`.
#
# The id is still *derived* from the vendor's key, because a re-sync has to
# update a row rather than duplicate it. Derivation is not the same as adoption:
# the id is namespaced to Veris and to the connection, so two systems that
# happen to number their people identically produce two rows, a vendor that
# renumbers does not silently rewrite Veris's history, and nothing downstream
# can parse a Veris id back into a vendor id and act on it.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://veris.health/id")


@dataclass
class SyncReport:
    connection_id: str
    run_id: str
    kind: str
    status: str
    discovered: int = 0
    synced: int = 0
    failed: int = 0
    attempts: int = 1
    error: str = ""
    counts: dict | None = None


def _redact(message: str) -> str:
    """Never let a credential reach a log or a database row.

    Errors from HTTP clients routinely embed the request URL, and vendor APIs
    routinely put keys in query strings.
    """
    import re
    text = str(message)
    # Bearer first: the generic rule below would otherwise consume only the word
    # "Bearer" as the value and leave the token itself in the string.
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)"
                  r"\s*[=:]\s*\S+", r"\1=[redacted]", text)
    return text[:500]


def _backoff(attempt: int, hint: float | None = None) -> float:
    if hint:
        return min(hint, 60.0)
    return min(BASE_BACKOFF ** attempt + random.random(), 30.0)


class SyncEngine:
    def __init__(self, store: Store, sleep=time.sleep, data_dir: Path | str | None = None):
        self.store = store
        self._sleep = sleep  # injectable so tests do not actually wait
        # Where canonical text and retained artifacts live. A connector that
        # returns document *text* goes through the same ingest path as an
        # uploaded file, so it needs the same place to freeze it.
        self.data_dir = Path(data_dir or os.environ.get("VERIS_DATA_DIR", "data"))

    # --- connection lifecycle ------------------------------------------------

    def connect(self, connector_id: str, name: str = "",
                credentials: dict | None = None,
                config: dict | None = None) -> dict:
        """Authenticate and discover. Returns what the customer sees next."""
        info = registry.get(connector_id)
        if not info:
            raise ConnectorError(f"Unknown connector: {connector_id}")
        connector = registry.create(connector_id, config or {})

        auth = connector.authenticate(credentials or {})
        if not auth.ok:
            state = "AUTHENTICATION_REQUIRED" if auth.requires_user_action else "ERROR"
            connection_id = self._upsert_connection(info, name, state, config,
                                                    error=auth.message)
            return {"connection_id": connection_id, "state": state,
                    "message": auth.message, "discovery": None}

        discovery = connector.discover()
        connection_id = self._upsert_connection(info, name, "CONNECTED", config)
        self.store.log("connection_established",
                       f"{info.name} ({info.id}) · {discovery.total} records discovered")
        self.store.commit()
        return {
            "connection_id": connection_id,
            "state": "CONNECTED",
            "message": auth.message,
            "discovery": {"counts": discovery.counts, "notes": discovery.notes,
                          "total": discovery.total},
        }

    def _upsert_connection(self, info, name: str, state: str,
                           config: dict | None, error: str = "") -> str:
        existing = self.store.one(
            "SELECT id FROM connections WHERE connector_id = ?", (info.id,))
        cid = existing["id"] if existing else new_id("con")
        self.store._insert("connections", {
            "id": cid, "connector_id": info.id,
            "name": name or info.name, "category": info.category,
            "status": state, "auth_method": info.auth_methods[0],
            # Config never holds credentials; those live in the OS keychain.
            "config": json.dumps(config or {}),
            "is_mock": int(info.is_mock),
            "last_sync_at": None, "next_sync_at": None, "cursor": None,
            "last_error": _redact(error) if error else None,
            "created_at": now(),
        })
        self.store.commit()
        return cid

    def disconnect(self, connection_id: str) -> None:
        conn = self.store.one("SELECT * FROM connections WHERE id = ?", (connection_id,))
        if not conn:
            raise ConnectorError("Connection not found")
        connector = registry.create(conn["connector_id"], json.loads(conn["config"] or "{}"))
        connector.disconnect()
        self.store.db.execute(
            "UPDATE connections SET status='DISCONNECTED', cursor=NULL WHERE id=?",
            (connection_id,))
        self.store.log("connection_disconnected", conn["name"])
        self.store.commit()

    # --- health --------------------------------------------------------------

    def health(self, connection_id: str, probe: bool = True) -> ConnectorHealth:
        """One shape for every connection (§3).

        Everything the customer needs to answer "is this working?" — reachable,
        authenticated, when it last ran, what it delivered, and what it declared
        but is no longer delivering. That last one is the reason this is not
        just a status string: a connection can be perfectly healthy and have
        quietly stopped returning completions, and only the intelligence layer
        notices, because only it knows what it was depending on.
        """
        conn = self.store.one("SELECT * FROM connections WHERE id = ?", (connection_id,))
        if not conn:
            raise ConnectorError("Connection not found")
        info = registry.get(conn["connector_id"])
        declared = tuple(info.capabilities) if info else ()

        state, message, latency = conn["status"], "", None
        authenticated = conn["status"] in ("CONNECTED", "SYNCING", "SYNCED", "WARNING")
        if probe:
            try:
                connector = registry.create(conn["connector_id"],
                                            json.loads(conn["config"] or "{}"))
                connector.authenticate({})
                started = time.monotonic()
                status = connector.health_check()
                latency = (status.latency_ms if status.latency_ms is not None
                           else int((time.monotonic() - started) * 1000))
                message = status.message
                authenticated = status.state not in (
                    "DISCONNECTED", "AUTHENTICATION_REQUIRED", "ERROR")
                # A live probe outranks a stored status, except that a stored
                # WARNING or ERROR from the last sync is a real finding about
                # the data and is not cleared by the source being reachable now.
                if conn["status"] not in ("WARNING", "ERROR"):
                    state = status.state
            except ConnectorError as e:
                state, message, authenticated = "ERROR", _redact(str(e)), False
            except Exception as e:
                state, message, authenticated = "ERROR", _redact(str(e)), False

        runs = self.store.q(
            "SELECT * FROM sync_runs WHERE connection_id = ?"
            " ORDER BY started_at DESC LIMIT 20", (connection_id,))
        last_run = None
        if runs:
            r = runs[0]
            last_run = {"id": r["id"], "kind": r["kind"], "status": r["status"],
                        "started_at": r["started_at"], "finished_at": r["finished_at"],
                        "synced": r["synced"], "failed": r["failed"],
                        "attempts": r["attempts"]}
        failures = 0
        for r in runs:
            if r["status"] in ("FAILED", "PARTIAL"):
                failures += 1
            else:
                break

        records = self._record_counts(connection_id)
        delivered = self._delivered_capabilities(connection_id, records)
        # Nothing has run yet, so nothing is degraded — it is merely unstarted.
        degraded = (tuple(c for c in declared if c not in delivered)
                    if any(r["status"] != "RUNNING" for r in runs) else ())

        return ConnectorHealth(
            connection_id=connection_id, connector_id=conn["connector_id"],
            name=conn["name"], category=conn["category"],
            state=state if state in HEALTH_STATES else conn["status"],
            message=message, is_mock=bool(conn["is_mock"]),
            authenticated=authenticated, auth_method=conn["auth_method"] or "",
            capabilities=tuple(delivered) if delivered else declared,
            degraded_capabilities=degraded,
            last_sync_at=conn["last_sync_at"], next_sync_at=conn["next_sync_at"],
            last_run=last_run, consecutive_failures=failures, records=records,
            latency_ms=latency, error=conn["last_error"] or "")

    def health_all(self, probe: bool = True) -> list[ConnectorHealth]:
        return [self.health(c["id"], probe=probe)
                for c in self.store.q("SELECT id FROM connections ORDER BY category, name")]

    def _record_counts(self, connection_id: str) -> dict[str, int]:
        counts = {}
        for table in ("people", "courses", "completions", "evidence_records"):
            counts[table] = self.store.q(
                f"SELECT COUNT(*) n FROM {table} WHERE connection_id = ?",
                (connection_id,))[0]["n"]
        counts["documents"] = self.store.q(
            "SELECT COUNT(*) n FROM documents WHERE json_extract(metadata,'$.connection_id') = ?",
            (connection_id,))[0]["n"]
        return {k: v for k, v in counts.items() if v}

    def _delivered_capabilities(self, connection_id: str,
                                records: dict[str, int]) -> set[str]:
        """What this connection has actually supplied, as opposed to declared.

        Declared capability is a promise about the vendor; delivered capability
        is a fact about this customer's data. The intelligence layer uses the
        second, because an agent that runs on a promise produces a finding about
        nothing.
        """
        delivered: set[str] = set()
        if records.get("courses"):
            delivered.add("course_catalog")
        if records.get("people"):
            delivered.add("person_roster")
        if records.get("completions"):
            delivered.add("completion_records")
        docs = self.store.q(
            "SELECT document_type, char_count FROM documents"
            " WHERE json_extract(metadata,'$.connection_id') = ?", (connection_id,))
        for d in docs:
            if d["document_type"] in ("POLICY", "PROCEDURE"):
                delivered.add("policy_metadata")
                if d["char_count"]:
                    delivered.add("policy_text")
            elif d["document_type"] in ("STANDARD", "REGULATION"):
                delivered.add("standard_metadata")
                if d["char_count"]:
                    delivered.add("standard_text")
        for row in self.store.q(
                "SELECT DISTINCT evidence_type FROM evidence_records"
                " WHERE connection_id = ?", (connection_id,)):
            delivered.add({"ACKNOWLEDGMENT": "acknowledgments",
                           "AUDIT": "audit_results"}.get(
                               row["evidence_type"], "audit_results"))
        return delivered

    def connected_capabilities(self) -> dict[str, list[str]]:
        """Capability → what is delivering it, across everything Veris holds.

        This is what an agent consults before it runs, and what the dashboard
        renders as "what Veris cannot assess".

        Documents uploaded directly count. A hospital that drags a policy PDF in
        has supplied policy text as surely as a policy system would have, and a
        capability model that only counted API connections would tell them Veris
        cannot quote their policies while it was quoting them.
        """
        out: dict[str, list[str]] = {}
        for conn in self.store.q(
                "SELECT id, name FROM connections"
                " WHERE status IN ('CONNECTED','SYNCED','WARNING')"):
            for capability in self._delivered_capabilities(
                    conn["id"], self._record_counts(conn["id"])):
                out.setdefault(capability, []).append(conn["name"])

        for row in self.store.q(
                "SELECT document_type, MAX(char_count) chars FROM documents"
                " WHERE json_extract(metadata,'$.connection_id') IS NULL"
                " GROUP BY document_type"):
            kind = row["document_type"]
            for capability, applies in (
                    ("policy_metadata", kind in ("POLICY", "PROCEDURE")),
                    ("policy_text", kind in ("POLICY", "PROCEDURE") and row["chars"]),
                    ("standard_metadata", kind in ("STANDARD", "REGULATION")),
                    ("standard_text", kind in ("STANDARD", "REGULATION") and row["chars"]),
                    ("education_content", kind in ("EDUCATION", "COMPETENCY")
                     and row["chars"])):
                if applies:
                    out.setdefault(capability, [])
                    if "Uploaded documents" not in out[capability]:
                        out[capability].append("Uploaded documents")
        return out

    # --- synchronization -----------------------------------------------------

    def run(self, connection_id: str, kind: str = "FULL",
            since: str | None = None) -> SyncReport:
        conn = self.store.one("SELECT * FROM connections WHERE id = ?", (connection_id,))
        if not conn:
            raise ConnectorError("Connection not found")

        connector = registry.create(conn["connector_id"],
                                    json.loads(conn["config"] or "{}"))
        connector.authenticate({})

        run_id = new_id("run")
        cursor_before = conn["cursor"] if kind == "INCREMENTAL" else None
        self.store._insert("sync_runs", {
            "id": run_id, "connection_id": connection_id, "kind": kind,
            "status": "RUNNING", "started_at": now(), "finished_at": None,
            "discovered": 0, "synced": 0, "failed": 0, "attempts": 1,
            "cursor_before": cursor_before, "cursor_after": None,
            "error": None, "detail": None,
        })
        self.store.db.execute("UPDATE connections SET status='SYNCING' WHERE id=?",
                              (connection_id,))
        self.store.commit()

        synced = failed = 0
        attempts = 1
        counts: dict[str, int] = {}
        cursor = cursor_before
        error = ""
        status = "SUCCEEDED"

        # Retry and resumability are the same mechanism: a failed attempt
        # re-invokes sync() from the last checkpoint rather than re-advancing a
        # generator that has already raised, which cannot be resumed.
        while True:
            try:
                for page in connector.sync(since=since, cursor=cursor):
                    for record in page.records:
                        try:
                            kind_key = self._apply(connection_id, record,
                                                   conn["connector_id"])
                            counts[kind_key] = counts.get(kind_key, 0) + 1
                            synced += 1
                        except Exception as e:        # one bad record must not
                            failed += 1                # abandon the whole sync
                            self.store.log("record_rejected", _redact(str(e)))
                    if page.cursor:
                        cursor = page.cursor
                        self.store.db.execute(
                            "UPDATE connections SET cursor=? WHERE id=?",
                            (cursor, connection_id))
                        self.store.commit()
                break
            except (RateLimited, TransientError) as e:
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    status, error = "FAILED", _redact(str(e))
                    break
                hint = getattr(e, "retry_after", None)
                self.store.log("sync_retry",
                               f"attempt {attempts} after {type(e).__name__}")
                self._sleep(_backoff(attempts, hint))
            except ConnectorError as e:
                status, error = "FAILED", _redact(str(e))
                break
            except Exception as e:
                status, error = "FAILED", _redact(str(e))
                break

        if status == "SUCCEEDED" and failed:
            status = "PARTIAL"

        next_sync = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(
            timespec="seconds")
        self.store.db.execute(
            "UPDATE sync_runs SET status=?, finished_at=?, discovered=?, synced=?,"
            " failed=?, attempts=?, cursor_after=?, error=?, detail=? WHERE id=?",
            (status, now(), synced + failed, synced, failed, max(attempts, 1),
             cursor, error or None, json.dumps(counts), run_id))
        self.store.db.execute(
            "UPDATE connections SET status=?, last_sync_at=?, next_sync_at=?,"
            " last_error=? WHERE id=?",
            ("SYNCED" if status == "SUCCEEDED" else
             ("WARNING" if status == "PARTIAL" else "ERROR"),
             now(), next_sync, error or None, connection_id))
        repaired = self.resolve_pending_links()
        self.store.log("sync_completed",
                       f"{conn['name']} · {kind} · {status} · {synced} records"
                       + (f" · {repaired} pending references resolved" if repaired else ""))
        self.store.commit()

        return SyncReport(connection_id, run_id, kind, status,
                          discovered=synced + failed, synced=synced, failed=failed,
                          attempts=max(attempts, 1), error=error, counts=counts)

    # --- normalization -------------------------------------------------------

    def _apply(self, connection_id: str, record: dict,
               source_system: str = "") -> str:
        kind = record.get("_type")
        handler = {
            "person": self._apply_person,
            "course": self._apply_course,
            "completion": self._apply_completion,
            "knowledge_document": self._apply_knowledge_document,
            "policy_record": self._apply_policy,
            "requirement_record": self._apply_requirement,
            "evidence_record": self._apply_evidence,
        }.get(kind)
        if not handler:
            raise ValueError(f"Unknown record type: {kind!r}")
        handler(connection_id, record, source_system)
        return kind

    @staticmethod
    def _origin(record: dict, source_system: str) -> dict:
        """External identity, preserved on every normalized row (§7).

        A connector may name its own record type and report when the source last
        changed the record; where it does not, Veris records the normalized type
        and leaves the vendor timestamp null rather than substituting its own.
        Import time is always Veris's own clock and is labelled as such.
        """
        return {
            "source_system": source_system or None,
            "source_record_type": record.get("_source_type") or record.get("_type"),
            "source_updated_at": (record.get("_source_updated_at")
                                  or record.get("content_updated_at")),
            "imported_at": now(),
        }

    def _apply_person(self, connection_id: str, r: dict, system: str = "") -> None:
        source_id = _require_source_id(r)
        self.store._insert("people", {
            "id": _stable_id("per", connection_id, source_id),
            "connection_id": connection_id, "source_id": source_id,
            **self._origin(r, system),
            "name": r.get("name"), "job_role": r.get("job_role"),
            "department": r.get("department"), "facility": r.get("facility"),
            "active": int(r.get("active", True)), "updated_at": now(),
        })

    def _apply_course(self, connection_id: str, r: dict, system: str = "") -> None:
        source_id = _require_source_id(r)
        self.store._insert("courses", {
            "id": _stable_id("crs", connection_id, source_id),
            "connection_id": connection_id, "source_id": source_id,
            **self._origin(r, system),
            "title": r["title"], "description": r.get("description"),
            "category": r.get("category"),
            "content_updated_at": r.get("content_updated_at"),
            "required": int(r.get("required", False)), "updated_at": now(),
        })

    def _apply_completion(self, connection_id: str, r: dict, system: str = "") -> None:
        source_id = _require_source_id(r)
        person_ext = r.get("person_external_id") or r.get("person_source_id")
        course_ext = r.get("course_external_id") or r.get("course_source_id")
        self.store._insert("completions", {
            "id": _stable_id("cmp", connection_id, source_id),
            "connection_id": connection_id, "source_id": source_id,
            **self._origin(r, system),
            "person_id": self._resolve("people", connection_id, person_ext, "per"),
            "course_id": self._resolve("courses", connection_id, course_ext, "crs"),
            "person_source_id": person_ext, "course_source_id": course_ext,
            "status": r.get("status"), "completed_at": r.get("completed_at"),
            "due_at": r.get("due_at"),
        })

    def _resolve(self, table: str, connection_id: str,
                 source_id: str | None, prefix: str) -> str | None:
        """Internal id for an external reference, or None if it has not arrived.

        Sync order across systems is not guaranteed — a completions export can
        precede the roster, and the roster may come from a different connector
        entirely. Dropping the row would lose data over timing.
        """
        if not source_id:
            return None
        candidate = _stable_id(prefix, connection_id, source_id)
        if self.store.one(f"SELECT id FROM {table} WHERE id = ?", (candidate,)):
            return candidate
        # The reference may point at a record that arrived through a different
        # connection — a roster from the HR system, completions from the LMS.
        row = self.store.one(
            f"SELECT id FROM {table} WHERE source_id = ?", (source_id,))
        return row["id"] if row else None

    def resolve_pending_links(self) -> int:
        """Fill in references whose other side has since arrived. Run after any
        sync so a late roster repairs earlier completions."""
        fixed = 0
        for row in self.store.q(
                "SELECT * FROM completions WHERE person_id IS NULL OR course_id IS NULL"):
            person_id = row["person_id"] or self._resolve(
                "people", row["connection_id"], row["person_source_id"], "per")
            course_id = row["course_id"] or self._resolve(
                "courses", row["connection_id"], row["course_source_id"], "crs")
            if person_id != row["person_id"] or course_id != row["course_id"]:
                self.store.db.execute(
                    "UPDATE completions SET person_id=?, course_id=? WHERE id=?",
                    (person_id, course_id, row["id"]))
                fixed += 1
        self.store.commit()
        return fixed

    def _apply_knowledge_document(self, connection_id: str, r: dict,
                                  system: str = "") -> None:
        """A document with its text, from a source that supplies text.

        Routed through the same pipeline an uploaded file uses, deliberately.
        A regulation fetched over HTTP and the same regulation dropped in as a
        PDF must be indistinguishable downstream: same frozen canonical text,
        same byte-verified spans, same citations. Anything less would make a
        finding's trustworthiness depend on how the document arrived.
        """
        from .pipeline import IngestError, ingest_text

        source_id = _require_source_id(r)
        text = r.get("text") or ""
        if not text.strip():
            raise ValueError(f"{source_id} arrived with no text")

        meta = {k: v for k, v in r.items()
                if k in ("publisher", "authority", "jurisdiction", "version",
                         "effective_date", "reference_url", "retrieval_date",
                         "source_title", "standard", "owner", "department",
                         "status")}
        meta["source_type"] = r.get("source_type") or (
            "REGULATION" if r.get("document_type") == "REGULATION"
            else "ACCREDITATION_STANDARD")
        meta["connection_id"] = connection_id
        origin = self._origin(r, system) | {"source_ref": source_id}
        try:
            ingest_text(self.store, text, meta, self.data_dir,
                        title=r.get("title") or source_id,
                        doc_type=r.get("document_type", "REGULATION"),
                        origin=origin)
        except IngestError as e:
            raise ValueError(str(e)) from None

    def _apply_policy(self, connection_id: str, r: dict, system: str = "") -> None:
        """A policy from a policy system. Text may or may not come with it."""
        text = r.get("text") or ""
        vendor_id = _require_source_id(r)
        origin = self._origin(r, system)
        existing = self.store.one(
            "SELECT id FROM documents WHERE title = ? AND COALESCE(version,'') = ?",
            (r["title"], r.get("version") or ""))
        if existing:
            # Already ingested from a file, which has the text. Record where the
            # policy system knows it as, so the document is traceable to both.
            self.store.db.execute(
                "UPDATE documents SET source_system=COALESCE(source_system,?),"
                " source_record_type=COALESCE(source_record_type,?),"
                " source_ref=COALESCE(source_ref,?),"
                " source_updated_at=COALESCE(source_updated_at,?),"
                " imported_at=COALESCE(imported_at,?) WHERE id=?",
                (origin["source_system"], origin["source_record_type"], vendor_id,
                 origin["source_updated_at"], origin["imported_at"], existing["id"]))
            return

        source_id = self.store.add_source(
            source_type="ORGANIZATIONAL", title=r["title"],
            publisher=r.get("publisher") or "Connected policy system",
            authority="ORGANIZATIONAL", jurisdiction=None,
            effective_date=r.get("effective_date"), version=r.get("version"),
            retrieval_date=now()[:10], content_hash=_hash(r),
            source_id=vendor_id, **origin)
        self.store.add_document(
            source_id=source_id, document_type="POLICY", title=r["title"],
            version=r.get("version"), effective_date=r.get("effective_date"),
            status=r.get("status", "ACTIVE"), owner=r.get("owner") or None,
            department=r.get("department"),
            text_sha256=_hash(r), char_count=len(text),
            storage_path=None, canonical_path=None,
            source_ref=vendor_id, **origin,
            metadata=json.dumps({
                "connection_id": connection_id,
                "next_review_date": r.get("next_review_date"),
                # The honest majority case at first connection.
                "metadata_only": not text,
            }))

    def _apply_requirement(self, connection_id: str, r: dict,
                           system: str = "") -> None:
        """A requirement announced by a standards feed.

        One metadata-only document per standard *revision*, not per requirement:
        a feed reporting six requirements describes one revision of one standard,
        and creating six documents would make them look like six versions of it.

        No entities are created. The feed names the requirements but does not
        supply their text, and an entity without a verified span cannot be cited
        — which is the invariant the whole trust model rests on. Veris records
        that the requirement exists and says plainly that it has not read it.
        """
        locator = f"{r['standard']} {r['element']}"
        if self.store.one("SELECT id FROM entities WHERE locator LIKE ?",
                          (f"%{locator}",)):
            return  # already present, with text, from an ingested document

        revision = r.get("revision") or ""
        title = f"{r['standard']} (feed)"
        existing = self.store.one(
            "SELECT id, metadata FROM documents WHERE title = ?"
            " AND COALESCE(version,'') = ?", (title, revision))

        if existing:
            meta = json.loads(existing["metadata"] or "{}")
            elements = meta.get("elements", [])
            if r["element"] not in elements:
                elements.append(r["element"])
                meta["elements"] = elements
                self.store.db.execute("UPDATE documents SET metadata=? WHERE id=?",
                                      (json.dumps(meta), existing["id"]))
            return

        origin = self._origin(r, system)
        source_id = self.store.add_source(
            source_type="ACCREDITATION_STANDARD", title=r["standard"],
            publisher=r.get("publisher") or "Connected standards feed",
            authority="ACCREDITOR", version=revision,
            effective_date=r.get("effective_date"), retrieval_date=now()[:10],
            content_hash=_hash({"standard": r["standard"], "revision": revision}),
            source_id=r["standard"], **origin)
        self.store.add_document(
            source_id=source_id, document_type="STANDARD", title=title,
            version=revision, effective_date=r.get("effective_date"),
            text_sha256=_hash({"standard": r["standard"], "revision": revision}),
            char_count=0, source_ref=r["standard"], **origin,
            metadata=json.dumps({"connection_id": connection_id,
                                 "elements": [r["element"]],
                                 "metadata_only": True}))

    def _apply_evidence(self, connection_id: str, r: dict, system: str = "") -> None:
        source_id = _require_source_id(r)
        self.store._insert("evidence_records", {
            "id": _stable_id("evr", connection_id, source_id),
            "connection_id": connection_id, "source_id": source_id,
            **self._origin(r, system),
            "evidence_type": r.get("evidence_type", "ATTESTATION"),
            "subject": r.get("subject"), "source": r.get("source"),
            "occurred_at": r.get("occurred_at"), "owner": r.get("owner"),
            "status": r.get("status"), "entity_id": r.get("entity_id"),
            "detail": json.dumps(r.get("detail") or {}), "created_at": now(),
        })



def _require_source_id(record: dict) -> str:
    """The vendor's identifier for this record.

    A record with no identifier of its own cannot be re-synced idempotently —
    the next sync would either duplicate it or overwrite something else — so it
    is rejected here and isolated as a failed record rather than admitted.
    """
    source_id = record.get("source_id") or record.get("external_id")
    if not source_id:
        raise ValueError(f"{record.get('_type')} record has no source identifier")
    return str(source_id)


def _stable_id(prefix: str, connection_id: str, source_id: str) -> str:
    """A Veris identifier, deterministic per connection and source record.

    Deterministic so a re-sync updates rows instead of duplicating them; minted
    in Veris's namespace so the vendor's key is never the primary identity.
    """
    return f"{prefix}_{uuid.uuid5(NAMESPACE, f'{connection_id}:{source_id}').hex[:12]}"


def _hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
