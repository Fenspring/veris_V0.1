"""The Veris HTTP API.

The UI is a client of this, not a privileged path into it. Everything the
workspace shows is available to a third-party system asking the same question,
which is the point: Veris should eventually be callable as infrastructure (§23).

Security posture for a no-PHI MVP (§26): every input is validated and bounded,
uploads are size- and type-limited and never trusted for their filename, errors
returned to the caller are safe and generic while the detail goes to the log,
and an optional bearer token guards every mutating route. Nothing here claims
HIPAA compliance; it claims not to be careless.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import AGENTS, run_agent
from .analyze import analyze_source_version, impact_of_change
from .ask import ask
from .changes import find_version_pairs, record_changes
from .connectors.base import ConnectorError, registry
from .connectors.catalog import register_catalog
from .connectors.mock import register_mocks
from .credentials import backend as credential_backend, store_credential
from .model import ModelError, from_env
from .pipeline import IngestError, ingest_file
from .store import REVIEW_STATUSES, Store
from .sync import SyncEngine

log = logging.getLogger("veris")

DATA_DIR = Path(os.environ.get("VERIS_DATA_DIR", "data"))
DB_PATH = Path(os.environ.get("VERIS_DB", DATA_DIR / "veris.db"))
API_TOKEN = os.environ.get("VERIS_API_TOKEN", "")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

MAX_UPLOAD = 25 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

app = FastAPI(
    title="Veris",
    version="0.1.0",
    description="Healthcare Knowledge Operating System — grounded intelligence "
                "over an organization's own knowledge.",
)
store = Store(DB_PATH)
register_mocks()
register_catalog()
sync_engine = SyncEngine(store)


def require_token(authorization: str = Header(default="")) -> None:
    """Guards mutating routes. When no token is configured the API is open,
    which is correct for a local demo and must not be the deployed posture —
    the deployment documentation says so, and the health endpoint reports it."""
    if not API_TOKEN:
        return
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    # Detail goes to the operator; the caller gets nothing exploitable.
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500,
                        content={"detail": "Internal error. See server logs."})


# --- health and metadata -----------------------------------------------------

@app.get("/api/v1/health")
def health() -> dict:
    try:
        stats = store.stats()
        db_ok = True
    except Exception:
        stats, db_ok = {}, False
    model_name = "unconfigured"
    try:
        model_name = from_env().info.name
    except ModelError:
        pass
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "model_provider": os.environ.get("VERIS_MODEL_PROVIDER", "recorded"),
        "model": model_name,
        "auth": "token" if API_TOKEN else "open",
        "credential_store": credential_backend().name,
        "counts": stats,
    }


# --- documents ---------------------------------------------------------------

@app.get("/api/v1/documents")
def list_documents() -> list[dict]:
    return store.q("""
        SELECT d.*, s.publisher, s.authority, s.jurisdiction, s.source_type,
               s.version AS source_version, s.effective_date AS source_effective,
               s.reference_url,
               (SELECT COUNT(*) FROM entities e WHERE e.document_id = d.id) AS entity_count
        FROM documents d JOIN sources s ON s.id = d.source_id
        ORDER BY s.source_type DESC, d.title, s.version""")


@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: str) -> dict:
    doc = store.one("""
        SELECT d.*, s.publisher, s.authority, s.jurisdiction, s.source_type,
               s.version AS source_version, s.reference_url, s.retrieval_date
        FROM documents d JOIN sources s ON s.id = d.source_id WHERE d.id = ?""",
        (document_id,))
    if not doc:
        raise HTTPException(404, "Document not found")
    doc["entities"] = store.q(
        "SELECT * FROM entities WHERE document_id = ? ORDER BY rowid", (document_id,))
    return doc


@app.post("/api/v1/documents", dependencies=[Depends(require_token)])
async def upload_document(file: UploadFile = File(...)) -> dict:
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD // (1024*1024)} MB limit")
    if not raw:
        raise HTTPException(400, "File is empty")
    # The client-supplied filename is used only for its extension, and only
    # after being stripped of anything that could escape a directory.
    safe = SAFE_NAME.sub("_", Path(file.filename or "upload").name)[:120]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / safe
        path.write_bytes(raw)
        try:
            result = ingest_file(store, path, DATA_DIR)
        except IngestError as e:
            raise HTTPException(400, str(e))
    return {
        "document_id": result.document_id, "title": result.title,
        "document_type": result.document_type, "entities": result.entities,
        "rejected_spans": result.rejected, "already_present": result.reused,
    }


# --- knowledge graph ---------------------------------------------------------

@app.get("/api/v1/knowledge/{entity_id}")
def get_entity(entity_id: str) -> dict:
    entity = store.entity(entity_id)
    if not entity:
        raise HTTPException(404, "Knowledge entity not found")
    if entity.get("evidence_id"):
        entity["evidence"] = store.one("SELECT * FROM evidence WHERE id = ?",
                                       (entity["evidence_id"],))
    return entity


@app.get("/api/v1/knowledge/{entity_id}/relationships")
def get_relationships(entity_id: str) -> dict:
    if not store.entity(entity_id):
        raise HTTPException(404, "Knowledge entity not found")
    related = store.neighbourhood(entity_id)
    findings = store.q("""
        SELECT f.* FROM findings f
        JOIN finding_entities fe ON fe.finding_id = f.id
        WHERE fe.entity_id = ? ORDER BY
        CASE f.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END""",
        (entity_id,))
    return {"entity": store.entity(entity_id), "related": related, "findings": findings}


@app.get("/api/v1/knowledge")
def search_knowledge(q: str = Query("", max_length=300),
                     role: str = Query("", max_length=40),
                     limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    # Connection count is what makes the explorer navigable: the most connected
    # item is the most useful place to land, and an item with none is a leaf.
    sql = """SELECT e.id, e.locator, e.statement, e.role, e.entity_type,
                    d.title AS document_title, d.document_type, d.department,
                    (SELECT COUNT(*) FROM relationships r
                      WHERE r.from_entity_id = e.id OR r.to_entity_id = e.id)
                      AS relationship_count,
                    (SELECT COUNT(*) FROM finding_entities fx
                      WHERE fx.entity_id = e.id) AS finding_count
             FROM entities e JOIN documents d ON d.id = e.document_id WHERE 1=1"""
    args: list[Any] = []
    if q:
        sql += " AND (e.statement LIKE ? OR e.locator LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if role:
        sql += " AND e.role = ?"
        args.append(role)
    sql += " ORDER BY relationship_count DESC, finding_count DESC, d.title, e.rowid LIMIT ?"
    args.append(limit)
    return store.q(sql, tuple(args))


# --- changes and impact ------------------------------------------------------

@app.get("/api/v1/changes")
def list_changes() -> list[dict]:
    return store.q("""
        SELECT c.*, dt.title AS document_title,
               st.version AS to_version, sf.version AS from_version,
               st.effective_date AS effective_date, st.publisher,
               (SELECT COUNT(*) FROM findings f WHERE f.change_id = c.id) AS finding_count
        FROM changes c
        LEFT JOIN documents dt ON dt.id = c.to_document_id
        LEFT JOIN sources   st ON st.id = dt.source_id
        LEFT JOIN documents df ON df.id = c.from_document_id
        LEFT JOIN sources   sf ON sf.id = df.source_id
        ORDER BY CASE c.change_type WHEN 'ADDED' THEN 0 WHEN 'MODIFIED' THEN 1
                 ELSE 2 END, c.locator""")


@app.get("/api/v1/changes/{change_id}/impact")
def get_impact(change_id: str) -> dict:
    impact = impact_of_change(store, change_id)
    if not impact:
        raise HTTPException(404, "Change not found")
    return impact


class DetectRequest(BaseModel):
    from_document_id: str = Field(min_length=1, max_length=64)
    to_document_id: str = Field(min_length=1, max_length=64)


@app.post("/api/v1/changes/detect", dependencies=[Depends(require_token)])
def detect(req: DetectRequest) -> dict:
    for doc_id in (req.from_document_id, req.to_document_id):
        if not store.one("SELECT id FROM documents WHERE id = ?", (doc_id,)):
            raise HTTPException(404, "Document not found")
    ids = record_changes(store, req.from_document_id, req.to_document_id)
    return {"changes": len(ids), "change_ids": ids}


@app.get("/api/v1/versions")
def versions() -> list[dict]:
    return [{"from": a, "to": b} for a, b in find_version_pairs(store)]


# --- findings and review -----------------------------------------------------

@app.get("/api/v1/findings")
def list_findings(status: str = Query("", max_length=20),
                  severity: str = Query("", max_length=10)) -> list[dict]:
    sql = """SELECT f.*, e.locator AS subject_locator
             FROM findings f LEFT JOIN entities e ON e.id = f.subject_entity_id
             WHERE 1=1"""
    args: list[Any] = []
    if status:
        sql += " AND f.status = ?"
        args.append(status.upper())
    if severity:
        sql += " AND f.severity = ?"
        args.append(severity.upper())
    sql += (" ORDER BY CASE f.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1"
            " ELSE 2 END, f.created_at")
    return store.q(sql, tuple(args))


@app.get("/api/v1/findings/{finding_id}")
def get_finding(finding_id: str) -> dict:
    detail = store.finding_detail(finding_id)
    if not detail:
        raise HTTPException(404, "Finding not found")
    return detail


class ReviewRequest(BaseModel):
    action: str = Field(pattern="^(ACCEPT|REJECT|NEEDS_REVIEW|COMMENT|ASSIGN|RESOLVE|RETYPE)$")
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=4000)
    assigned_to: str | None = Field(default=None, max_length=120)
    due_date: str | None = Field(default=None, max_length=32)
    new_value: str | None = Field(default=None, max_length=64)


@app.post("/api/v1/findings/{finding_id}/reviews", dependencies=[Depends(require_token)])
def review_finding(finding_id: str, req: ReviewRequest) -> dict:
    if not store.one("SELECT id FROM findings WHERE id = ?", (finding_id,)):
        raise HTTPException(404, "Finding not found")
    store.add_review("FINDING", finding_id, req.action, req.reviewer,
                     comment=req.comment, assigned_to=req.assigned_to,
                     due_date=req.due_date, new_value=req.new_value)
    store.commit()
    return store.finding_detail(finding_id)


@app.post("/api/v1/relationships/{relationship_id}/reviews",
          dependencies=[Depends(require_token)])
def review_relationship(relationship_id: str, req: ReviewRequest) -> dict:
    if not store.one("SELECT id FROM relationships WHERE id = ?", (relationship_id,)):
        raise HTTPException(404, "Relationship not found")
    store.add_review("RELATIONSHIP", relationship_id, req.action, req.reviewer,
                     comment=req.comment, assigned_to=req.assigned_to,
                     due_date=req.due_date, new_value=req.new_value)
    store.commit()
    return store.one("SELECT * FROM relationships WHERE id = ?", (relationship_id,))


@app.get("/api/v1/reviews")
def list_reviews(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    return store.q("SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,))


# --- intelligence ------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.post("/api/v1/intelligence/query")
def intelligence_query(req: AskRequest) -> dict:
    try:
        model = from_env()
    except ModelError:
        model = None
    answer = ask(store, req.question, model)
    return {
        "question": answer.question, "summary": answer.summary,
        "sections": answer.sections, "findings": answer.findings,
        "scope": answer.scope, "flags": answer.flags,
    }


class AnalyzeRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=64)


@app.post("/api/v1/analysis", dependencies=[Depends(require_token)])
def run_analysis(req: AnalyzeRequest) -> dict:
    if not store.one("SELECT id FROM documents WHERE id = ?", (req.document_id,)):
        raise HTTPException(404, "Document not found")
    try:
        model = from_env()
    except ModelError as e:
        raise HTTPException(503, f"No model configured: {e}")
    return analyze_source_version(store, req.document_id, model)



# --- what the organization has connected --------------------------------------

# Veris holds no knowledge of its own. Every role below is filled by something
# the organization already had; an empty role is not a defect in Veris but a
# statement about what it has been given — and about what it therefore cannot
# yet say. Naming that second part is the honest half, and the useful one: it
# tells an organization what connecting more would actually buy them.
LIFECYCLE_VIEW = [
    ("REQUIRES", "What we are required to do",
     "Regulations, accreditation standards, government and professional requirements",
     "Without a connected requirement, Veris cannot tell you what any of your "
     "knowledge is accountable to."),
    ("COMMITS", "What we have decided to do",
     "Policies — the organization's own stated rules",
     "Without connected policy, Veris cannot tell you what the organization has "
     "committed to in response to a requirement."),
    ("OPERATIONALIZES", "How it is actually done",
     "Procedures, protocols, workflows, order sets",
     "Without connected procedures, Veris cannot tell you whether what is written "
     "matches what is practised."),
    ("TEACHES", "How people learn it",
     "Education modules, orientation, in-service training",
     "Without connected education, Veris cannot tell you whether staff are taught "
     "what the policy requires — or whether they are still being taught something "
     "a standard has since changed."),
    ("VALIDATES", "How competence is verified",
     "Competencies, skills validation, credentialing",
     "Without connected competencies, Veris cannot tell you whether anyone has "
     "confirmed that staff can actually do what is required."),
    ("MEASURES", "How we know it is happening",
     "Audit tools, quality metrics, tracers, dashboards",
     "Without connected measurement, Veris cannot tell you whether any of this "
     "is working in practice."),
]


@app.get("/api/v1/coverage")
def coverage() -> dict:
    """Connection coverage across the obligation lifecycle.

    The product thesis made measurable: the organization already owns every
    piece of this, and the value Veris adds is proportional to how much of it
    has been connected.
    """
    rows = store.q("""
        SELECT e.role,
               COUNT(*) AS entities,
               COUNT(DISTINCT e.document_id) AS documents
        FROM entities e GROUP BY e.role""")
    by_role = {r["role"]: r for r in rows}

    linked = store.q("""
        SELECT e.role, COUNT(DISTINCT e.id) AS connected
        FROM entities e
        JOIN relationships r ON r.from_entity_id = e.id OR r.to_entity_id = e.id
        GROUP BY e.role""")
    connected = {r["role"]: r["connected"] for r in linked}

    out = []
    for role, label, examples, absence in LIFECYCLE_VIEW:
        row = by_role.get(role, {})
        entities = row.get("entities", 0)
        out.append({
            "role": role,
            "label": label,
            "examples": examples,
            "documents": row.get("documents", 0),
            "entities": entities,
            "connected_entities": connected.get(role, 0),
            "present": entities > 0,
            "absence_note": "" if entities else absence,
        })

    total_docs = store.q("SELECT COUNT(*) n FROM documents")[0]["n"]
    return {
        "lifecycle": out,
        "documents": total_docs,
        "relationships": store.q("SELECT COUNT(*) n FROM relationships")[0]["n"],
        "roles_present": sum(1 for r in out if r["present"]),
        "roles_total": len(out),
        "owned_by": store.q(
            "SELECT publisher, COUNT(*) n FROM sources WHERE publisher IS NOT NULL"
            " GROUP BY publisher ORDER BY n DESC"),
    }



# --- connectors and connections ----------------------------------------------

@app.get("/api/v1/connectors")
def list_connectors() -> dict:
    """The registry. The Connection Center renders entirely from this, which is
    why adding an integration requires no dashboard change."""
    connected = {c["connector_id"]: c for c in store.q("SELECT * FROM connections")}
    by_category: dict[str, list[dict]] = {}
    for info in registry.all():
        row = info.as_dict()
        existing = connected.get(info.id)
        row["connection"] = {
            "id": existing["id"], "status": existing["status"],
            "last_sync_at": existing["last_sync_at"],
        } if existing else None
        by_category.setdefault(info.category, []).append(row)
    return {"categories": by_category,
            "credential_store": credential_backend().as_dict()
            if hasattr(credential_backend(), "as_dict") else
            credential_backend().__dict__}


@app.get("/api/v1/connections")
def list_connections() -> list[dict]:
    rows = store.q("""
        SELECT c.*, (SELECT COUNT(*) FROM sync_runs r WHERE r.connection_id = c.id)
                     AS run_count
        FROM connections c ORDER BY c.category, c.name""")
    for r in rows:
        info = registry.get(r["connector_id"])
        r["reads"] = list(info.reads) if info else []
        r["capabilities"] = list(info.capabilities) if info else []
    return rows


@app.get("/api/v1/connections/{connection_id}")
def get_connection(connection_id: str) -> dict:
    conn = store.one("SELECT * FROM connections WHERE id = ?", (connection_id,))
    if not conn:
        raise HTTPException(404, "Connection not found")
    info = registry.get(conn["connector_id"])
    conn["connector"] = info.as_dict() if info else None
    conn["runs"] = store.q(
        "SELECT * FROM sync_runs WHERE connection_id = ? ORDER BY started_at DESC"
        " LIMIT 20", (connection_id,))
    conn["records"] = {
        "people": store.q("SELECT COUNT(*) n FROM people WHERE connection_id=?",
                          (connection_id,))[0]["n"],
        "courses": store.q("SELECT COUNT(*) n FROM courses WHERE connection_id=?",
                           (connection_id,))[0]["n"],
        "completions": store.q(
            "SELECT COUNT(*) n FROM completions WHERE connection_id=?",
            (connection_id,))[0]["n"],
    }
    return conn


class ConnectRequest(BaseModel):
    connector_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    # Credentials are handed to the OS keychain and never persisted by Veris.
    credentials: dict[str, str] = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


@app.post("/api/v1/connections", dependencies=[Depends(require_token)])
def create_connection(req: ConnectRequest) -> dict:
    try:
        result = sync_engine.connect(req.connector_id, req.name,
                                     req.credentials, req.config)
    except ConnectorError as e:
        raise HTTPException(400, str(e))
    # Persist secrets to the OS store only; a failure here must not be fatal to
    # the connection, and must never fall back to writing them somewhere else.
    for field_name, value in req.credentials.items():
        try:
            store_credential(result["connection_id"], field_name, value)
        except Exception as e:
            result.setdefault("warnings", []).append(str(e))
    return result


@app.post("/api/v1/connections/{connection_id}/sync",
          dependencies=[Depends(require_token)])
def sync_connection(connection_id: str, kind: str = Query("FULL", max_length=16)) -> dict:
    if kind.upper() not in ("FULL", "INCREMENTAL", "DISCOVERY"):
        raise HTTPException(400, "kind must be FULL, INCREMENTAL or DISCOVERY")
    try:
        report = sync_engine.run(connection_id, kind.upper())
    except ConnectorError as e:
        raise HTTPException(404, str(e))
    return report.__dict__


@app.delete("/api/v1/connections/{connection_id}",
            dependencies=[Depends(require_token)])
def delete_connection(connection_id: str) -> dict:
    try:
        sync_engine.disconnect(connection_id)
    except ConnectorError as e:
        raise HTTPException(404, str(e))
    return {"status": "DISCONNECTED"}


# --- agents ------------------------------------------------------------------

@app.get("/api/v1/agents")
def list_agents() -> list[dict]:
    connected = {c["category"] for c in store.q(
        "SELECT DISTINCT category FROM connections WHERE status IN ('SYNCED','CONNECTED')")}
    out = []
    for info in AGENTS.values():
        missing = [r for r in info.requires if r not in connected]
        out.append({**info.__dict__,
                    "produces": list(info.produces),
                    "requires": list(info.requires),
                    "runnable": not missing,
                    "blocked_by": missing})
    return out


@app.post("/api/v1/agents/{agent_id}/run", dependencies=[Depends(require_token)])
def run_one_agent(agent_id: str) -> dict:
    if agent_id not in AGENTS:
        raise HTTPException(404, "Unknown agent")
    result = run_agent(store, agent_id)
    return result.__dict__


@app.get("/api/v1/overview")
def overview() -> dict:
    """Everything the workspace needs for a first paint, in one round trip."""
    return {
        "counts": store.stats(),
        "documents_by_type": store.q(
            "SELECT document_type, COUNT(*) n FROM documents GROUP BY 1 ORDER BY n DESC"),
        "findings_by_severity": store.q(
            "SELECT severity, COUNT(*) n FROM findings WHERE status != 'REJECTED'"
            " GROUP BY 1"),
        "findings_by_status": store.q(
            "SELECT status, COUNT(*) n FROM findings GROUP BY 1"),
        "relationships_by_type": store.q(
            "SELECT relationship_type, COUNT(*) n FROM relationships GROUP BY 1"
            " ORDER BY n DESC"),
        "open_findings": store.q(
            "SELECT COUNT(*) n FROM findings WHERE status = 'PROPOSED'")[0]["n"],
    }


# --- static web app ----------------------------------------------------------

if WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="app")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
