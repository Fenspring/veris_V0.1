"""Connector contract tests.

Every connector — mock or real — must pass this suite. A new integration is
therefore proven against the same bar as the ones already shipping, rather than
against whatever its author remembered to check.

Run: .venv/bin/python tests/test_connectors.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from veris.connectors.base import (  # noqa: E402
    AUTH_METHODS, CATEGORIES, Connector, ConnectorError, RateLimited,
    SyncPage, TransientError, registry,
)
from veris.connectors.catalog import map_columns, register_catalog  # noqa: E402
from veris.connectors.mock import register_mocks  # noqa: E402
from veris.store import Store  # noqa: E402
from veris.sync import SyncEngine, _redact  # noqa: E402

register_mocks()
register_catalog()
ALL = [i.id for i in registry.available()
       if i.id not in ("file_import",)]  # file import needs a file


def _store() -> Store:
    return Store(Path(tempfile.mkdtemp()) / "t.db")


# --- contract every connector must satisfy ----------------------------------

def test_every_connector_implements_the_protocol():
    for cid in ALL:
        c = registry.create(cid)
        assert isinstance(c, Connector), f"{cid} does not satisfy Connector"


def test_metadata_is_valid_and_renderable():
    for info in registry.all():
        assert info.category in CATEGORIES, info.id
        assert info.auth_methods, f"{info.id} declares no auth method"
        for m in info.auth_methods:
            assert m in AUTH_METHODS, f"{info.id}: {m}"
        assert info.reads, f"{info.id} must state what it reads, for the consent screen"
        assert info.name and info.id


def test_connectors_are_read_only():
    """The interface has no write method, and no connector may claim one."""
    for info in registry.all():
        assert info.writes == (), f"{info.id} declares writes"
    for cid in ALL:
        c = registry.create(cid)
        for forbidden in ("write", "push", "update_remote", "create_remote", "delete"):
            assert not hasattr(c, forbidden), f"{cid} exposes {forbidden}()"


def test_authenticate_then_test_connection():
    for cid in ALL:
        c = registry.create(cid)
        assert c.test_connection().state == "AUTHENTICATION_REQUIRED"
        assert c.authenticate({}).ok
        assert c.test_connection().state == "CONNECTED"


def test_discovery_reports_counts_before_any_sync():
    for cid in ALL:
        c = registry.create(cid)
        c.authenticate({})
        d = c.discover()
        assert d.counts, f"{cid} discovered nothing"
        assert d.total > 0
        assert all(isinstance(v, int) and v >= 0 for v in d.counts.values())


def test_sync_yields_pages_with_typed_records():
    for cid in ALL:
        c = registry.create(cid)
        c.authenticate({})
        pages = list(c.sync())
        assert pages, f"{cid} yielded no pages"
        assert all(isinstance(p, SyncPage) for p in pages)
        for p in pages:
            for r in p.records:
                assert r.get("_type"), f"{cid} emitted an untyped record"
                assert r.get("external_id"), f"{cid} emitted a record with no external id"
        assert pages[-1].has_more is False, f"{cid} never terminates"


def test_health_and_disconnect():
    for cid in ALL:
        c = registry.create(cid)
        c.authenticate({})
        assert c.health_check().state in ("CONNECTED", "SYNCED", "WARNING")
        c.disconnect()
        assert c.health_check().state == "DISCONNECTED"


def test_registry_rejects_unknown_category_and_writes():
    from veris.connectors.base import ConnectorInfo
    for bad in (ConnectorInfo(id="x", name="X", category="NOPE"),
                ConnectorInfo(id="y", name="Y", category="LMS", writes=("courses",))):
        try:
            registry.register(bad, lambda c: None)
            raise AssertionError(f"registry accepted {bad.id}")
        except ValueError:
            pass


# --- sync engine behaviour ---------------------------------------------------

def test_sync_normalizes_and_is_idempotent():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    r = engine.connect("mock_lms")
    first = engine.run(r["connection_id"])
    before = store.stats()
    second = engine.run(r["connection_id"])
    after = store.stats()
    assert first.status == "SUCCEEDED"
    assert second.status == "SUCCEEDED"
    # Deterministic ids mean a re-sync updates rather than duplicating.
    assert before["courses"] == after["courses"], "re-sync duplicated courses"
    assert before["people"] == after["people"], "re-sync duplicated people"


def test_sync_checkpoints_after_each_page():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    r = engine.connect("mock_lms")
    engine.run(r["connection_id"])
    conn = store.one("SELECT cursor FROM connections WHERE id=?", (r["connection_id"],))
    assert conn["cursor"], "no checkpoint recorded; an interrupted sync could not resume"


def test_sync_run_is_audited():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    r = engine.connect("mock_policy")
    rep = engine.run(r["connection_id"])
    runs = store.q("SELECT * FROM sync_runs WHERE connection_id=?", (r["connection_id"],))
    assert len(runs) == 1
    assert runs[0]["status"] == "SUCCEEDED"
    assert runs[0]["finished_at"]
    assert runs[0]["synced"] == rep.synced


def test_transient_failure_is_retried_then_succeeds():
    """A dropped connection mid-sync must not fail the run."""
    from veris.connectors.base import ConnectorInfo
    state = {"failures": 0}

    class Flaky:
        info = ConnectorInfo(id="flaky", name="Flaky", category="LMS",
                             auth_methods=("none",), reads=("Courses",), is_mock=True)
        def __init__(self, config): pass
        def authenticate(self, c): from veris.connectors.base import AuthResult; return AuthResult(True)
        def test_connection(self):
            from veris.connectors.base import ConnectionStatus; return ConnectionStatus("CONNECTED")
        def discover(self):
            from veris.connectors.base import DiscoveryResult
            return DiscoveryResult(counts={"courses": 1})
        def sync(self, since=None, cursor=None):
            if state["failures"] < 2:
                state["failures"] += 1
                raise TransientError("connection reset")
            yield SyncPage([{"_type": "course", "external_id": "C1", "title": "T"}],
                           cursor="done", has_more=False)
        def health_check(self):
            from veris.connectors.base import HealthStatus; return HealthStatus("CONNECTED")
        def disconnect(self): pass

    if not registry.get("flaky"):
        registry.register(Flaky.info, Flaky)
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    r = engine.connect("flaky")
    rep = engine.run(r["connection_id"])
    assert rep.status == "SUCCEEDED", f"transient failure not retried: {rep.error}"
    assert state["failures"] == 2


def test_one_bad_record_does_not_abandon_the_sync():
    from veris.connectors.base import ConnectorInfo, AuthResult, ConnectionStatus, DiscoveryResult, HealthStatus

    class Mixed:
        info = ConnectorInfo(id="mixed", name="Mixed", category="LMS",
                             auth_methods=("none",), reads=("Courses",), is_mock=True)
        def __init__(self, config): pass
        def authenticate(self, c): return AuthResult(True)
        def test_connection(self): return ConnectionStatus("CONNECTED")
        def discover(self): return DiscoveryResult(counts={"courses": 2})
        def sync(self, since=None, cursor=None):
            yield SyncPage([
                {"_type": "course", "external_id": "OK1", "title": "Fine"},
                {"_type": "nonsense", "external_id": "BAD"},
                {"_type": "course", "external_id": "OK2", "title": "Also fine"},
            ], cursor="done", has_more=False)
        def health_check(self): return HealthStatus("CONNECTED")
        def disconnect(self): pass

    if not registry.get("mixed"):
        registry.register(Mixed.info, Mixed)
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    r = engine.connect("mixed")
    rep = engine.run(r["connection_id"])
    assert rep.synced == 2 and rep.failed == 1, (rep.synced, rep.failed)
    assert rep.status == "PARTIAL"


def test_credentials_are_never_written_to_the_database():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    secret = "sk-live-DO-NOT-PERSIST-123456"
    engine.connect("mock_lms", credentials={"api_key": secret},
                   config={"scope_limit": 5})
    engine.run(store.q("SELECT id FROM connections")[0]["id"])
    dump = "".join(str(r) for t in ("connections", "sync_runs", "events")
                   for r in store.q(f"SELECT * FROM {t}"))
    assert secret not in dump, "a credential reached the database"


def test_error_messages_are_redacted():
    for raw, must_not_contain in [
        ("Request failed: https://api.example.com?api_key=sk-live-abc123", "sk-live-abc123"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ("token=supersecrettoken", "supersecrettoken"),
    ]:
        out = _redact(raw)
        assert must_not_contain not in out, out
        assert "redacted" in out.lower(), out


def test_rate_limit_backs_off_rather_than_hammering():
    from veris.connectors.base import ConnectorInfo, AuthResult, ConnectionStatus, DiscoveryResult, HealthStatus
    waits, state = [], {"n": 0}

    class Limited:
        info = ConnectorInfo(id="limited", name="Limited", category="LMS",
                             auth_methods=("none",), reads=("Courses",), is_mock=True)
        def __init__(self, config): pass
        def authenticate(self, c): return AuthResult(True)
        def test_connection(self): return ConnectionStatus("CONNECTED")
        def discover(self): return DiscoveryResult(counts={"courses": 1})
        def sync(self, since=None, cursor=None):
            if state["n"] == 0:
                state["n"] += 1
                raise RateLimited("slow down", retry_after=7.0)
            yield SyncPage([{"_type": "course", "external_id": "C1", "title": "T"}],
                           cursor="done", has_more=False)
        def health_check(self): return HealthStatus("CONNECTED")
        def disconnect(self): pass

    if not registry.get("limited"):
        registry.register(Limited.info, Limited)
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: waits.append(s))
    r = engine.connect("limited")
    rep = engine.run(r["connection_id"])
    assert rep.status == "SUCCEEDED"
    assert waits and waits[0] == 7.0, f"vendor retry hint ignored: {waits}"


def test_planned_connectors_are_declared_but_refuse_to_connect():
    """Showing a vendor next to a Connect button that silently does nothing is
    worse than not listing it. Never fake an integration."""
    planned = [i for i in registry.all() if i.availability == "planned"]
    assert planned, "the catalogue should declare what is coming"
    for info in planned:
        assert info.setup_note, f"{info.id} gives the customer no explanation"
        try:
            registry.create(info.id)
            raise AssertionError(f"{info.id} allowed a connection while planned")
        except ConnectorError:
            pass


def test_file_import_maps_columns_and_asks_about_the_rest():
    mapping, unmapped = map_columns(
        ["Employee ID", "Course ID", "Completion Status", "Date Completed",
         "Cost Center", "Widget Score"])
    assert mapping["person_external_id"] == "Employee ID"
    assert mapping["status"] == "Completion Status"
    assert mapping["department"] == "Cost Center"
    assert unmapped == ["Widget Score"], unmapped


def test_completion_rows_do_not_collide_without_an_id_column():
    csv_text = ("Employee ID,Course ID,Completion Status\n"
                "E1,C1,COMPLETED\nE2,C1,COMPLETED\nE1,C2,OVERDUE\n")
    c = registry.create("file_import",
                        {"content": csv_text, "record_type": "completion"})
    c.authenticate({})
    records = list(c.sync())[0].records
    ids = [r["external_id"] for r in records]
    assert len(set(ids)) == 3, f"rows collapsed onto each other: {ids}"


def test_references_tolerate_arrival_order():
    """A completions export may arrive before the roster, or the people may be
    in a different system. Dropping the rows would lose data over timing."""
    import json as _json
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    comp = "Employee ID,Course ID,Completion Status\nE1,C1,COMPLETED\n"
    r = engine.connect("file_import",
                       config={"content": comp, "record_type": "completion"})
    engine.run(r["connection_id"])
    assert store.stats()["completions"] == 1, "completion discarded for arriving first"
    assert store.q("SELECT COUNT(*) n FROM completions WHERE person_id IS NULL")[0]["n"] == 1

    roster = "id,name,role\nE1,Alice,Registered Nurse\n"
    store.db.execute("UPDATE connections SET config=? WHERE id=?",
                     (_json.dumps({"content": roster, "record_type": "person"}),
                      r["connection_id"]))
    store.commit()
    engine.run(r["connection_id"])
    unresolved = store.q(
        "SELECT COUNT(*) n FROM completions WHERE person_id IS NULL")[0]["n"]
    assert unresolved == 0, "reference did not resolve once the roster arrived"


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
