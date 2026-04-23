"""Commander deck sessions for Urza."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from urza.server import mcp

BASIC_LANDS = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest", "snow-covered wastes",
}

# Cards that bypass singleton via rules text "A deck can have any number of
# cards named X". Not exhaustive — full check needs Scryfall (v0.2).
ANY_NUMBER_CARDS = {
    "rat colony", "persistent petitioners", "dragon's approach",
    "shadowborn apostle", "relentless rats", "seven dwarves", "nazgûl",
    "nazgul", "hare apparent", "templar knight", "cid, dragon emperor",
}


class CardEntry(BaseModel):
    name: str
    count: int = 1


class DeckSession(BaseModel):
    session_id: str
    name: str
    format: str = "commander"
    commander: str | None = None
    partner: str | None = None
    background: str | None = None
    cards: dict[str, int] = Field(default_factory=dict)
    notes: str = ""
    created_at: str
    updated_at: str


class SessionSummary(BaseModel):
    session_id: str
    name: str
    commander: str | None
    partner: str | None
    background: str | None
    main_count: int
    total_count: int
    updated_at: str


class ValidationReport(BaseModel):
    session_id: str
    structurally_legal: bool
    total_count: int
    main_count: int
    has_commander: bool
    warnings: list[str]
    errors: list[str]


_SESSIONS: dict[str, DeckSession] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(session_id: str) -> DeckSession:
    if session_id not in _SESSIONS:
        raise ValueError(f"No session with id {session_id!r}")
    return _SESSIONS[session_id]


def _main_count(session: DeckSession) -> int:
    return sum(session.cards.values())


def _total_count(session: DeckSession) -> int:
    extras = sum(1 for x in (session.commander, session.partner, session.background) if x)
    return _main_count(session) + extras


def _summary(session: DeckSession) -> SessionSummary:
    return SessionSummary(
        session_id=session.session_id,
        name=session.name,
        commander=session.commander,
        partner=session.partner,
        background=session.background,
        main_count=_main_count(session),
        total_count=_total_count(session),
        updated_at=session.updated_at,
    )


@mcp.tool(
    description=(
        "Create a Commander deck session. commander required; optional partner "
        "or background (mutually exclusive). Returns the new session."
    )
)
def urza_session_create(
    name: str,
    commander: str,
    partner: str | None = None,
    background: str | None = None,
) -> DeckSession:
    if partner and background:
        raise ValueError("A Commander deck may have a partner OR a background, not both.")
    sid = uuid4().hex[:12]
    now = _now()
    session = DeckSession(
        session_id=sid,
        name=name,
        commander=commander,
        partner=partner,
        background=background,
        created_at=now,
        updated_at=now,
    )
    _SESSIONS[sid] = session
    return session


@mcp.tool(description="List all deck sessions (summary view).")
def urza_session_list() -> list[SessionSummary]:
    return [_summary(s) for s in _SESSIONS.values()]


@mcp.tool(description="Get the full state of a deck session.")
def urza_session_get(session_id: str) -> DeckSession:
    return _require(session_id)


@mcp.tool(
    description=(
        "Add cards to the main deck. cards: list of {name, count}. Soft "
        "singleton — duplicates are not rejected; surfaced as warnings by "
        "urza_session_validate."
    )
)
def urza_session_add(session_id: str, cards: list[CardEntry]) -> DeckSession:
    session = _require(session_id)
    for entry in cards:
        key = entry.name.strip()
        if not key:
            continue
        session.cards[key] = session.cards.get(key, 0) + entry.count
    session.updated_at = _now()
    return session


@mcp.tool(
    description=(
        "Remove cards from the main deck. Subtracts count; entries dropping to "
        "zero are removed. Silently ignores cards that aren't in the session."
    )
)
def urza_session_remove(session_id: str, cards: list[CardEntry]) -> DeckSession:
    session = _require(session_id)
    for entry in cards:
        key = entry.name.strip()
        if key in session.cards:
            remaining = session.cards[key] - entry.count
            if remaining <= 0:
                session.cards.pop(key)
            else:
                session.cards[key] = remaining
    session.updated_at = _now()
    return session


@mcp.tool(description="Delete a deck session.")
def urza_session_delete(session_id: str) -> dict[str, str]:
    _require(session_id)
    del _SESSIONS[session_id]
    return {"status": "deleted", "session_id": session_id}


@mcp.tool(
    description=(
        "Structural validation of a Commander session. Checks total == 100, "
        "commander is set, and soft singleton (warns on non-basic duplicates). "
        "Full rules validation (color identity, set legality) is v0.2."
    )
)
def urza_session_validate(session_id: str) -> ValidationReport:
    session = _require(session_id)
    warnings: list[str] = []
    errors: list[str] = []
    has_commander = bool(session.commander)
    if not has_commander:
        errors.append("No commander set.")
    total = _total_count(session)
    if total != 100:
        errors.append(f"Total count is {total}, expected 100.")
    for name, count in session.cards.items():
        if count > 1:
            norm = name.lower()
            if norm not in BASIC_LANDS and norm not in ANY_NUMBER_CARDS:
                warnings.append(f"Singleton violation: {name} x{count}")
    return ValidationReport(
        session_id=session_id,
        structurally_legal=not errors,
        total_count=total,
        main_count=_main_count(session),
        has_commander=has_commander,
        warnings=warnings,
        errors=errors,
    )


@mcp.tool(
    description=(
        "Export a session as a plain-text decklist. format='moxfield' emits "
        "lines of '{count} {name}' with commander/partner/background tagged."
    )
)
def urza_session_export(session_id: str, format: str = "moxfield") -> str:
    if format != "moxfield":
        raise ValueError("Only 'moxfield' format supported in v0.1.")
    session = _require(session_id)
    lines: list[str] = []
    if session.commander:
        lines.append(f"1 {session.commander}  # Commander")
    if session.partner:
        lines.append(f"1 {session.partner}  # Partner")
    if session.background:
        lines.append(f"1 {session.background}  # Background")
    for name in sorted(session.cards):
        lines.append(f"{session.cards[name]} {name}")
    return "\n".join(lines)
