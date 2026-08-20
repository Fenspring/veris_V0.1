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
    AUTH_METHODS, CAPABILITIES, CATEGORIES, HEALTH_STATES, Connector,
    ConnectorError, ConnectorHealth, RateLimited, SyncPage, TransientError,
    registry,
)
from veris.connectors.base import Verification  # noqa: E402
from veris.connectors.catalog import map_columns, register_catalog  # noqa: E402
from veris.connectors.ecfr import parse_part, register_ecfr  # noqa: E402
from veris.connectors.mock import register_mocks  # noqa: E402
from veris.store import EXTERNAL_IDENTITY_TABLES, Store  # noqa: E402
from veris.sync import SyncEngine, _redact  # noqa: E402

register_mocks()
register_catalog()
register_ecfr()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# file import needs a file; eCFR needs a network, and is exercised separately
# against fixtures below rather than being quietly skipped.
ALL = [i.id for i in registry.available() if i.id not in ("file_import", "ecfr")]


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
                             auth_methods=("none",), reads=("Courses",),
                             capabilities=("course_catalog",), is_mock=True,
                             verification=Verification(status="mock"))
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
                             auth_methods=("none",), reads=("Courses",),
                             capabilities=("course_catalog",), is_mock=True,
                             verification=Verification(status="mock"))
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
                             auth_methods=("none",), reads=("Courses",),
                             capabilities=("course_catalog",), is_mock=True,
                             verification=Verification(status="mock"))
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


# --- capabilities, health, and external identity ----------------------------

def test_capabilities_are_declared_from_the_shared_vocabulary():
    """A connector may not invent a capability.

    The intelligence layer decides what it can assess by reading these; a
    free-text claim would be a promise nothing downstream can act on.
    """
    for info in registry.all():
        assert info.capabilities, f"{info.id} declares no capabilities"
        for capability in info.capabilities:
            assert capability in CAPABILITIES, f"{info.id}: {capability}"


def test_every_capability_states_what_its_absence_costs():
    """The vocabulary is what the product says when something is not connected,
    so an entry with no `without_it` would leave a silent hole in the UI."""
    for capability in CAPABILITIES.values():
        assert capability.kind in ("knowledge", "operational"), capability.id
        assert capability.enables.strip(), capability.id
        assert capability.without_it.strip(), capability.id
        assert capability.without_it.strip().endswith("."), capability.id


def test_health_is_one_shape_for_every_connector():
    """Whatever the vendor, the dashboard renders the same record."""
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    for cid in ALL:
        outcome = engine.connect(cid)
        engine.run(outcome["connection_id"])
        health = engine.health(outcome["connection_id"])
        assert isinstance(health, ConnectorHealth)
        assert health.state in HEALTH_STATES, f"{cid}: {health.state}"
        assert health.connector_id == cid
        assert health.last_run and health.last_run["status"] in (
            "SUCCEEDED", "PARTIAL", "FAILED")
        assert health.consecutive_failures == 0
        rendered = health.as_dict()
        assert isinstance(rendered["capabilities"], list)
        for entry in rendered["capabilities"]:
            assert {"id", "label", "kind", "enables", "without_it"} <= set(entry)


def test_health_reports_a_declared_capability_that_delivered_nothing():
    """Reachable is not the same as working.

    The demo policy system declares policy metadata and delivers it; a file
    import that declares four capabilities and carries one export must report
    the other three as degraded rather than as connected.
    """
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    csv_text = "course_id,title,last_revised\nC-1,Hand Hygiene,2025-01-01\n"
    outcome = engine.connect("file_import", config={"content": csv_text,
                                                    "record_type": "course"})
    engine.run(outcome["connection_id"])
    health = engine.health(outcome["connection_id"])
    assert "course_catalog" in health.capabilities
    assert "completion_records" in health.degraded_capabilities
    assert "person_roster" in health.degraded_capabilities


def test_normalized_rows_preserve_external_identity():
    """Every row from a connected system records where it came from (§7)."""
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    outcome = engine.connect("mock_lms", config={"scope_limit": 5})
    engine.run(outcome["connection_id"])
    for table in ("people", "courses", "completions"):
        rows = store.q(f"SELECT * FROM {table}")
        assert rows, f"{table} is empty"
        for row in rows:
            assert row["source_system"] == "mock_lms", table
            assert row["source_record_type"], table
            assert row["source_id"], table
            assert row["imported_at"], table


def test_the_vendor_id_is_never_the_veris_id():
    """Veris mints its own identity. The vendor's key is data, not the key."""
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    outcome = engine.connect("mock_lms", config={"scope_limit": 5})
    engine.run(outcome["connection_id"])
    for table, prefix in (("people", "per_"), ("courses", "crs_")):
        for row in store.q(f"SELECT * FROM {table}"):
            assert row["id"] != row["source_id"], table
            assert row["id"].startswith(prefix), table
            assert row["source_id"] not in row["id"], (
                f"{table}: the vendor id leaked into the Veris id")


def test_a_record_with_no_source_identifier_is_rejected_not_guessed():
    """Without a stable key the next sync would duplicate or overwrite it."""
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    outcome = engine.connect("mock_lms", config={"scope_limit": 2})
    try:
        engine._apply(outcome["connection_id"],
                      {"_type": "course", "title": "Nameless"}, "mock_lms")
        raise AssertionError("a record with no source id was accepted")
    except ValueError:
        pass


def test_origin_of_any_row_is_answerable():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None)
    outcome = engine.connect("mock_lms", config={"scope_limit": 3})
    engine.run(outcome["connection_id"])
    course = store.q("SELECT id FROM courses LIMIT 1")[0]
    origin = store.record_origin("courses", course["id"])
    assert origin["veris_id"] == course["id"]
    assert origin["source_system"] == "mock_lms"
    assert "mock_lms" in origin["origin"]
    for table in EXTERNAL_IDENTITY_TABLES:
        store.record_origin(table, "does-not-exist")   # must not raise


def test_schema_migrations_are_recorded():
    store = _store()
    from veris.store import SCHEMA_VERSION
    assert store.schema_version() == SCHEMA_VERSION



# --- verification: an integration is not proven because it compiles ---------

def test_an_unverified_connector_never_reads_as_proven():
    info = registry.get("ecfr")
    assert info.availability == "unverified"
    assert info.verification.status == "unverified"
    assert info.verification.reason, "an unverified connector must say why"
    assert "never" in info.verification.summary.lower()


def test_an_unverified_connector_can_still_be_created():
    """A connector nobody can run is a connector nobody can verify."""
    connector = registry.create("ecfr", {"fixture_dir": str(FIXTURES / "ecfr")})
    assert isinstance(connector, Connector)


def test_a_connector_cannot_declare_itself_available():
    """Promotion comes from a recorded run, never from the declaration."""
    from veris.connectors.base import ConnectorInfo, ConnectorRegistry
    bare = ConnectorRegistry()
    info = ConnectorInfo(id="wishful", name="Wishful", category="LMS",
                         capabilities=("course_catalog",), reads=("Courses",),
                         availability="available")
    try:
        bare.register(info, lambda config: None)
        raise AssertionError("a connector promoted itself to available")
    except ValueError as e:
        assert "verification" in str(e)


def test_a_recorded_verification_promotes_the_connector():
    import json as _json
    import os as _os
    from veris.connectors.base import ConnectorInfo, ConnectorRegistry
    directory = Path(tempfile.mkdtemp())
    (directory / "probe.json").write_text(_json.dumps({
        "status": "verified", "verified_at": "2026-08-20T00:00:00+00:00",
        "verified_by": "a person", "environment": "a laptop",
        "checks_passed": ["reachable", "sync_is_idempotent"]}))
    previous = _os.environ.get("VERIS_VERIFICATION_DIR")
    _os.environ["VERIS_VERIFICATION_DIR"] = str(directory)
    try:
        bare = ConnectorRegistry()
        bare.register(ConnectorInfo(
            id="probe", name="Probe", category="LMS",
            capabilities=("course_catalog",), reads=("Courses",),
            availability="unverified"), lambda config: None)
        promoted = bare.get("probe")
        assert promoted.availability == "available"
        assert promoted.verification.verified_by == "a person"
        assert "2 checks passed" in promoted.verification.summary
    finally:
        if previous is None:
            _os.environ.pop("VERIS_VERIFICATION_DIR", None)
        else:
            _os.environ["VERIS_VERIFICATION_DIR"] = previous


def test_the_verification_harness_refuses_to_verify_against_fixtures():
    from veris.verify_connector import verify
    try:
        verify("ecfr", {"fixture_dir": str(FIXTURES / "ecfr")})
        raise AssertionError("the harness verified against fixtures")
    except SystemExit as e:
        assert "fixtures" in str(e).lower()


# --- eCFR parsing (fixtures, not captured traffic) --------------------------

def test_ecfr_parses_sections_and_paragraph_hierarchy():
    sections = parse_part((FIXTURES / "ecfr" / "full.xml").read_bytes())
    assert [s.number for s in sections] == ["482.13", "482.23"]
    designators = [p.designator for p in sections[0].paragraphs]
    assert "(a)(1)" in designators
    assert "(e)(1)(i)" in designators, "roman nesting under a numbered paragraph"
    assert "(e)(1)(ii)" in designators


def test_ecfr_tolerates_skipped_paragraph_designators():
    """CFR parts reserve and remove designators. `(e)` arriving after `(b)(2)`
    is `(e)`, not a child of `(b)`."""
    sections = parse_part((FIXTURES / "ecfr" / "full.xml").read_bytes())
    designators = [p.designator for p in sections[0].paragraphs]
    assert "(e)" in designators
    assert "(b)(e)" not in designators


def test_ecfr_sections_become_citable_entities():
    """The point of a source that supplies text: its requirements can be quoted.

    The section goes through the same pipeline an uploaded PDF uses, so the
    spans are byte-verified the same way.
    """
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None,
                        data_dir=Path(tempfile.mkdtemp()))
    outcome = engine.connect("ecfr", config={"fixture_dir": str(FIXTURES / "ecfr")})
    report = engine.run(outcome["connection_id"])
    assert report.status == "SUCCEEDED" and report.failed == 0

    locators = [e["locator"] for e in store.q("SELECT locator FROM entities")]
    assert any(l.startswith("42 CFR §482.13(a)(1)") for l in locators), locators[:3]

    # Every entity's statement must be exactly the text at its cited span.
    for row in store.q("""SELECT e.statement, ev.quote, ev.char_start, ev.char_end,
                                 d.canonical_path
                          FROM entities e JOIN evidence ev ON ev.id = e.evidence_id
                          JOIN documents d ON d.id = e.document_id"""):
        text = Path(row["canonical_path"]).read_text(encoding="utf-8")
        assert text[row["char_start"]:row["char_end"]] == row["statement"]


def test_ecfr_records_the_amendment_date_the_source_reported():
    store = _store()
    engine = SyncEngine(store, sleep=lambda s: None,
                        data_dir=Path(tempfile.mkdtemp()))
    outcome = engine.connect("ecfr", config={"fixture_dir": str(FIXTURES / "ecfr")})
    engine.run(outcome["connection_id"])
    doc = store.one("SELECT * FROM documents WHERE source_ref = '482.13'")
    assert doc["source_system"] == "ecfr"
    assert doc["source_updated_at"] == "2026-04-01"
    assert doc["effective_date"] == "2026-04-01"


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
