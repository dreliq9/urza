"""Commander brew orchestrator — chains EDHREC + Spellbook + collection + synergy."""
from __future__ import annotations

from pydantic import BaseModel

from mtg_mcp_server.config import Settings
from mtg_mcp_server.services.edhrec import (
    CommanderNotFoundError,
    EDHRECClient,
    EDHRECError,
)

from urza import collection, sessions
from urza.server import mcp
from urza.synergy import score_card
from urza.synergy_graph import build_graph

_edhrec: EDHRECClient | None = None


async def _get_edhrec() -> EDHRECClient:
    global _edhrec
    if _edhrec is None:
        s = Settings()
        client = EDHRECClient(base_url=s.edhrec_base_url)
        await client.__aenter__()
        _edhrec = client
    return _edhrec


class BrewCandidate(BaseModel):
    name: str
    synergy_score: float
    synergy_payoff: float
    synergy_enabler: float
    synergy_trait: float
    edhrec_inclusion: int = 0
    edhrec_synergy: float = 0.0
    owned: bool | None = None
    owned_count: int = 0
    total_score: float
    rationale: str


class BrewResult(BaseModel):
    commander: str
    session_id: str | None
    deck_size: int
    candidates_considered: int
    candidates: list[BrewCandidate]
    edhrec_available: bool
    collection_loaded: bool


def _compose_total(
    synergy_score: float,
    edhrec_inclusion: int,
    edhrec_synergy: float,
    owned: bool,
) -> tuple[float, str]:
    # EDHREC inclusion 0-100 → /5 so a 50% staple contributes +10 to total,
    # putting it on the same order of magnitude as a combo payoff (8).
    inclusion_weight = edhrec_inclusion / 5.0
    # EDHREC synergy is -1..+1; scale by 10 so it weighs similarly to one
    # pairwise combo hit.
    synergy_weight = edhrec_synergy * 10.0
    ownership_bonus = 5.0 if owned else 0.0
    total = synergy_score + inclusion_weight + synergy_weight + ownership_bonus

    factors: list[str] = []
    if synergy_score >= 10:
        factors.append(f"combo/tribal synergy (+{synergy_score:.1f})")
    elif synergy_score >= 2:
        factors.append(f"moderate synergy (+{synergy_score:.1f})")
    if inclusion_weight >= 10:
        factors.append(f"EDHREC staple ({edhrec_inclusion}%)")
    elif inclusion_weight >= 4:
        factors.append(f"common ({edhrec_inclusion}% of decks)")
    if synergy_weight >= 5:
        factors.append(f"commander-specific ({edhrec_synergy:+.2f})")
    if ownership_bonus:
        factors.append("owned")
    return round(total, 2), ", ".join(factors) if factors else "low signal"


def _gather_deck_context(
    commander: str, session_id: str | None
) -> tuple[list[str], set[str]]:
    deck_names: list[str] = [commander]
    excluded: set[str] = {commander.lower()}
    if session_id:
        session = sessions._require(session_id)
        for candidate in (session.commander, session.partner, session.background):
            if candidate and candidate.lower() not in excluded:
                deck_names.append(candidate)
                excluded.add(candidate.lower())
        for name in session.cards:
            if name.lower() not in excluded:
                deck_names.append(name)
                excluded.add(name.lower())
    return deck_names, excluded


async def _fetch_edhrec_candidates(
    commander: str,
    excluded: set[str],
    category: str | None,
) -> tuple[dict[str, tuple[int, float]], bool]:
    candidates: dict[str, tuple[int, float]] = {}
    try:
        edhrec = await _get_edhrec()
        data = await edhrec.commander_top_cards(commander)
    except (EDHRECError, CommanderNotFoundError, Exception):
        return {}, False
    for cardlist in data.cardlists:
        if category:
            haystack = (cardlist.header + " " + cardlist.tag).lower()
            if category.lower() not in haystack:
                continue
        for card in cardlist.cardviews:
            if card.name.lower() in excluded:
                continue
            if card.name not in candidates:
                candidates[card.name] = (card.inclusion, card.synergy)
    return candidates, True


async def brew_suggest(
    commander: str,
    session_id: str | None = None,
    limit: int = 30,
    category: str | None = None,
    prefer_owned: bool = True,
) -> BrewResult:
    deck_names, excluded = _gather_deck_context(commander, session_id)
    candidate_data, edhrec_available = await _fetch_edhrec_candidates(
        commander, excluded, category
    )
    collection_loaded = bool(collection._COLLECTION)

    if not candidate_data:
        return BrewResult(
            commander=commander,
            session_id=session_id,
            deck_size=len(deck_names),
            candidates_considered=0,
            candidates=[],
            edhrec_available=edhrec_available,
            collection_loaded=collection_loaded,
        )

    candidate_names = list(candidate_data.keys())
    graph = await build_graph(deck_names + candidate_names, commander=commander)
    deck_sigs = [graph[n] for n in deck_names if n in graph]

    owned_lookup: dict[str, int] = {}
    if collection_loaded and prefer_owned:
        owned_lookup = collection.owned_counts(candidate_names).owned

    scored: list[BrewCandidate] = []
    for name in candidate_names:
        if name not in graph:
            continue
        sig = graph[name]
        card_score = score_card(sig, deck_sigs)
        inclusion, edhrec_syn = candidate_data[name]
        is_owned = name in owned_lookup
        total, rationale = _compose_total(
            card_score.score, inclusion, edhrec_syn, is_owned and prefer_owned
        )
        scored.append(
            BrewCandidate(
                name=name,
                synergy_score=card_score.score,
                synergy_payoff=card_score.as_payoff,
                synergy_enabler=card_score.as_enabler,
                synergy_trait=card_score.trait_overlap,
                edhrec_inclusion=inclusion,
                edhrec_synergy=edhrec_syn,
                owned=is_owned if collection_loaded else None,
                owned_count=owned_lookup.get(name, 0),
                total_score=total,
                rationale=rationale,
            )
        )

    scored.sort(key=lambda c: c.total_score, reverse=True)
    return BrewResult(
        commander=commander,
        session_id=session_id,
        deck_size=len(deck_names),
        candidates_considered=len(candidate_names),
        candidates=scored[:limit],
        edhrec_available=edhrec_available,
        collection_loaded=collection_loaded,
    )


async def brew_evaluate(
    commander: str,
    card_name: str,
    session_id: str | None = None,
) -> BrewCandidate | None:
    deck_names, _ = _gather_deck_context(commander, session_id)
    graph = await build_graph(deck_names + [card_name], commander=commander)
    if card_name not in graph:
        return None
    deck_sigs = [graph[n] for n in deck_names if n in graph]
    card_score = score_card(graph[card_name], deck_sigs)

    edhrec_inclusion = 0
    edhrec_syn = 0.0
    try:
        edhrec = await _get_edhrec()
        data = await edhrec.commander_top_cards(commander)
        for cardlist in data.cardlists:
            for card in cardlist.cardviews:
                if card.name.lower() == card_name.lower():
                    edhrec_inclusion = card.inclusion
                    edhrec_syn = card.synergy
                    break
    except Exception:
        pass

    collection_loaded = bool(collection._COLLECTION)
    is_owned = False
    owned_count = 0
    if collection_loaded:
        owned = collection.owned_counts([card_name]).owned
        if card_name in owned:
            is_owned = True
            owned_count = owned[card_name]

    total, rationale = _compose_total(
        card_score.score, edhrec_inclusion, edhrec_syn, is_owned
    )
    return BrewCandidate(
        name=card_name,
        synergy_score=card_score.score,
        synergy_payoff=card_score.as_payoff,
        synergy_enabler=card_score.as_enabler,
        synergy_trait=card_score.trait_overlap,
        edhrec_inclusion=edhrec_inclusion,
        edhrec_synergy=edhrec_syn,
        owned=is_owned if collection_loaded else None,
        owned_count=owned_count,
        total_score=total,
        rationale=rationale,
    )


@mcp.tool(
    description=(
        "Brew suggestions for a Commander deck. Chains EDHREC staples + "
        "Commander Spellbook combos + collection filter + pairwise synergy "
        "density scoring. If session_id is given, suggestions are scored "
        "against the existing deck and cards already in the session are "
        "excluded. If a collection is loaded, owned cards are boosted "
        "(prefer_owned=True). Optional category filters to creatures, "
        "artifacts, enchantments, lands, etc."
    )
)
async def urza_brew_suggest(
    commander: str,
    session_id: str | None = None,
    limit: int = 30,
    category: str | None = None,
    prefer_owned: bool = True,
) -> BrewResult:
    return await brew_suggest(
        commander=commander,
        session_id=session_id,
        limit=limit,
        category=category,
        prefer_owned=prefer_owned,
    )


@mcp.tool(
    description=(
        "Evaluate a single candidate card for a Commander deck. Returns the "
        "card's synergy score against the session (or just the commander if "
        "no session), EDHREC inclusion/synergy, ownership, and composite "
        "total with rationale. Use for individual swap decisions."
    )
)
async def urza_brew_evaluate(
    commander: str,
    card_name: str,
    session_id: str | None = None,
) -> BrewCandidate | None:
    return await brew_evaluate(
        commander=commander, card_name=card_name, session_id=session_id
    )
