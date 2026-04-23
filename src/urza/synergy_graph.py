"""Synergy graph adapter — builds CardSignatures from Scryfall bulk + Spellbook."""
from __future__ import annotations

from mtg_mcp_server.config import Settings
from mtg_mcp_server.services.scryfall_bulk import ScryfallBulkClient
from mtg_mcp_server.services.spellbook import SpellbookClient
from mtg_mcp_server.types import Card

from urza.server import mcp
from urza.synergy import CardSignature, DeckDensity, compute_density

_bulk: ScryfallBulkClient | None = None
_spellbook: SpellbookClient | None = None

# Supertypes don't carry synergy signal — strip them from type/subtype extraction.
_SUPERTYPES = {"Basic", "Legendary", "Snow", "World", "Ongoing", "Host", "Elite"}


async def _get_bulk() -> ScryfallBulkClient:
    global _bulk
    if _bulk is None:
        s = Settings()
        client = ScryfallBulkClient(
            base_url=s.scryfall_base_url,
            refresh_hours=s.bulk_data_refresh_hours,
        )
        await client.__aenter__()
        _bulk = client
    return _bulk


async def _get_spellbook() -> SpellbookClient:
    global _spellbook
    if _spellbook is None:
        s = Settings()
        client = SpellbookClient(base_url=s.spellbook_base_url)
        await client.__aenter__()
        _spellbook = client
    return _spellbook


def _extract_types(type_line: str) -> tuple[list[str], list[str]]:
    # MTG type lines use the em-dash "—"; fall back to "-" defensively.
    if "—" in type_line:
        front, back = type_line.split("—", 1)
    elif " - " in type_line:
        front, back = type_line.split(" - ", 1)
    else:
        front, back = type_line, ""
    types = [t for t in front.strip().split() if t not in _SUPERTYPES]
    subtypes = back.strip().split()
    return types, subtypes


def _signature_from_card(card: Card, combo_ids: list[str]) -> CardSignature:
    types, subtypes = _extract_types(card.type_line)
    provides: set[str] = set()
    needs: set[str] = set()
    traits: set[str] = set()
    for t in types:
        provides.add(f"type:{t}")
        traits.add(f"type:{t}")
    for s in subtypes:
        provides.add(f"subtype:{s}")
        traits.add(f"subtype:{s}")
    for k in card.keywords:
        provides.add(f"keyword:{k.lower()}")
    for cid in combo_ids:
        provides.add(f"combo:{cid}")
        needs.add(f"combo:{cid}")
    return CardSignature(name=card.name, provides=provides, needs=needs, traits=traits)


async def build_graph(
    card_names: list[str],
    commander: str | None = None,
) -> dict[str, CardSignature]:
    bulk = await _get_bulk()
    resolved = await bulk.get_cards(card_names)

    combos_by_card: dict[str, list[str]] = {}
    if commander:
        try:
            spellbook = await _get_spellbook()
            result = await spellbook.find_decklist_combos(
                commanders=[commander],
                decklist=card_names,
            )
            for combo in result.included:
                for combo_card in combo.cards:
                    combos_by_card.setdefault(combo_card.name.lower(), []).append(combo.id)
        except Exception:
            # Spellbook unavailable — degrade to types/keywords-only signatures
            pass

    signatures: dict[str, CardSignature] = {}
    for queried_name, card in resolved.items():
        if card is None:
            signatures[queried_name] = CardSignature(name=queried_name)
            continue
        combo_ids = combos_by_card.get(card.name.lower(), [])
        signatures[card.name] = _signature_from_card(card, combo_ids)
    return signatures


@mcp.tool(
    description=(
        "Build typed CardSignatures for a list of MTG cards using Scryfall bulk "
        "data (types, subtypes, keywords) plus Commander Spellbook combo graph "
        "when a commander is supplied. Output feeds urza_synergy_score. First "
        "call downloads ~30MB of Scryfall bulk data (~10-30s); subsequent calls "
        "are instant."
    )
)
async def urza_synergy_build_graph(
    card_names: list[str],
    commander: str | None = None,
) -> dict[str, CardSignature]:
    return await build_graph(card_names, commander)


@mcp.tool(
    description=(
        "One-shot synergy analysis: fetch real card data + combo graph, then "
        "compute pairwise interaction density. Equivalent to calling "
        "urza_synergy_build_graph followed by urza_synergy_score. Per-pair "
        "density interpretation: goodstuff ~0.1-0.5, tribal ~0.5-1.5, "
        "combo-heavy decks ~1.5+."
    )
)
async def urza_synergy_analyze(
    card_names: list[str],
    commander: str | None = None,
    top_n: int = 5,
) -> DeckDensity:
    graph = await build_graph(card_names, commander)
    return compute_density(list(graph.values()), top_n=top_n)
