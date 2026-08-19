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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .connectors.base import (
    ConnectorError, RateLimited, TransientError, registry,
)
from .store import Store, new_id, now

MAX_ATTEMPTS = 4
BASE_BACKOFF = 1.5


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
    def __init__(self, store: Store, sleep=time.sleep):
        self.store = store
        self._sleep = sleep  # injectable so tests do not actually wait

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
                            kind_key = self._apply(connection_id, record)
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
        self.store.log("sync_completed",
                       f"{conn['name']} · {kind} · {status} · {synced} records")
        self.store.commit()

        return SyncReport(connection_id, run_id, kind, status,
                          discovered=synced + failed, synced=synced, failed=failed,
                          attempts=max(attempts, 1), error=error, counts=counts)

    # --- normalization -------------------------------------------------------

    def _apply(self, connection_id: str, record: dict) -> str:
        kind = record.get("_type")
        handler = {
            "person": self._apply_person,
            "course": self._apply_course,
            "completion": self._apply_completion,
            "policy_record": self._apply_policy,
            "requirement_record": self._apply_requirement,
            "evidence_record": self._apply_evidence,
        }.get(kind)
        if not handler:
            raise ValueError(f"Unknown record type: {kind!r}")
        handler(connection_id, record)
        return kind

    def _apply_person(self, connection_id: str, r: dict) -> None:
        self.store._insert("people", {
            "id": _stable_id("per", connection_id, r["external_id"]),
            "connection_id": connection_id, "external_id": r["external_id"],
            "name": r.get("name"), "job_role": r.get("job_role"),
            "department": r.get("department"), "facility": r.get("facility"),
            "active": int(r.get("active", True)), "updated_at": now(),
        })

    def _apply_course(self, connection_id: str, r: dict) -> None:
        self.store._insert("courses", {
            "id": _stable_id("crs", connection_id, r["external_id"]),
            "connection_id": connection_id, "external_id": r["external_id"],
            "title": r["title"], "description": r.get("description"),
            "category": r.get("category"),
            "content_updated_at": r.get("content_updated_at"),
            "required": int(r.get("required", False)), "updated_at": now(),
        })

    def _apply_completion(self, connection_id: str, r: dict) -> None:
        self.store._insert("completions", {
            "id": _stable_id("cmp", connection_id, r["external_id"]),
            "connection_id": connection_id, "external_id": r["external_id"],
            "person_id": _stable_id("per", connection_id, r["person_external_id"]),
            "course_id": _stable_id("crs", connection_id, r["course_external_id"]),
            "status": r.get("status"), "completed_at": r.get("completed_at"),
            "due_at": r.get("due_at"),
        })

    def _apply_policy(self, connection_id: str, r: dict) -> None:
        """A policy from a policy system. Text may or may not come with it."""
        text = r.get("text") or ""
        existing = self.store.one(
            "SELECT id FROM documents WHERE title = ? AND COALESCE(version,'') = ?",
            (r["title"], r.get("version") or ""))
        if existing:
            return  # already ingested from a file; the file has the text

        source_id = self.store.add_source(
            source_type="ORGANIZATIONAL", title=r["title"],
            publisher=r.get("publisher") or "Connected policy system",
            authority="ORGANIZATIONAL", jurisdiction=None,
            effective_date=r.get("effective_date"), version=r.get("version"),
            retrieval_date=now()[:10], content_hash=_hash(r))
        self.store.add_document(
            source_id=source_id, document_type="POLICY", title=r["title"],
            version=r.get("version"), effective_date=r.get("effective_date"),
            status=r.get("status", "ACTIVE"), owner=r.get("owner") or None,
            department=r.get("department"),
            text_sha256=_hash(r), char_count=len(text),
            storage_path=None, canonical_path=None,
            metadata=json.dumps({
                "connection_id": connection_id,
                "external_id": r["external_id"],
                "next_review_date": r.get("next_review_date"),
                # The honest majority case at first connection.
                "metadata_only": not text,
            }))

    def _apply_requirement(self, connection_id: str, r: dict) -> None:
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

        source_id = self.store.add_source(
            source_type="ACCREDITATION_STANDARD", title=r["standard"],
            publisher=r.get("publisher") or "Connected standards feed",
            authority="ACCREDITOR", version=revision,
            effective_date=r.get("effective_date"), retrieval_date=now()[:10],
            content_hash=_hash({"standard": r["standard"], "revision": revision}))
        self.store.add_document(
            source_id=source_id, document_type="STANDARD", title=title,
            version=revision, effective_date=r.get("effective_date"),
            text_sha256=_hash({"standard": r["standard"], "revision": revision}),
            char_count=0,
            metadata=json.dumps({"connection_id": connection_id,
                                 "elements": [r["element"]],
                                 "metadata_only": True}))

    def _apply_evidence(self, connection_id: str, r: dict) -> None:
        self.store._insert("evidence_records", {
            "id": _stable_id("evr", connection_id, r["external_id"]),
            "connection_id": connection_id, "external_id": r["external_id"],
            "evidence_type": r.get("evidence_type", "ATTESTATION"),
            "subject": r.get("subject"), "source": r.get("source"),
            "occurred_at": r.get("occurred_at"), "owner": r.get("owner"),
            "status": r.get("status"), "entity_id": r.get("entity_id"),
            "detail": json.dumps(r.get("detail") or {}), "created_at": now(),
        })



def _stable_id(prefix: str, connection_id: str, external_id: str) -> str:
    """Deterministic ids so a re-sync updates rows instead of duplicating them."""
    digest = hashlib.sha256(f"{connection_id}:{external_id}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
