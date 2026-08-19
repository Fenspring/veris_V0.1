"""The connector framework.

Every external system — LMS, policy management, regulatory source — implements
one interface. Vendor-specific behaviour lives inside the connector and nowhere
else; if a vendor's quirk leaks into the sync engine or the dashboard, the
abstraction has failed.

Three properties are deliberate:

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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    capabilities: tuple[str, ...] = ()          # 'courses', 'policies', 'completions'
    reads: tuple[str, ...] = ()                 # plain language, shown to the customer
    writes: tuple[str, ...] = ()                # always empty; present to be visibly empty
    is_mock: bool = False
    # available: implemented and usable now.
    # planned:   declared so the customer can see it is coming and what it will
    #            need — shown in the Connection Center, refuses to connect.
    # A planned connector must never look connectable. Never fake an integration.
    availability: str = "available"
    requires_vendor_enablement: bool = False    # customer must ask their rep first
    setup_note: str = ""                        # shown before the customer starts
    docs_url: str = ""

    def as_dict(self) -> dict:
        d = {k: (list(v) if isinstance(v, tuple) else v)
             for k, v in self.__dict__.items()}
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
    state: str
    message: str = ""
    detail: dict = field(default_factory=dict)


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
        for method in info.auth_methods:
            if method not in AUTH_METHODS:
                raise ValueError(f"Unknown auth method: {method}")
        if info.writes:
            # Belt and braces: connectors are read-only, and a connector that
            # declares a write capability is a bug, not a feature request.
            raise ValueError(f"{info.id} declares write capabilities; connectors are read-only")
        self._factories[info.id] = factory
        self._info[info.id] = info

    def create(self, connector_id: str, config: dict | None = None) -> Connector:
        if connector_id not in self._factories:
            raise ConnectorError(f"No connector registered with id {connector_id!r}")
        info = self._info[connector_id]
        if info.availability != "available":
            raise ConnectorError(
                f"{info.name} is not available yet. {info.setup_note}".strip())
        return self._factories[connector_id](config or {})

    def available(self) -> list[ConnectorInfo]:
        return [i for i in self.all() if i.availability == "available"]

    def get(self, connector_id: str) -> ConnectorInfo | None:
        return self._info.get(connector_id)

    def all(self) -> list[ConnectorInfo]:
        return sorted(self._info.values(), key=lambda i: (i.category, i.name))

    def by_category(self) -> dict[str, list[ConnectorInfo]]:
        out: dict[str, list[ConnectorInfo]] = {}
        for info in self.all():
            out.setdefault(info.category, []).append(info)
        return out


registry = ConnectorRegistry()
