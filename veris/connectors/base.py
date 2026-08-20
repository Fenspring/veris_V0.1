"""The connector framework.

Every external system — LMS, policy management, regulatory source — implements
one interface. Vendor-specific behaviour lives inside the connector and nowhere
else; if a vendor's quirk leaks into the sync engine or the dashboard, the
abstraction has failed.

Four properties are deliberate:

**Read-only by construction.** There is no write method. Veris reads,
normalizes, relates and recommends; it never changes a customer's system. That
is enforced by the shape of the interface rather than by a rule someone has to
remember.

**The registry drives the UI.** A connector declares its category, the auth
methods it supports, what it will read, and what it will never touch. The
Connection Center renders from that declaration, so adding an integration adds
no dashboard code.

**A connector states what it needs from the customer.** Many healthcare vendors
require an account manager to enable API access. Saying so on the connection
screen is the difference between an honest product and one that appears broken.

**A connector declares what it can provide, in a shared vocabulary.** Not a
feature list — a statement about what Veris can and cannot assess once this is
connected. The intelligence layer reads those declarations rather than connector
ids, so connecting a different vendor that supplies the same thing changes what
Veris can say without changing a line of reasoning.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclasses_fields
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

CATEGORIES = ("LMS", "POLICY", "REGULATORY", "IDENTITY", "DOCUMENT", "EVIDENCE")

# How a connector can be given access. Not every vendor offers an API, and a
# framework that assumes one cannot integrate most of healthcare.
AUTH_METHODS = (
    "oauth", "api_key", "basic", "sftp", "file_import", "manual_upload", "none",
)

HEALTH_STATES = (
    "CONNECTED", "SYNCING", "SYNCED", "WARNING",
    "AUTHENTICATION_REQUIRED", "ERROR", "DISCONNECTED",
)


# --- What a connector can provide -------------------------------------------
#
# A capability is not a feature list. It is a statement about what Veris can and
# cannot assess once this connector is connected, which is why each one carries
# `without_it`: the sentence the product says when nothing supplies it.
#
# The intelligence layer reads this vocabulary rather than connector ids. An
# agent declares the capabilities it needs; if none of the connected systems
# provides one, the agent does not run and the customer is told exactly which
# knowledge is missing and what that costs them. Adding a connector therefore
# widens what Veris can assess without touching an agent, and removing one
# narrows it without leaving an agent quietly guessing.


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    kind: str        # 'knowledge' becomes citable documents; 'operational' never does
    enables: str     # what Veris can say when something provides this
    without_it: str  # what Veris cannot assess when nothing does


CAPABILITIES: dict[str, Capability] = {c.id: c for c in (
    # Knowledge. Text can be cited; metadata alone cannot, and the difference
    # between these two is the difference between "Veris has read this" and
    # "Veris knows this exists" (Decision 0007).
    Capability(
        "policy_metadata", "Policy metadata", "knowledge",
        "Which policies exist, who owns them, their versions, effective dates "
        "and review dates.",
        "Veris cannot tell you which policies are unowned, overdue for review, "
        "or missing entirely."),
    Capability(
        "policy_text", "Policy text", "knowledge",
        "The words of each policy, so statements can be extracted and cited.",
        "Veris can name your policies but cannot quote them, so no finding can "
        "rest on what a policy actually says."),
    Capability(
        "standard_metadata", "Standard metadata", "knowledge",
        "Which standards and elements of performance exist, and when each was "
        "last revised.",
        "Veris cannot tell you when a standard changed, so it cannot tell you "
        "what a change affects."),
    Capability(
        "standard_text", "Standard text", "knowledge",
        "The text of each requirement, so obligations can be extracted and cited.",
        "Veris cannot read the requirements themselves, so gaps and conflicts "
        "against them cannot be established."),
    Capability(
        "education_content", "Education content", "knowledge",
        "The objectives a course teaches, so training can be compared against "
        "the policy it is meant to teach.",
        "Veris can see that a course exists but not what it teaches, so it "
        "cannot tell you whether the training matches the policy."),

    # Operational facts. True, useful, and never citable — no document says them.
    Capability(
        "course_catalog", "Course catalogue", "operational",
        "Courses, their categories, whether they are required, and when the "
        "content was last revised.",
        "Veris cannot tell you whether training exists for an obligation."),
    Capability(
        "person_roster", "Staff roster", "operational",
        "Who works here, in what role and department.",
        "Veris cannot tell you who an obligation applies to."),
    Capability(
        "completion_records", "Training completions", "operational",
        "Who completed what, and what is overdue.",
        "Veris cannot tell you whether required training was actually done."),
    Capability(
        "acknowledgments", "Policy acknowledgements", "operational",
        "Who attested to having read a policy.",
        "Veris cannot tell you whether staff have seen a policy that changed."),
    Capability(
        "audit_results", "Audit and observation results", "operational",
        "Observed practice — audits, rounding, competency validation.",
        "Veris cannot tell you whether any of this is working in practice. It "
        "can only tell you what the organization has written down."),
    Capability(
        "incident_records", "Reported events", "operational",
        "Events staff have reported.",
        "Veris cannot connect an obligation to what has gone wrong around it."),
)}


def describe_capabilities(ids: Iterable[str]) -> list[dict]:
    """Capability declarations as data, for the UI and the API."""
    return [asdict(CAPABILITIES[c]) for c in ids if c in CAPABILITIES]


# --- Availability and verification ------------------------------------------
#
# Three states, and the middle one is the honest answer to a question most
# integration catalogues never ask: *has anyone actually run this?*
#
#   available   implemented and proven against the real system
#   unverified  implemented against a published contract, never executed live
#   planned     declared so the customer can see what is coming; refuses to connect
#
# `unverified` exists because the alternatives are both dishonest. Calling such
# a connector `available` claims a working integration on the strength of code
# compiling. Calling it `planned` hides working code a design partner could
# verify in an afternoon. Neither tells the customer what is true.

AVAILABILITY = ("available", "unverified", "planned")


@dataclass(frozen=True)
class Verification:
    """What was actually verified about a connector, and by whom.

    Never inferred and never defaulted to something reassuring. A connector
    nobody has run says so, in the registry, in the API, and on the screen.
    """
    status: str = "unverified"           # unverified | verified | mock
    verified_at: str = ""                # ISO date of the verification run
    verified_by: str = ""                # who ran it
    environment: str = ""                # where it ran, in plain language
    endpoints: tuple[str, ...] = ()      # what was actually called
    checks_passed: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = ()
    reason: str = ""                     # why it is unverified, if it is
    exercised_against: str = ""          # what it *has* been run against
    notes: str = ""

    @classmethod
    def recorded(cls, connector_id: str) -> "Verification | None":
        """Load what a verification run actually recorded, if one has happened.

        A connector cannot declare itself verified; it declares what it is
        without one. `make verify-connector` writes the record, and that record
        is what promotes it. The claim therefore always traces to a run, with a
        date, a person, and a list of what passed.
        """
        directory = Path(os.environ.get("VERIS_VERIFICATION_DIR")
                         or Path(__file__).resolve().parents[2]
                         / "docs" / "connectors" / "verification")
        path = directory / f"{connector_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        fields = {f.name for f in dataclasses_fields(cls)}
        return cls(**{k: (tuple(v) if isinstance(v, list) else v)
                      for k, v in data.items() if k in fields})

    @property
    def summary(self) -> str:
        if self.status == "mock":
            return "Demo data — no external system to verify against."
        if self.status == "verified":
            return ((f"Verified {self.verified_at} by {self.verified_by or 'unknown'}"
                     if self.verified_at else
                     f"Verified by {self.verified_by or 'unknown'}")
                    + f" · {len(self.checks_passed)} checks passed")
        return ("Never run against the live system. " + (self.reason or "")).strip()


class ConnectorError(RuntimeError):
    """Base for connector failures. Messages are safe to show a customer:
    they never carry credentials, tokens, or document contents."""


class AuthenticationError(ConnectorError):
    pass


class RateLimited(ConnectorError):
    """Raised with the server's retry hint so the sync engine can back off
    rather than hammering a vendor and getting the customer's key revoked."""

    def __init__(self, message: str, retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(ConnectorError):
    """A failure worth retrying — a timeout, a 5xx, a dropped connection."""


@dataclass(frozen=True)
class ConnectorInfo:
    """Registry metadata. This is what the Connection Center renders from."""
    id: str
    name: str
    category: str
    vendor: str = ""
    auth_methods: tuple[str, ...] = ("api_key",)
    # Capabilities are keys into CAPABILITIES above, validated at registration.
    # A free-text list would let a connector claim something the intelligence
    # layer cannot act on, which is the same as claiming nothing.
    capabilities: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()                 # plain language, shown to the customer
    writes: tuple[str, ...] = ()                # always empty; present to be visibly empty
    # How the source behaves, so the sync engine and the UI stop guessing.
    supports_incremental: bool = False          # can return only what changed
    supports_webhooks: bool = False
    rate_limit: str = ""                        # plain language, e.g. "600 requests/minute"
    is_mock: bool = False
    # See AVAILABILITY above. A planned connector must never look connectable,
    # and an unverified one must never look proven. Never fake an integration.
    availability: str = "available"
    verification: Verification = field(default_factory=Verification)
    requires_vendor_enablement: bool = False    # customer must ask their rep first
    setup_note: str = ""                        # shown before the customer starts
    docs_url: str = ""

    def as_dict(self) -> dict:
        d = {k: (list(v) if isinstance(v, tuple) else v)
             for k, v in self.__dict__.items()}
        d["verification"] = {**asdict(self.verification),
                             "summary": self.verification.summary}
        d["capabilities"] = describe_capabilities(self.capabilities)
        return d


@dataclass
class AuthResult:
    ok: bool
    message: str = ""
    requires_user_action: bool = False


@dataclass
class ConnectionStatus:
    state: str
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class DiscoveryResult:
    """What the connector found before syncing anything.

    `counts` is what the customer sees — "827 courses discovered" — and is the
    moment the product stops feeling like configuration.
    """
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[dict]] = field(default_factory=dict)
    fields: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class SyncPage:
    """One page of normalized records, plus the cursor to resume after it.

    Connectors yield pages rather than returning everything, so a sync over
    14,000 policies can be checkpointed and resumed after an interruption
    instead of starting again.
    """
    records: list[dict]
    cursor: str | None = None
    has_more: bool = False


@dataclass
class HealthStatus:
    """What a connector reports about itself. Deliberately small: a connector
    knows whether it can reach its source, not what Veris has done with it."""
    state: str
    message: str = ""
    latency_ms: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class ConnectorHealth:
    """One shape for every connection, whatever the vendor.

    The Connection Center, the dashboard and the API all render this and nothing
    else. A connector that behaves badly cannot invent its own status vocabulary,
    and a page that shows connection health does not need to know which vendors
    exist.

    `capabilities` and `degraded_capabilities` are the operative fields: a
    connection can be reachable and authenticated while no longer supplying
    something the intelligence layer depends on, and that is a different failure
    from being down.
    """
    connection_id: str
    connector_id: str
    name: str
    category: str
    state: str                                  # one of HEALTH_STATES
    message: str = ""
    is_mock: bool = False
    authenticated: bool = False
    auth_method: str = ""
    capabilities: tuple[str, ...] = ()
    degraded_capabilities: tuple[str, ...] = () # declared, but nothing arrived
    last_sync_at: str | None = None
    next_sync_at: str | None = None
    last_run: dict | None = None                # status, counts, duration
    consecutive_failures: int = 0
    records: dict[str, int] = field(default_factory=dict)
    latency_ms: int | None = None
    error: str = ""                             # already redacted
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["capabilities"] = describe_capabilities(self.capabilities)
        d["degraded_capabilities"] = describe_capabilities(self.degraded_capabilities)
        return d


@runtime_checkable
class Connector(Protocol):
    """The whole surface. Note the absence of any write operation."""

    info: ConnectorInfo

    def authenticate(self, credentials: dict[str, str]) -> AuthResult: ...

    def test_connection(self) -> ConnectionStatus: ...

    def discover(self) -> DiscoveryResult: ...

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]: ...

    def health_check(self) -> HealthStatus: ...

    def disconnect(self) -> None: ...


class ConnectorRegistry:
    """Where connectors announce themselves.

    Registration is metadata plus a factory. Nothing in the dashboard, the sync
    engine, or the API knows any connector by name.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Any] = {}
        self._info: dict[str, ConnectorInfo] = {}

    def register(self, info: ConnectorInfo, factory) -> None:
        if info.category not in CATEGORIES:
            raise ValueError(f"Unknown connector category: {info.category}")
        if info.availability not in AVAILABILITY:
            raise ValueError(f"Unknown availability: {info.availability}")
        if (info.availability == "available"
                and info.verification.status not in ("verified", "mock")):
            # A connector cannot promote itself. `available` means somebody ran
            # it against the real system and recorded what passed.
            raise ValueError(
                f"{info.id} declares availability 'available' but carries no "
                f"verification record. Use 'unverified' until "
                f"`make verify-connector CONNECTOR={info.id}` has been run.")
        for method in info.auth_methods:
            if method not in AUTH_METHODS:
                raise ValueError(f"Unknown auth method: {method}")
        for capability in info.capabilities:
            if capability not in CAPABILITIES:
                # A capability the intelligence layer does not understand is a
                # promise nothing can keep.
                raise ValueError(
                    f"{info.id} declares unknown capability {capability!r}")
        if info.writes:
            # Belt and braces: connectors are read-only, and a connector that
            # declares a write capability is a bug, not a feature request.
            raise ValueError(f"{info.id} declares write capabilities; connectors are read-only")
        self._factories[info.id] = factory
        self._info[info.id] = _with_recorded_verification(info)

    def create(self, connector_id: str, config: dict | None = None) -> Connector:
        if connector_id not in self._factories:
            raise ConnectorError(f"No connector registered with id {connector_id!r}")
        info = self._info[connector_id]
        if info.availability == "planned":
            raise ConnectorError(
                f"{info.name} is not available yet. {info.setup_note}".strip())
        # `unverified` connectors are creatable on purpose: a connector nobody
        # can run is a connector nobody can verify. What must not happen is
        # anyone mistaking it for proven, which is why the state is carried on
        # the registry entry, the health record, and the connection screen.
        return self._factories[connector_id](config or {})

    def available(self) -> list[ConnectorInfo]:
        """Connectors that can be connected — proven and unproven alike.

        The distinction between them is `availability`, carried everywhere it
        is rendered, not a filter that hides one of them.
        """
        return [i for i in self.all() if i.availability != "planned"]

    def verified(self) -> list[ConnectorInfo]:
        return [i for i in self.all() if i.verification.status == "verified"]

    def get(self, connector_id: str) -> ConnectorInfo | None:
        return self._info.get(connector_id)

    def all(self) -> list[ConnectorInfo]:
        return sorted(self._info.values(), key=lambda i: (i.category, i.name))

    def providing(self, capability: str) -> list[ConnectorInfo]:
        """Every connector that could supply a capability, available or not.

        Used to answer "what would I have to connect to find that out?" — the
        other half of telling someone what Veris cannot assess.
        """
        return [i for i in self.all() if capability in i.capabilities]

    def by_category(self) -> dict[str, list[ConnectorInfo]]:
        out: dict[str, list[ConnectorInfo]] = {}
        for info in self.all():
            out.setdefault(info.category, []).append(info)
        return out


def _with_recorded_verification(info: ConnectorInfo) -> ConnectorInfo:
    """Overlay what a verification run recorded, if there is one.

    This is the only path from `unverified` to `available`. A connector's own
    declaration can never promote it, which is what stops "it compiles" from
    becoming "it works" one optimistic edit at a time.
    """
    recorded = Verification.recorded(info.id)
    if recorded is None or recorded.status != "verified":
        return info
    availability = "available" if info.availability == "unverified" else info.availability
    return replace(info, verification=recorded, availability=availability)


registry = ConnectorRegistry()
