"""The connector catalogue.

Two kinds of entry, and the difference is visible to the customer:

**Available** — implemented and usable now. The file-import connector below is
the important one: it is what lets Veris answer "let's connect it another way"
instead of "we don't support your vendor". Most healthcare systems can produce a
scheduled CSV export even when their API is locked behind an account manager.

**Planned** — declared so the Connection Center can show what is coming and what
it will require, and refusing to connect if anyone tries. Showing a vendor's name
next to a Connect button that silently does nothing would be worse than omitting
it. Never fake an integration.

Ordering follows an integration score — market reach, API quality, documentation,
authentication simplicity, data accessibility, change detection, webhooks, minus
engineering complexity — rather than brand recognition.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable

from .base import (
    AuthResult, ConnectionStatus, ConnectorError, ConnectorInfo,
    DiscoveryResult, HealthStatus, SyncPage, Verification, registry,
)

# --------------------------------------------------------------------------
# Available now: universal file import
# --------------------------------------------------------------------------

# Column aliases seen across real LMS and policy exports. Zero-config mapping
# handles the common shapes; anything unrecognised is reported rather than
# guessed at, because a wrong automatic mapping is worse than a question.
ALIASES = {
    "external_id": ("id", "external_id", "course_id", "employee_id", "policy_id",
                    "identifier", "number", "code"),
    "title": ("title", "name", "course_name", "policy_title", "course_title"),
    "person_external_id": ("employee_id", "user_id", "person_id", "staff_id"),
    "course_external_id": ("course_id", "training_id", "curriculum_id"),
    "job_role": ("role", "job_role", "job_title", "position"),
    "department": ("department", "dept", "cost_center", "unit"),
    "facility": ("facility", "site", "location", "campus"),
    "status": ("status", "completion_status", "state"),
    "completed_at": ("completed_at", "completion_date", "date_completed"),
    "due_at": ("due_at", "due_date", "assigned_due_date"),
    "owner": ("owner", "policy_owner", "responsible_party"),
    "version": ("version", "revision"),
    "effective_date": ("effective_date", "effective", "approved_date"),
    "next_review_date": ("next_review_date", "review_date", "next_review"),
    "content_updated_at": ("content_updated_at", "last_revised", "last_updated",
                           "modified_date"),
    "category": ("category", "type", "subject"),
}


def map_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map source columns onto the normalized schema.

    Returns the confident mapping and the columns it could not place. The
    unmapped list is shown to the customer as a short question rather than a
    forty-field mapping grid.
    """
    lowered = {h.strip().lower().replace(" ", "_"): h for h in headers}
    mapping: dict[str, str] = {}
    for field, options in ALIASES.items():
        for option in options:
            if option in lowered and lowered[option] not in mapping.values():
                mapping[field] = lowered[option]
                break
    unmapped = [h for h in headers
                if h.strip().lower().replace(" ", "_") not in
                {v.strip().lower().replace(" ", "_") for v in mapping.values()}]
    return mapping, unmapped


class FileImportConnector:
    """Import an export. CSV today; the same shape takes SFTP or a scheduled
    drop without changing anything above it."""

    info = ConnectorInfo(
        id="file_import",
        name="File import (CSV)",
        category="DOCUMENT",
        vendor="Any",
        auth_methods=("file_import",),
        # What file import *can* carry. One connection carries one record
        # type; its health record reports what actually arrived, which is how
        # a declared capability and a delivered one stay distinguishable.
        capabilities=("course_catalog", "person_roster", "completion_records",
                      "policy_metadata"),
        reads=("Whatever columns your export contains",),
        setup_note="Export from any system. Veris maps the columns for you and "
                   "asks about anything it is unsure of.",
        # The only connector whose external system is a file on disk. There is
        # no vendor to reach, so what needs proving is that Veris reads a real
        # export correctly — and that is proven on every commit rather than once.
        verification=Verification(
            status="verified",
            verified_by="tests/test_connectors.py",
            environment="Local file. No external system to reach.",
            checks_passed=("column mapping across LMS and policy export shapes",
                           "unmappable columns reported rather than guessed",
                           "composite keys for completion exports with no id",
                           "idempotent re-import",
                           "read-only, no write path"),
            notes="Verified continuously by the contract suite, not by a "
                  "one-off run against a vendor.",
        ),
    )

    def __init__(self, config: dict):
        self.config = config or {}
        self.path = self.config.get("path")
        self.record_type = self.config.get("record_type", "course")
        self._rows: list[dict] = []
        self._headers: list[str] = []
        self._authenticated = False

    def _load(self) -> None:
        if self._rows:
            return
        text = self.config.get("content")
        if text is None:
            if not self.path or not Path(self.path).is_file():
                raise ConnectorError("No file supplied for import")
            text = Path(self.path).read_text(encoding="utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        self._headers = list(reader.fieldnames or [])
        self._rows = [r for r in reader]

    def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        self._authenticated = True
        return AuthResult(ok=True, message="File accepted")

    def test_connection(self) -> ConnectionStatus:
        try:
            self._load()
        except ConnectorError as e:
            return ConnectionStatus("ERROR", str(e))
        return ConnectionStatus("CONNECTED", f"{len(self._rows)} rows readable")

    def discover(self) -> DiscoveryResult:
        self._load()
        mapping, unmapped = map_columns(self._headers)
        notes = [f"{len(self._rows)} rows discovered",
                 f"{len(mapping)} columns mapped automatically"]
        if unmapped:
            notes.append(f"{len(unmapped)} columns need your help: "
                         + ", ".join(unmapped[:5]))
        return DiscoveryResult(
            counts={self.record_type: len(self._rows)},
            fields={self.record_type: self._headers},
            samples={self.record_type: self._rows[:3]},
            notes=notes)

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]:
        self._load()
        mapping, _ = map_columns(self._headers)
        if "external_id" not in mapping:
            raise ConnectorError(
                "This export has no column Veris can use as a unique id. "
                "Add an id column and import again.")
        records = []
        for row in self._rows:
            record = {"_type": self.record_type}
            for field, column in mapping.items():
                value = (row.get(column) or "").strip()
                if value:
                    record[field] = value

            # A completion export rarely carries its own id — the row is
            # identified by *who* completed *what*. Without this, every row
            # would take the course id as its key and overwrite the previous
            # one, silently collapsing thousands of records into a handful.
            if self.record_type == "completion":
                person = record.get("person_external_id")
                course = record.get("course_external_id") or record.get("external_id")
                if course and record.get("external_id") == course:
                    record["course_external_id"] = course
                if person and course:
                    record["external_id"] = f"{person}:{course}"
            records.append(record)
        yield SyncPage(records, cursor="complete", has_more=False)

    def health_check(self) -> HealthStatus:
        return HealthStatus("CONNECTED" if self._authenticated else "DISCONNECTED",
                            f"{len(self._rows)} rows")

    def disconnect(self) -> None:
        self._authenticated = False
        self._rows = []


# --------------------------------------------------------------------------
# Planned: declared, visible, and honestly not connectable yet
# --------------------------------------------------------------------------

def _planned(cid, name, category, vendor, auth, caps, reads,
             enablement=False, note="") -> ConnectorInfo:
    return ConnectorInfo(
        id=cid, name=name, category=category, vendor=vendor,
        auth_methods=auth, capabilities=caps, reads=reads,
        availability="planned", requires_vendor_enablement=enablement,
        setup_note=note or ("Your vendor must enable API access before this can "
                            "be connected." if enablement else
                            "Not yet available. Use file import in the meantime."))


PLANNED = [
    _planned("healthstream", "HealthStream", "LMS", "HealthStream",
             ("oauth", "api_key"), ("course_catalog", "person_roster", "completion_records"),
             ("Course catalogue", "Staff roster", "Assignments", "Completions"),
             enablement=True),
    _planned("relias", "Relias", "LMS", "Relias",
             ("oauth", "api_key"), ("course_catalog", "person_roster", "completion_records"),
             ("Course catalogue", "Staff roster", "Completions"), enablement=True),
    _planned("cornerstone", "Cornerstone", "LMS", "Cornerstone OnDemand",
             ("oauth",), ("course_catalog", "person_roster", "completion_records"),
             ("Learning objects", "Users", "Transcripts"), enablement=True),
    _planned("workday_learning", "Workday Learning", "LMS", "Workday",
             ("oauth",), ("course_catalog", "person_roster", "completion_records"),
             ("Learning content", "Workers", "Completions"), enablement=True),
    _planned("moodle", "Moodle", "LMS", "Moodle",
             ("api_key",), ("course_catalog", "person_roster", "completion_records"),
             ("Courses", "Users", "Course completions")),
    _planned("docebo", "Docebo", "LMS", "Docebo",
             ("oauth",), ("course_catalog", "person_roster", "completion_records"),
             ("Courses", "Users", "Enrollments")),
    _planned("successfactors", "SAP SuccessFactors", "LMS", "SAP",
             ("oauth",), ("course_catalog", "person_roster", "completion_records"),
             ("Learning items", "Users", "Completions"), enablement=True),

    _planned("policystat", "PolicyStat", "POLICY", "RLDatix",
             ("api_key",), ("policy_metadata", "policy_text", "acknowledgments"),
             ("Policy documents", "Owners and departments", "Version history",
              "Acknowledgements"), enablement=True),
    _planned("powerdms", "PowerDMS", "POLICY", "NEOGOV",
             ("oauth", "api_key"), ("policy_metadata", "policy_text", "acknowledgments"),
             ("Documents", "Workflow status", "Signatures"), enablement=True),
    _planned("policytech", "PolicyTech", "POLICY", "NAVEX",
             ("api_key",), ("policy_metadata", "policy_text"),
             ("Policies", "Approval status", "Owners"), enablement=True),
    _planned("sharepoint", "SharePoint / document library", "POLICY", "Microsoft",
             ("oauth",), ("policy_metadata", "policy_text"),
             ("Document libraries", "File metadata and versions")),

    _planned("cms", "CMS", "REGULATORY", "Centers for Medicare & Medicaid Services",
             ("none",), ("standard_metadata", "standard_text"),
             ("Conditions of Participation", "Interpretive guidance", "Revisions"),
             note="Public source. Requires no credentials."),
    _planned("joint_commission", "The Joint Commission", "REGULATORY",
             "The Joint Commission", ("api_key",),
             ("standard_metadata", "standard_text"),
             ("Standards and Elements of Performance", "Revision dates"),
             enablement=True,
             note="Requires your organization's standards subscription."),
    _planned("dnv", "DNV Healthcare", "REGULATORY", "DNV", ("api_key",),
             ("standard_metadata", "standard_text"),
             ("NIAHO standards", "Revision dates"), enablement=True),
    _planned("achc", "ACHC", "REGULATORY", "ACHC", ("api_key",),
             ("standard_metadata", "standard_text"), ("Accreditation standards",),
             enablement=True),
    _planned("state_regulatory", "State licensure source", "REGULATORY", "Varies",
             ("none", "file_import"), ("standard_metadata", "standard_text"),
             ("State licensure rules and updates",),
             note="Varies by state. File import is available today."),
]


def register_catalog() -> None:
    registry.register(FileImportConnector.info, FileImportConnector)
    for info in PLANNED:
        if not registry.get(info.id):
            registry.register(info, _unavailable_factory(info))


def _unavailable_factory(info: ConnectorInfo):
    def factory(config):  # pragma: no cover - registry refuses before this runs
        raise ConnectorError(f"{info.name} is not available yet.")
    return factory
