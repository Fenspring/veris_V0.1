"""Verify a connector against the system it claims to read.

    make verify-connector CONNECTOR=ecfr BY="Jamie Chen"

Run this from a machine that can actually reach the vendor. It calls the live
system, records exactly which checks passed and which endpoints answered, and
writes `docs/connectors/verification/<id>.json`. That file — not the connector's
own declaration — is what promotes it from `unverified` to `available`.

The separation is deliberate. A connector that could vouch for itself would
eventually be marked available because someone was confident, and "the code
compiles" would quietly become "the integration works". Here the claim always
traces to a run: a date, a person, an environment, and a list of what was
actually exercised.

A failed check does not fail the run — it is recorded. A partial verification
that says which four of nine checks passed is more useful to the next engineer
than a red cross, and more honest than a green tick.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .connectors.base import ConnectorError, Verification, registry
from .store import Store
from .sync import SyncEngine, _redact

VERIFICATION_DIR = Path(__file__).resolve().parents[1] / "docs" / "connectors" / "verification"


class Check:
    """One thing that was tried, and what happened."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.passed: bool | None = None
        self.detail = ""

    def record(self, passed: bool, detail: str = "") -> None:
        self.passed, self.detail = passed, _redact(detail)


def verify(connector_id: str, config: dict | None = None,
           by: str = "", notes: str = "") -> dict:
    info = registry.get(connector_id)
    if info is None:
        raise SystemExit(f"No connector registered with id {connector_id!r}")
    if info.availability == "planned":
        raise SystemExit(
            f"{info.name} is a declaration, not an implementation. There is "
            f"nothing to verify yet.")

    config = dict(config or {})
    if config.get("fixture_dir"):
        # The whole point of this harness is that it touches the real system.
        raise SystemExit(
            "Refusing to verify against fixtures. Fixtures prove the parser is "
            "self-consistent; they say nothing about what the vendor returns. "
            "Run this from a machine that can reach the source.")

    checks = [
        Check("reachable",
              "The source answers, and reports what it is current as of."),
        Check("authenticates",
              "Credentials are accepted, or the source states it needs none."),
        Check("discovery_reports_counts",
              "Discovery returns real counts before anything is synced."),
        Check("records_are_typed_and_identified",
              "Every record carries a type and the vendor's own identifier."),
        Check("declared_capabilities_are_delivered",
              "Everything the connector declared actually arrived."),
        Check("sync_is_resumable",
              "Sync resumes from a cursor instead of starting again."),
        Check("sync_is_idempotent",
              "Running twice produces the same rows, not duplicates."),
        Check("health_reports_a_known_state",
              "Health returns one of the standard states, with a latency."),
        Check("errors_carry_no_credentials",
              "A forced failure produces a message safe to show a customer."),
    ]
    by_name = {c.name: c for c in checks}
    endpoints: list[str] = []

    store = Store(Path(tempfile.mkdtemp()) / "verify.db")
    data_dir = Path(tempfile.mkdtemp())
    engine = SyncEngine(store, data_dir=data_dir)

    connector = registry.create(connector_id, config)

    # --- reachable and authenticated ---------------------------------------
    try:
        auth = connector.authenticate({})
        by_name["authenticates"].record(
            auth.ok, auth.message or "no message returned")
        status = connector.test_connection()
        by_name["reachable"].record(
            status.state == "CONNECTED", f"{status.state}: {status.message}")
    except Exception as e:
        by_name["reachable"].record(False, f"{type(e).__name__}: {e}")
        by_name["authenticates"].record(False, f"{type(e).__name__}: {e}")

    # --- discovery ----------------------------------------------------------
    try:
        discovery = connector.discover()
        by_name["discovery_reports_counts"].record(
            discovery.total > 0,
            f"{discovery.total} records across {len(discovery.counts)} kinds: "
            f"{discovery.counts}")
    except Exception as e:
        by_name["discovery_reports_counts"].record(False, f"{type(e).__name__}: {e}")

    # --- one real sync ------------------------------------------------------
    first_cursor = None
    try:
        outcome = engine.connect(connector_id, config=config)
        report = engine.run(outcome["connection_id"])
        by_name["records_are_typed_and_identified"].record(
            report.synced > 0 and report.failed == 0,
            f"{report.synced} synced, {report.failed} rejected. "
            f"{report.error}".strip())

        health = engine.health(outcome["connection_id"])
        first_cursor = store.one(
            "SELECT cursor FROM connections WHERE id = ?",
            (outcome["connection_id"],))["cursor"]
        by_name["health_reports_a_known_state"].record(
            bool(health.state) and health.latency_ms is not None,
            f"{health.state} · {health.latency_ms}ms · {health.message}")
        missing = list(health.degraded_capabilities)
        by_name["declared_capabilities_are_delivered"].record(
            not missing,
            "delivered " + ", ".join(health.capabilities)
            + (f"; declared but absent: {', '.join(missing)}" if missing else ""))

        before = store.stats()
        engine.run(outcome["connection_id"])
        after = store.stats()
        by_name["sync_is_idempotent"].record(
            before == after, f"{before} then {after}")
    except Exception as e:
        for name in ("records_are_typed_and_identified",
                     "declared_capabilities_are_delivered",
                     "sync_is_idempotent", "health_reports_a_known_state"):
            if by_name[name].passed is None:
                by_name[name].record(False, f"{type(e).__name__}: {e}")

    # --- resumability -------------------------------------------------------
    try:
        if first_cursor:
            fresh = registry.create(connector_id, config)
            fresh.authenticate({})
            pages = list(fresh.sync(cursor=first_cursor))
            resumed = sum(len(p.records) for p in pages)
            full = registry.create(connector_id, config)
            full.authenticate({})
            total = sum(len(p.records) for p in full.sync())
            by_name["sync_is_resumable"].record(
                resumed < total,
                f"{resumed} records after the checkpoint, {total} in full")
        else:
            by_name["sync_is_resumable"].record(
                False, "the connector recorded no cursor, so a sync cannot resume")
    except Exception as e:
        by_name["sync_is_resumable"].record(False, f"{type(e).__name__}: {e}")

    # --- error hygiene ------------------------------------------------------
    try:
        broken = registry.create(connector_id, dict(config, **_bad_config(config)))
        broken.authenticate({"api_key": "sk-verify-canary-0000"})
        try:
            broken.discover()
            by_name["errors_carry_no_credentials"].record(
                True, "no error raised to inspect")
        except Exception as e:
            message = _redact(str(e))
            leaked = "sk-verify-canary-0000" in str(e)
            by_name["errors_carry_no_credentials"].record(
                not leaked,
                "credential leaked into the error" if leaked else message[:200])
    except Exception as e:
        by_name["errors_carry_no_credentials"].record(
            "sk-verify-canary-0000" not in str(e), _redact(str(e))[:200])

    endpoints = sorted({e for e in getattr(connector, "endpoints_called", []) or []})

    passed = tuple(c.name for c in checks if c.passed)
    failed = tuple(c.name for c in checks if c.passed is False)
    record = Verification(
        status="verified" if not failed else "unverified",
        verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        verified_by=by or os.environ.get("USER") or "unknown",
        environment=f"{platform.system()} {platform.release()} · "
                    f"Python {platform.python_version()}",
        endpoints=tuple(endpoints),
        checks_passed=passed,
        checks_failed=failed,
        reason="" if not failed
               else f"{len(failed)} of {len(checks)} checks did not pass.",
        exercised_against="Live source." if not failed else
                          "Live source; verification incomplete.",
        notes=notes,
    )
    return {"record": record, "checks": checks, "connector": info}


def _bad_config(config: dict) -> dict:
    """A configuration that should fail, to see what the failure says."""
    if "part" in config or "title" in config:
        return {"part": "000000", "title": 999}
    return {"path": "/nonexistent/veris-verification-canary.csv"}


def write_record(connector_id: str, record: Verification) -> Path:
    VERIFICATION_DIR.mkdir(parents=True, exist_ok=True)
    path = VERIFICATION_DIR / f"{connector_id}.json"
    path.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-connector",
        description="Run a connector against the live system it reads and "
                    "record exactly what was verified.")
    parser.add_argument("connector")
    parser.add_argument("--by", default="", help="who ran it")
    parser.add_argument("--notes", default="")
    parser.add_argument("--config", action="append", default=[],
                        metavar="KEY=VALUE")
    parser.add_argument("--dry-run", action="store_true",
                        help="report without writing the verification record")
    args = parser.parse_args(argv)

    _register_all()
    config = {}
    for item in args.config:
        key, _, value = item.partition("=")
        config[key.strip()] = value.strip()

    result = verify(args.connector, config, by=args.by, notes=args.notes)
    record, checks, info = result["record"], result["checks"], result["connector"]

    print(f"\n{info.name} ({info.id}) — {record.environment}\n")
    for check in checks:
        mark = " ok " if check.passed else "FAIL" if check.passed is False else " -- "
        print(f"  [{mark}] {check.name}")
        print(f"         {check.description}")
        if check.detail:
            print(f"         → {check.detail}")
    print()

    if args.dry_run:
        print("Dry run — no verification record written.")
    elif record.status == "verified":
        path = write_record(args.connector, record)
        print(f"VERIFIED · {len(record.checks_passed)} checks passed")
        print(f"Recorded in {path}")
        print(f"{info.name} will now register as 'available'.")
    else:
        path = write_record(args.connector, record)
        print(f"NOT VERIFIED · {len(record.checks_failed)} of {len(checks)} "
              f"checks did not pass: {', '.join(record.checks_failed)}")
        print(f"Recorded in {path}. The connector stays 'unverified'.")
    return 0 if record.status == "verified" else 1


def _register_all() -> None:
    from .connectors.catalog import register_catalog
    from .connectors.ecfr import register_ecfr
    from .connectors.mock import register_mocks
    register_mocks()
    register_catalog()
    register_ecfr()


if __name__ == "__main__":
    sys.exit(main())
