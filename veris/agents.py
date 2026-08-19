"""The agent framework.

An agent is a named, versioned piece of reasoning that produces findings with
provenance. Agents are modular so that adding one does not touch the dashboard,
the API, or any other agent — the registry is what the UI renders from.

Every finding an agent produces carries the four-way distinction the
constitution requires, and the framework makes it hard to blur:

    SOURCE FACT      the document says this
    OBSERVATION      Veris computed this from what it was given
    INFERENCE        a model judged this
    RECOMMENDATION   what a human might do about it

The most valuable agents here need **two connected systems at once**. The
policy-to-training agent compares a policy's effective date against the last
revision of the course that teaches it — a comparison neither the policy system
nor the LMS can make alone, because each holds only one half. That is the
product thesis expressed as code rather than as a slogan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .store import Store

AGENTS: dict[str, "AgentInfo"] = {}


@dataclass(frozen=True)
class AgentInfo:
    id: str
    name: str
    description: str
    produces: tuple[str, ...]
    requires: tuple[str, ...] = ()      # what must be connected for it to run
    version: str = "1.0"


@dataclass
class AgentResult:
    agent_id: str
    findings_created: int = 0
    examined: int = 0
    skipped_reason: str = ""
    notes: list[str] = field(default_factory=list)


def register(info: AgentInfo, fn: Callable[[Store], AgentResult]) -> None:
    AGENTS[info.id] = info
    _IMPL[info.id] = fn


_IMPL: dict[str, Callable[[Store], AgentResult]] = {}


def _days_between(a: str | None, b: str | None) -> int | None:
    try:
        d1 = datetime.fromisoformat((a or "")[:10])
        d2 = datetime.fromisoformat((b or "")[:10])
    except ValueError:
        return None
    return (d1 - d2).days


def _scope(store: Store) -> str:
    n = store.q("SELECT COUNT(*) n FROM documents")[0]["n"]
    c = store.q("SELECT COUNT(*) n FROM connections WHERE status IN ('SYNCED','CONNECTED')")[0]["n"]
    return f"{n} documents across {c} connected system(s)"


def _already(store: Store, title: str) -> bool:
    return bool(store.one("SELECT id FROM findings WHERE title = ?", (title,)))


# --------------------------------------------------------------------------
# Policy ↔ Training drift — requires the policy system AND the LMS
# --------------------------------------------------------------------------

POLICY_TRAINING = AgentInfo(
    id="policy_training",
    name="Policy-to-Training Agent",
    description="Compares when a policy was last effective against when the "
                "course that teaches it was last revised.",
    produces=("REQUIRES_HUMAN_REVIEW",),
    requires=("POLICY", "LMS"),
)

# Course title fragments that indicate a course teaches a policy subject. Kept
# deliberately narrow: a loose match here produces confident nonsense, and this
# is a lexical bridge between two systems that do not share identifiers.
SUBJECT_HINTS = [
    ("controlled substance", "controlled substance"),
    ("medication wast", "medication wast"),
    ("hand hygiene", "hand hygiene"),
    ("blood", "blood"),
    ("emergency", "emergency"),
]

DRIFT_DAYS = 90


def run_policy_training(store: Store) -> AgentResult:
    result = AgentResult(POLICY_TRAINING.id)
    have = {r["category"] for r in store.q(
        "SELECT DISTINCT category FROM connections WHERE status IN ('SYNCED','CONNECTED')")}
    courses = store.q("SELECT * FROM courses")
    if not courses:
        result.skipped_reason = ("No learning system is connected, so Veris cannot "
                                 "compare policies against the training that teaches them.")
        return result

    policies = store.q(
        "SELECT * FROM documents WHERE document_type IN ('POLICY','PROCEDURE')")
    scope = _scope(store)

    for policy in policies:
        title = (policy["title"] or "").lower()
        for hint, _ in SUBJECT_HINTS:
            if hint not in title:
                continue
            for course in courses:
                if hint not in (course["title"] or "").lower():
                    continue
                result.examined += 1
                gap = _days_between(policy["effective_date"],
                                    course["content_updated_at"])
                if gap is None or gap < DRIFT_DAYS:
                    continue
                finding_title = (f"{course['title']} may predate "
                                 f"{policy['title']}")
                if _already(store, finding_title):
                    continue
                store.add_finding(
                    finding_type="REQUIRES_HUMAN_REVIEW",
                    severity="MEDIUM" if gap < 365 else "HIGH",
                    title=finding_title,
                    statement=(
                        f"The policy took effect {policy['effective_date']} and the "
                        f"course was last revised {course['content_updated_at']} — "
                        f"{gap} days earlier. The training may not reflect the "
                        f"current policy."),
                    missing="", subject_entity_id=None, change_id=None,
                    confidence=0.55,
                    # An observation about two dates, not a model's opinion.
                    provenance_class="VERIS_INTERPRETATION",
                    scope=scope,
                    recommended_reviewer="Nursing Professional Development",
                )
                result.findings_created += 1
            break
    store.commit()
    return result


# --------------------------------------------------------------------------
# Gap analysis — structural, no model required
# --------------------------------------------------------------------------

GAP_ANALYSIS = AgentInfo(
    id="gap_analysis",
    name="Gap Analysis Agent",
    description="Finds policies with no owner, overdue reviews, requirements "
                "with no connected policy, and training with no policy behind it.",
    produces=("POTENTIAL_GAP", "REQUIRES_HUMAN_REVIEW"),
    requires=(),
)


def run_gap_analysis(store: Store) -> AgentResult:
    result = AgentResult(GAP_ANALYSIS.id)
    scope = _scope(store)
    today = datetime.now().date().isoformat()

    # A policy nobody owns cannot be reviewed by anyone.
    for doc in store.q("""SELECT * FROM documents
                          WHERE document_type IN ('POLICY','PROCEDURE')
                            AND (owner IS NULL OR owner = '')"""):
        result.examined += 1
        title = f"{doc['title']} has no recorded owner"
        if _already(store, title):
            continue
        store.add_finding(
            finding_type="POTENTIAL_GAP", severity="MEDIUM", title=title,
            statement=("No owner is recorded for this policy in the connected "
                       "system. Without an owner there is nobody to route a "
                       "review to."),
            missing="A named policy owner.",
            confidence=0.9, provenance_class="VERIS_INTERPRETATION",
            scope=scope, recommended_reviewer="Policy Owner",
        )
        result.findings_created += 1

    # Overdue review, read from the policy system's own metadata.
    import json as _json
    for doc in store.q("SELECT * FROM documents WHERE metadata IS NOT NULL"):
        try:
            meta = _json.loads(doc["metadata"] or "{}")
        except ValueError:
            continue
        review = meta.get("next_review_date")
        if not review or review >= today:
            continue
        result.examined += 1
        title = f"{doc['title']} is past its review date"
        if _already(store, title):
            continue
        store.add_finding(
            finding_type="REQUIRES_HUMAN_REVIEW", severity="MEDIUM", title=title,
            statement=(f"The connected policy system records a review date of "
                       f"{review}, which has passed."),
            missing="", confidence=0.95,
            # The review date is a source fact; that it has passed is Veris
            # comparing it to today, which makes the finding an interpretation.
            provenance_class="VERIS_INTERPRETATION",
            scope=scope, recommended_reviewer=doc["owner"] or "Policy Owner",
        )
        result.findings_created += 1

    # A required course that teaches nothing Veris can connect to a policy.
    linked = {r["course_id"] for r in store.q("SELECT course_id FROM course_entity_links")}
    required = store.q("SELECT id FROM courses WHERE required = 1")
    unlinked = [c for c in required if c["id"] not in linked]
    result.examined += len(required)
    if unlinked:
        result.notes.append(
            f"{len(unlinked)} of {len(required)} required courses are not yet "
            f"connected to a policy or requirement.")
    store.commit()
    return result


# --------------------------------------------------------------------------
# Survey readiness — a view, not a score
# --------------------------------------------------------------------------

SURVEY_READINESS = AgentInfo(
    id="survey_readiness",
    name="Survey Readiness Agent",
    description="Summarises requirements, policies, training, evidence and "
                "ownership, and states what is not connected.",
    produces=(),
    requires=(),
)


def run_survey_readiness(store: Store) -> AgentResult:
    """Deliberately produces no findings and no score.

    A single readiness percentage would be the most requested number in the
    product and the least defensible: it would average over knowledge Veris has
    read, knowledge it has only metadata for, and knowledge nobody has connected,
    and present the result as if it measured the organization.
    """
    result = AgentResult(SURVEY_READINESS.id)
    reqs = store.q("SELECT COUNT(*) n FROM entities WHERE role='REQUIRES'")[0]["n"]
    covered = store.q("""SELECT COUNT(DISTINCT from_entity_id) n FROM relationships
                         WHERE relationship_type IN
                         ('DIRECTLY_ADDRESSES','PARTIALLY_ADDRESSES')""")[0]["n"]
    unowned = store.q("""SELECT COUNT(*) n FROM documents
                         WHERE document_type IN ('POLICY','PROCEDURE')
                           AND (owner IS NULL OR owner='')""")[0]["n"]
    open_findings = store.q(
        "SELECT COUNT(*) n FROM findings WHERE status='PROPOSED'")[0]["n"]
    result.examined = reqs
    result.notes = [
        f"{covered} of {reqs} requirements have at least one connected policy or procedure.",
        f"{unowned} connected policies have no recorded owner.",
        f"{open_findings} findings are awaiting human review.",
    ]
    missing_roles = [role for role in ("MEASURES", "TEACHES", "VALIDATES")
                     if not store.q("SELECT 1 FROM entities WHERE role=? LIMIT 1", (role,))]
    for role in missing_roles:
        result.notes.append(
            f"Nothing connected plays the {role.lower()} role, so readiness in "
            f"that dimension cannot be assessed.")
    return result


register(POLICY_TRAINING, run_policy_training)
register(GAP_ANALYSIS, run_gap_analysis)
register(SURVEY_READINESS, run_survey_readiness)


def run_agent(store: Store, agent_id: str) -> AgentResult:
    if agent_id not in _IMPL:
        raise KeyError(f"Unknown agent: {agent_id}")
    result = _IMPL[agent_id](store)
    store.log("agent_run",
              f"{agent_id} · examined {result.examined} · "
              f"created {result.findings_created}")
    store.commit()
    return result


def run_all(store: Store) -> list[AgentResult]:
    return [run_agent(store, aid) for aid in AGENTS]
