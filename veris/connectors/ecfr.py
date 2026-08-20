"""The eCFR connector — federal regulation, straight from the source.

The Electronic Code of Federal Regulations is published by the Government
Publishing Office with a free, public, credential-free API. For a hospital that
makes it the one external system where the *authoritative* text of a rule they
must follow is available without an account manager, a contract, or a
procurement cycle. 42 CFR 482 — Conditions of Participation for Hospitals — is
the regulation almost every accreditation standard in an American hospital
ultimately crosswalks to.

Unlike a policy system or an LMS, this source supplies **text**, not just
metadata. Sections arrive as documents through the same pipeline a dropped-in
PDF uses, get the same frozen canonical text and the same byte-verified spans,
and are therefore citable. That is what makes a finding that spans a hospital
policy and a federal requirement possible at all.

---

## Verification status: UNVERIFIED

**This connector has never been executed against the live eCFR API.** It was
written against the published API documentation, in an environment whose network
policy refuses outbound connections to every host except package registries.

That is stated in the connector's `availability`, in its `Verification` record,
in the Connection Center, and here. Veris does not claim an integration works
because its code compiles.

What *is* exercised: the parsers, against fixtures under
`tests/fixtures/ecfr/`. Those fixtures are hand-written to the documented
response shape — they are not captured traffic, and they prove the parser is
self-consistent, not that eCFR returns this. The distinction matters and is not
softened anywhere in this codebase.

To verify it for real, on a machine with network access:

    make verify-connector CONNECTOR=ecfr

That records exactly which checks passed, against which endpoints, on which
date, and writes the result to `docs/connectors/verification/ecfr.json`. Until
that file says otherwise, this connector is unverified.

### What verification must confirm

The parser makes assumptions that documentation alone cannot settle. Each is
marked `# VERIFY:` at the point it is made, and each is a check in the harness:

1. `DIV8/@N` — whether section numbers arrive as `482.13` or `§ 482.13`.
2. Paragraph designators — whether `<P>` text carries its own `(a)(1)`
   designator, and how deep nesting is expressed.
3. `content_versions[].amendment_date` — present on every entry, or only on
   substantive ones.
4. Rate limiting — whether a `Retry-After` header is sent, and on what status.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from .base import (
    AuthResult, ConnectionStatus, ConnectorError, ConnectorInfo, DiscoveryResult,
    HealthStatus, RateLimited, SyncPage, TransientError, Verification, registry,
)

BASE = "https://www.ecfr.gov"
USER_AGENT = "Veris/0.1 (healthcare knowledge platform; +https://veris.health)"
TIMEOUT = 30

# 42 CFR 482 unless told otherwise. Not a hardcoded integration — a default that
# is right for a hospital and wrong for nobody, since both are configurable.
DEFAULT_TITLE = 42
DEFAULT_PART = "482"


class EcfrConnector:
    """Read 42 CFR (or any title/part) from the eCFR versioner API."""

    info = ConnectorInfo(
        id="ecfr",
        name="eCFR — Code of Federal Regulations",
        category="REGULATORY",
        vendor="U.S. Government Publishing Office",
        auth_methods=("none",),
        # This source supplies the actual text, which is why it can be cited.
        capabilities=("standard_metadata", "standard_text"),
        reads=("The text of federal regulations",
               "Section titles and hierarchy",
               "Amendment dates and version history"),
        supports_incremental=True,
        rate_limit="Unpublished. The connector backs off on 429 and obeys "
                   "Retry-After when the server sends one.",
        availability="unverified",
        setup_note="Public source — no credentials, no vendor enablement. "
                   "Written against the published API and not yet run against "
                   "it; run `make verify-connector CONNECTOR=ecfr` from a "
                   "machine with network access before relying on it.",
        docs_url="https://www.ecfr.gov/developers/documentation/api/v1",
        verification=Verification(
            status="unverified",
            reason="Written against the published API documentation. The build "
                   "environment's network policy refuses outbound connections "
                   "to www.ecfr.gov, so no request has ever been made.",
            exercised_against="Hand-written fixtures matching the documented "
                              "response shape (tests/fixtures/ecfr/). Not "
                              "captured traffic.",
        ),
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.title = int(self.config.get("title", DEFAULT_TITLE))
        self.part = str(self.config.get("part", DEFAULT_PART))
        # Offline exercise. Set to a directory of documented-shape responses to
        # run the parsers without a network. Never a substitute for verification.
        self.fixture_dir = (Path(self.config["fixture_dir"])
                            if self.config.get("fixture_dir") else None)
        self.date: str | None = self.config.get("date")
        self._authenticated = False
        self._latency_ms: int | None = None

    # --- transport -----------------------------------------------------------

    def _fetch(self, path: str, params: dict | None = None,
               fixture: str = "") -> bytes:
        if self.fixture_dir is not None:
            candidate = self.fixture_dir / (fixture or Path(path).name)
            if not candidate.is_file():
                raise ConnectorError(f"No fixture for {path}")
            return candidate.read_bytes()

        url = f"{BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        import time
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
            self._latency_ms = int((time.monotonic() - started) * 1000)
            return body
        except urllib.error.HTTPError as e:
            # VERIFY: which status eCFR uses for throttling, and whether it
            # sends Retry-After. Obeying a hint we were given beats guessing.
            if e.code == 429:
                hint = e.headers.get("Retry-After") if e.headers else None
                raise RateLimited(f"eCFR rate limit on {path}",
                                  retry_after=float(hint) if hint else 60.0) from None
            if e.code >= 500:
                raise TransientError(f"eCFR returned {e.code} for {path}") from None
            if e.code == 404:
                raise ConnectorError(
                    f"eCFR has no content at {path}. Check the title and part "
                    f"— title {self.title}, part {self.part}.") from None
            raise ConnectorError(f"eCFR returned {e.code} for {path}") from None
        except urllib.error.URLError as e:
            raise TransientError(f"Could not reach eCFR: {e.reason}") from None
        except TimeoutError:
            raise TransientError(f"eCFR timed out after {TIMEOUT}s") from None

    def _fetch_json(self, path: str, params: dict | None = None,
                    fixture: str = "") -> dict:
        raw = self._fetch(path, params, fixture)
        try:
            return json.loads(raw)
        except ValueError:
            raise ConnectorError(f"eCFR returned unreadable JSON for {path}") from None

    # --- lifecycle -----------------------------------------------------------

    def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Public source. There is nothing to authenticate, and saying so is
        better than inventing a credential field nobody needs."""
        self._authenticated = True
        return AuthResult(ok=True, message="eCFR is public — no credentials required")

    def _current_date(self) -> str:
        """The date the requested title is current as of.

        eCFR is a point-in-time system: every content request is dated. Asking
        for today's date fails on days the title has not been rebuilt, so the
        date comes from the service rather than from the clock.
        """
        if self.date:
            return self.date
        titles = self._fetch_json("/api/versioner/v1/titles.json",
                                  fixture="titles.json").get("titles", [])
        for entry in titles:
            if int(entry.get("number", 0)) == self.title:
                date = (entry.get("up_to_date_as_of")
                        or entry.get("latest_issue_date"))
                if not date:
                    raise ConnectorError(
                        f"eCFR reports no current date for title {self.title}")
                self.date = date
                return date
        raise ConnectorError(f"eCFR does not publish a title {self.title}")

    def test_connection(self) -> ConnectionStatus:
        if not self._authenticated:
            return ConnectionStatus("AUTHENTICATION_REQUIRED",
                                    "Call authenticate() first")
        try:
            date = self._current_date()
        except ConnectorError as e:
            return ConnectionStatus("ERROR", str(e))
        return ConnectionStatus(
            "CONNECTED", f"Title {self.title} current as of {date}")

    def discover(self) -> DiscoveryResult:
        """What is there, before anything is synced."""
        versions = self._versions()
        sections = [v for v in versions if v.get("type") == "section"]
        amended = sorted((v.get("amendment_date") or "") for v in sections)
        notes = [f"{len(sections)} sections in {self.title} CFR {self.part}",
                 f"Title current as of {self._current_date()}"]
        if amended and amended[-1]:
            notes.append(f"Most recently amended {amended[-1]}")
        notes.append("UNVERIFIED CONNECTOR — these counts come from the eCFR "
                     "API as documented; no live request has been verified.")
        return DiscoveryResult(
            counts={"sections": len(sections)},
            fields={"section": ["identifier", "name", "amendment_date",
                                "issue_date", "substantive", "removed"]},
            samples={"section": [
                {"identifier": v.get("identifier"), "name": v.get("name")}
                for v in sections[:3]]},
            notes=notes)

    def _versions(self) -> list[dict]:
        payload = self._fetch_json(
            f"/api/versioner/v1/versions/title-{self.title}.json",
            {"part": self.part}, fixture="versions.json")
        # VERIFY: amendment_date on every entry, or only substantive ones.
        return payload.get("content_versions", [])

    def sync(self, since: str | None = None,
             cursor: str | None = None) -> Iterable[SyncPage]:
        """One document per section, with its text.

        Sections are yielded one page at a time with the section number as the
        cursor, so a part with two hundred sections resumes where it stopped
        rather than refetching the whole title.
        """
        date = self._current_date()
        amended = {v.get("identifier"): v.get("amendment_date")
                   for v in self._versions() if v.get("type") == "section"}

        xml = self._fetch(f"/api/versioner/v1/full/{date}/title-{self.title}.xml",
                          {"part": self.part}, fixture="full.xml")
        sections = parse_part(xml)
        if not sections:
            raise ConnectorError(
                f"eCFR returned no sections for {self.title} CFR {self.part}")

        resuming = cursor is not None
        for section in sections:
            if resuming:
                # Skip forward to just past the checkpoint.
                if section.number == cursor:
                    resuming = False
                continue
            updated = amended.get(section.number) or amended.get(f"§ {section.number}")
            if since and updated and updated < since:
                continue
            yield SyncPage([{
                "_type": "knowledge_document",
                "_source_type": "cfr_section",
                "_source_updated_at": updated,
                "source_id": section.number,
                "document_type": "REGULATION",
                "title": f"{self.title} CFR {section.number} — {section.plain_title}",
                # The locator prefix. "42 CFR §482.13(a)" is the citation a
                # surveyor writes; repeating the section number in the prefix
                # would make every locator read it twice.
                "standard": f"{self.title} CFR",
                "text": section.as_markdown(),
                "publisher": "U.S. Government Publishing Office",
                "authority": "FEDERAL",
                "jurisdiction": "United States",
                "version": date,
                "effective_date": updated,
                "reference_url": (f"{BASE}/current/title-{self.title}"
                                  f"/section-{section.number}"),
                "retrieval_date": date,
            }], cursor=section.number, has_more=section is not sections[-1])

    def health_check(self) -> HealthStatus:
        if not self._authenticated:
            return HealthStatus("DISCONNECTED", "Not connected")
        try:
            date = self._current_date()
        except (ConnectorError, TransientError) as e:
            return HealthStatus("ERROR", str(e))
        return HealthStatus("CONNECTED", f"Title {self.title} current as of {date}",
                            latency_ms=self._latency_ms,
                            detail={"title": self.title, "part": self.part,
                                    "verification": self.info.verification.status})

    def disconnect(self) -> None:
        self._authenticated = False
        self.date = None


# --- parsing -----------------------------------------------------------------
#
# Kept as free functions so the harness and the tests can exercise them against
# a response without constructing a connector or touching a network.

SECTION_NUMBER = re.compile(r"(\d+\.\d+)")
DESIGNATOR = re.compile(r"^\(([A-Za-z0-9]+)\)\s*")
# The first designator of each CFR numbering sequence. A designator that
# continues nothing and is not one of these opens no new level.
FIRST_OF_SEQUENCE = {"a", "1", "i", "A", "I"}


class Paragraph:
    __slots__ = ("designator", "text", "level")

    def __init__(self, designator: str, text: str, level: int):
        self.designator, self.text, self.level = designator, text, level


class Section:
    """One CFR section — the unit a citation points at."""

    def __init__(self, number: str, heading: str):
        self.number = number
        self.heading = heading
        self.paragraphs: list[Paragraph] = []

    @property
    def plain_title(self) -> str:
        """The heading without its leading citation — that lives in the title
        already, and repeating it reads as a stutter in every reference."""
        return re.sub(r"^§+\s*\d+\.\d+\s*", "", self.heading).strip() or self.number

    def as_markdown(self) -> str:
        """Render for the ingest pipeline.

        The extractor reads `## §<designator> <title>` headings, so each CFR
        paragraph becomes one entity with its own citable span, locating as
        *42 CFR §482.13(a) Notice of rights* — how the paragraph is actually
        cited. Rendering here rather than teaching the extractor about CFR XML
        keeps format knowledge inside the connector, which is the point of the
        boundary.

        Unlabelled paragraphs are given the section's own citation rather than
        being left as body text. In a Condition of Participation the unlabelled
        opening sentence is usually the condition itself, and leaving it outside
        the heading structure would drop the most important obligation in the
        section.
        """
        lines = [f"# {self.heading}".rstrip(), ""]
        for para in self.paragraphs:
            citation = f"§{self.number}{para.designator}"
            label = _first_sentence(para.text)
            lines.append(f"## {citation} {label}")
            lines.append(para.text)
            lines.append("")
        return "\n".join(lines).strip() + "\n"


def _first_sentence(text: str, limit: int = 80) -> str:
    """A short label for the paragraph, taken from its own words.

    Never generated. A label a model invented would be the one part of a
    citation that no source states, sitting inside the locator every finding
    quotes.
    """
    body = DESIGNATOR.sub("", text).strip()
    stripped = re.sub(r"^(Standard|Standards):\s*", "", body)
    cut = re.split(r"(?<=[.;:])\s", stripped, maxsplit=1)[0]
    return (cut[:limit].rstrip(" .,;:") or "Provision")


def _level_of(designator: str, stack: list[str]) -> int:
    """CFR paragraph depth: (a) → (1) → (i) → (A) → (1) → (i).

    `(i)` is ambiguous — the ninth lowercase letter and the first roman numeral
    — and the rules below resolve it by position rather than by guessing:

    1. If it continues an open level's sequence, it belongs to that level.
       `(i)` after `(h)` continues `(h)`; it does not open a roman sequence.
    2. Otherwise, if it is the first designator of a sequence, it opens a new
       level one deeper. `(i)` after `(1)` opens a roman level.
    3. Otherwise, if it is the same kind as an open level and sorts after it, it
       belongs to that level. CFR parts skip and reserve designators, so `(e)`
       arriving after `(b)(2)` is `(e)`, not `(b)(e)`.
    4. Otherwise it is a sibling of the deepest level — a numbering style this
       parser does not model, kept rather than dropped.

    VERIFY: that eCFR carries the designator in the paragraph text at all. If it
    instead nests paragraphs structurally, this function is unnecessary and the
    parser below is wrong in a way fixtures cannot reveal.
    """
    for depth in range(len(stack) - 1, -1, -1):
        if _follows(stack[depth], designator):
            return depth
    if designator in FIRST_OF_SEQUENCE:
        return len(stack)
    for depth in range(len(stack) - 1, -1, -1):
        if _sorts_after(stack[depth], designator):
            return depth
    return max(len(stack) - 1, 0)


ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman(token: str) -> int | None:
    total = previous = 0
    for char in reversed(token.lower()):
        value = ROMAN_VALUES.get(char)
        if value is None:
            return None
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


def _ordinals(designator: str) -> set[tuple[str, int]]:
    """Every sequence a designator could belong to, with its position in each.

    A set rather than a single value because `(i)` genuinely belongs to two:
    the lowercase alphabet at position 9, and the roman numerals at position 1.
    Carrying both lets the rules above pick by context instead of by guess.
    """
    out: set[tuple[str, int]] = set()
    if designator.isdigit():
        return {("digit", int(designator))}
    if len(designator) == 1 and designator.isalpha():
        kind = "alpha_upper" if designator.isupper() else "alpha_lower"
        out.add((kind, ord(designator.lower()) - ord("a") + 1))
    value = _roman(designator)
    if value is not None:
        out.add(("roman_upper" if designator.isupper() else "roman_lower", value))
    return out


def _follows(previous: str, current: str) -> bool:
    """Is `current` the next designator after `previous` in the same sequence?"""
    return any(kind == other_kind and value == other_value + 1
               for kind, value in _ordinals(current)
               for other_kind, other_value in _ordinals(previous))


def _sorts_after(previous: str, current: str) -> bool:
    """Same sequence, later position — a designator arriving after a gap."""
    return any(kind == other_kind and value > other_value
               for kind, value in _ordinals(current)
               for other_kind, other_value in _ordinals(previous))


def parse_part(xml: bytes) -> list[Section]:
    """ECFR XML → sections with their paragraphs.

    Tolerant by design: eCFR XML carries decades of typesetting history, and a
    parser that raises on an unexpected element would fail on one section in a
    part of two hundred and lose the other one hundred and ninety-nine.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ConnectorError(f"eCFR returned unreadable XML: {e}") from None

    sections: list[Section] = []
    for node in root.iter("DIV8"):
        # VERIFY: whether @N is '482.13' or '§ 482.13'. Both are accepted here
        # rather than assuming, and the heading is used as a fallback.
        raw = (node.get("N") or "").strip()
        match = SECTION_NUMBER.search(raw)
        head = _text_of(node.find("HEAD"))
        if not match:
            match = SECTION_NUMBER.search(head)
        if not match:
            continue
        section = Section(match.group(1), head)
        stack: list[str] = []
        for para in node.iter("P"):
            text = _text_of(para)
            if not text:
                continue
            designator = DESIGNATOR.match(text)
            if not designator:
                section.paragraphs.append(Paragraph("", text, 0))
                continue
            token = designator.group(1)
            level = _level_of(token, stack)
            stack = stack[:level] + [token]
            section.paragraphs.append(
                Paragraph("".join(f"({t})" for t in stack), text, level))
        if section.paragraphs:
            sections.append(section)
    return sections


def _text_of(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def register_ecfr() -> None:
    registry.register(EcfrConnector.info, EcfrConnector)
