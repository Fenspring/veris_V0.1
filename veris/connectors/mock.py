"""Mock connectors — a demonstrable product before any vendor credential exists.

These implement exactly the same interface as a real connector and pass exactly
the same contract tests. That matters for more than demos: when a real
HealthStream connector is written, it is proven against a suite that already
has passing implementations rather than against nothing.

The data is deterministic (seeded) so tests are stable, and it is shaped to be
*coherent with the knowledge corpus*: the controlled substance course here was
last revised before the standard changed, which is exactly the kind of
cross-system drift no single system can see on its own. A mock that produced
random noise would demonstrate the plumbing and none of the point.

Every mock declares `is_mock=True`. The UI labels them. A mock is never
presented as a live integration.
"""

from __future__ import annotations

import hashlib
import random
from typing import Iterable

from .base import (
    AuthResult, ConnectionStatus, Connector, ConnectorInfo, DiscoveryResult,
    HealthStatus, SyncPage, Verification, registry,
)

DEPARTMENTS = ["Nursing", "Pharmacy", "Emergency", "Surgical Services",
               "Laboratory", "Radiology", "Respiratory Therapy", "Environmental Services"]
ROLES = ["Registered Nurse", "Licensed Practical Nurse", "Pharmacist",
         "Pharmacy Technician", "Nursing Assistant", "Respiratory Therapist",
         "Surgical Technologist", "Physician"]
FACILITIES = ["Northstar Medical Center", "Northstar West Campus",
              "Northstar Ambulatory Surgery"]


# A mock has no external system behind it, so "verified" and "unverified" are
# both the wrong word. Saying so is better than leaving a status that reads as a
# judgement about an integration that does not exist.
DEMO_VERIFICATION = Verification(
    status="mock",
    environment="In-process, deterministic.",
    reason="Demo data. There is no external system to verify against.",
    exercised_against="The same contract suite every real connector must pass "
                      "(tests/test_connectors.py).",
)


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))


class _MockBase:
    """Shared behaviour. Real connectors will not inherit from this — they only
    share the interface, which is the point of having one."""

    def __init__(self, config: dict):
        self.config = config or {}
        self._authenticated = False
        # Scope: discovery always reports the true total, sync respects the
        # scope the customer chose. Reporting 14,284 policies and syncing 40 is
        # honest behaviour, not a shortcut.
        self.scope_limit = int(self.config.get("scope_limit", 0)) or None

    def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        self._authenticated = True
        return AuthResult(ok=True, message="Demo connection established")

    def test_connection(self) -> ConnectionStatus:
        return ConnectionStatus("CONNECTED" if self._authenticated
                                else "AUTHENTICATION_REQUIRED",
                                "Demo data source reachable")

    def health_check(self) -> HealthStatus:
        return HealthStatus("CONNECTED" if self._authenticated else "DISCONNECTED",
                            "Demo connector", latency_ms=0, detail={"mock": True})

    def disconnect(self) -> None:
        self._authenticated = False


# --------------------------------------------------------------------------
# LMS
# --------------------------------------------------------------------------

# Courses that correspond to real knowledge in the corpus, so cross-system
# findings are genuine rather than manufactured.
ANCHOR_COURSES = [
    ("CS-101", "Controlled Substance Safety — Annual", "Medication Safety",
     "2025-01-10", True),
    ("CS-140", "Medication Waste and Diversion Prevention", "Medication Safety",
     "2024-11-02", True),
    ("IC-210", "Hand Hygiene and Standard Precautions", "Infection Prevention",
     "2025-06-14", True),
    ("EM-330", "Emergency Preparedness and Response", "Emergency Management",
     "2024-08-21", True),
    ("BL-120", "Blood Product Administration", "Transfusion Safety",
     "2025-03-30", True),
]


class MockLMS(_MockBase):
    """Shaped after a large healthcare LMS: courses, people, assignments,
    completions."""

    info = ConnectorInfo(
        id="mock_lms",
        name="Demo Learning System",
        category="LMS",
        vendor="Veris Demo",
        auth_methods=("none",),
        capabilities=("course_catalog", "person_roster", "completion_records"),
        reads=("Course catalogue", "Staff roster and roles",
               "Assignments and due dates", "Completion records"),
        supports_incremental=True,
        is_mock=True,
        setup_note="Demo data. Connect a real LMS to replace it.",
        verification=DEMO_VERIFICATION,
    )

    TOTAL_PEOPLE = 12_842
    TOTAL_COURSES = 827
    TOTAL_COMPLETIONS = 14_203

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            counts={"courses": self.TOTAL_COURSES, "people": self.TOTAL_PEOPLE,
                    "completions": self.TOTAL_COMPLETIONS},
            fields={"course": ["id", "title", "category", "content_updated_at", "required"],
                    "person": ["id", "name", "job_role", "department", "facility"],
                    "completion": ["id", "person_id", "course_id", "status",
                                   "completed_at", "due_at"]},
            notes=[f"{self.TOTAL_COURSES} courses discovered",
                   f"{self.TOTAL_PEOPLE:,} staff records discovered"],
        )

    def _courses(self, limit: int) -> list[dict]:
        out = []
        for cid, title, cat, updated, required in ANCHOR_COURSES[:limit]:
            out.append({"_type": "course", "external_id": cid, "title": title,
                        "category": cat, "content_updated_at": updated,
                        "required": required})
        r = _rng("courses")
        topics = ["Fall Prevention", "Sepsis Recognition", "Restraint Use",
                  "Patient Identification", "Fire Safety", "Workplace Violence",
                  "Pain Assessment", "Pressure Injury Prevention"]
        i = 0
        while len(out) < limit:
            t = topics[i % len(topics)]
            i += 1
            out.append({"_type": "course", "external_id": f"GEN-{i:04d}",
                        "title": f"{t} — Annual Update",
                        "category": "General Compliance",
                        "content_updated_at": f"202{r.randint(4,5)}-"
                                              f"{r.randint(1,12):02d}-{r.randint(1,28):02d}",
                        "required": r.random() < 0.6})
        return out

    def _people(self, limit: int) -> list[dict]:
        r = _rng("people")
        return [{"_type": "person", "external_id": f"E{100000+i}",
                 "name": f"Staff Member {i+1}",
                 "job_role": r.choice(ROLES),
                 "department": r.choice(DEPARTMENTS),
                 "facility": r.choice(FACILITIES),
                 "active": True} for i in range(limit)]

    def _completions(self, courses: list[dict], people: list[dict],
                     limit: int) -> list[dict]:
        r = _rng("completions")
        out = []
        n = 0
        while len(out) < limit and courses and people:
            c = courses[n % len(courses)]
            p = people[(n * 7) % len(people)]
            n += 1
            status = r.choices(["COMPLETED", "ASSIGNED", "OVERDUE"],
                               weights=[0.82, 0.12, 0.06])[0]
            out.append({
                "_type": "completion", "external_id": f"CMP-{n:06d}",
                "person_external_id": p["external_id"],
                "course_external_id": c["external_id"],
                "status": status,
                "completed_at": f"2026-0{r.randint(1,8)}-{r.randint(1,28):02d}"
                                if status == "COMPLETED" else None,
                "due_at": f"2026-{r.randint(9,12)}-{r.randint(1,28):02d}",
            })
        return out

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]:
        # A demo syncs a representative slice; discovery already reported the
        # true totals, and the UI shows both.
        n_courses = min(self.scope_limit or 40, self.TOTAL_COURSES)
        n_people = min((self.scope_limit or 40) * 5, self.TOTAL_PEOPLE)
        n_completions = min((self.scope_limit or 40) * 10, self.TOTAL_COMPLETIONS)

        courses = self._courses(n_courses)
        people = self._people(n_people)
        yield SyncPage(courses, cursor="courses_done", has_more=True)
        yield SyncPage(people, cursor="people_done", has_more=True)
        yield SyncPage(self._completions(courses, people, n_completions),
                       cursor="complete", has_more=False)


# --------------------------------------------------------------------------
# Policy management
# --------------------------------------------------------------------------

class MockPolicySystem(_MockBase):
    """Shaped after a policy management system: policies with owners, versions,
    review dates and approval status."""

    info = ConnectorInfo(
        id="mock_policy",
        name="Demo Policy System",
        category="POLICY",
        vendor="Veris Demo",
        auth_methods=("none",),
        # Metadata only. This connector returns no policy text and no
        # acknowledgement records, so it declares neither — an overclaimed
        # capability would make the intelligence layer believe it can assess
        # something no data supports.
        capabilities=("policy_metadata",),
        reads=("Policy titles, owners and departments",
               "Version history and effective dates", "Review schedule"),
        supports_incremental=True,
        is_mock=True,
        setup_note="Demo data. Connect a real policy system to replace it.",
        verification=DEMO_VERIFICATION,
    )

    TOTAL_POLICIES = 14_284

    CATALOGUE = [
        ("POL-0412", "Controlled Substance Management Policy", "Pharmacy",
         "Director of Pharmacy", "4.2", "2024-11-15", "2026-11-15", "APPROVED"),
        ("POL-0418", "Medication Wasting Procedure", "Nursing",
         "Nurse Executive", "2.1", "2025-02-01", "2027-02-01", "APPROVED"),
        ("POL-0233", "Hand Hygiene Policy", "Infection Prevention",
         "Infection Preventionist", "3.0", "2025-06-01", "2027-06-01", "APPROVED"),
        ("POL-0501", "Blood and Blood Product Administration", "Laboratory",
         "Blood Bank Manager", "5.1", "2024-04-18", "2026-04-18", "UNDER_REVIEW"),
        ("POL-0140", "Emergency Operations Plan", "Emergency Management",
         "", "1.9", "2023-09-30", "2025-09-30", "APPROVED"),
        ("POL-0622", "Sharps and Regulated Waste Disposal", "Environmental Services",
         "Environment of Care Manager", "2.0", "2024-09-01", "2026-09-01", "APPROVED"),
    ]

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            counts={"policies": self.TOTAL_POLICIES},
            samples={"policy": [{"external_id": c[0], "title": c[1]}
                                for c in self.CATALOGUE[:3]]},
            fields={"policy": ["id", "title", "department", "owner", "version",
                              "effective_date", "next_review_date", "status"]},
            notes=[f"{self.TOTAL_POLICIES:,} policies discovered"],
        )

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]:
        records = []
        for (ext, title, dept, owner, version, effective,
             review, status) in self.CATALOGUE:
            records.append({
                "_type": "policy_record", "external_id": ext, "title": title,
                "department": dept, "owner": owner, "version": version,
                "effective_date": effective, "next_review_date": review,
                "status": status,
            })
        yield SyncPage(records, cursor="complete", has_more=False)


# --------------------------------------------------------------------------
# Regulatory / standards
# --------------------------------------------------------------------------

class MockRegulatory(_MockBase):
    """Shaped after a standards feed: authority, program, standard, requirement,
    effective dates and revisions."""

    info = ConnectorInfo(
        id="mock_regulatory",
        name="Demo Standards Feed",
        category="REGULATORY",
        vendor="Veris Demo",
        auth_methods=("none",),
        # The feed names requirements and dates it but does not supply their
        # text, so `standard_text` is not declared: nothing here can be cited.
        capabilities=("standard_metadata",),
        reads=("Standards and requirements", "Effective and revision dates",
               "Citations and crosswalks"),
        is_mock=True,
        setup_note="Demo data. Connect a licensed standards source to replace it.",
        verification=DEMO_VERIFICATION,
    )

    REQUIREMENTS = [
        ("NS-CS.02.01", "EP 2", "Controlled substance waste witnessing",
         "2026-07-01", "2.0"),
        ("NS-CS.02.01", "EP 3", "Count discrepancy reconciliation window",
         "2026-07-01", "2.0"),
        ("NS-CS.02.01", "EP 6", "Quarterly waste documentation audit",
         "2026-07-01", "2.0"),
    ]

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            counts={"authorities": 1, "standards": 1,
                    "requirements": len(self.REQUIREMENTS)},
            fields={"requirement": ["standard", "element", "title",
                                    "effective_date", "revision"]},
            notes=["1 standards program discovered",
                   f"{len(self.REQUIREMENTS)} requirements in the current revision"],
        )

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]:
        yield SyncPage(
            [{"_type": "requirement_record", "external_id": f"{s}/{e}",
              "standard": s, "element": e, "title": t,
              "effective_date": eff, "revision": rev}
             for s, e, t, eff, rev in self.REQUIREMENTS],
            cursor="complete", has_more=False)


def register_mocks() -> None:
    registry.register(MockLMS.info, MockLMS)
    registry.register(MockPolicySystem.info, MockPolicySystem)
    registry.register(MockRegulatory.info, MockRegulatory)
