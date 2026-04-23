"""Pairwise interaction density scoring for Urza.

Pure math. Caller supplies a list of typed card signatures
(provides/needs/traits sets); returns per-card scores and deck density.

Ported from r2-d2 deck_service.py:604-669.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from urza.server import mcp

INTERACTION_PAYOFF_W = 8.0
INTERACTION_ENABLER_W = 8.0
INTERACTION_TRAIT_W = 0.5
INTERACTION_CAP_PER_PAIR = 1


class CardSignature(BaseModel):
    name: str
    provides: set[str] = Field(default_factory=set)
    needs: set[str] = Field(default_factory=set)
    traits: set[str] = Field(default_factory=set)


class CardScore(BaseModel):
    name: str
    score: float
    as_payoff: float
    as_enabler: float
    trait_overlap: float


class DeckDensity(BaseModel):
    interaction_density: float
    interaction_per_pair: float
    top_cards: list[CardScore]
    weakest_cards: list[CardScore]
    weights: dict[str, float]


def score_card(card: CardSignature, others: list[CardSignature]) -> CardScore:
    payoff = enabler = trait = 0.0
    for other in others:
        if other.name == card.name:
            continue
        payoff += INTERACTION_PAYOFF_W * min(
            len(other.needs & card.provides), INTERACTION_CAP_PER_PAIR
        )
        enabler += INTERACTION_ENABLER_W * min(
            len(card.needs & other.provides), INTERACTION_CAP_PER_PAIR
        )
        trait += INTERACTION_TRAIT_W * min(
            len(card.traits & other.traits), INTERACTION_CAP_PER_PAIR
        )
    return CardScore(
        name=card.name,
        score=round(payoff + enabler + trait, 2),
        as_payoff=round(payoff, 2),
        as_enabler=round(enabler, 2),
        trait_overlap=round(trait, 2),
    )


def compute_density(cards: list[CardSignature], top_n: int = 5) -> DeckDensity:
    weights = {
        "payoff": INTERACTION_PAYOFF_W,
        "enabler": INTERACTION_ENABLER_W,
        "trait": INTERACTION_TRAIT_W,
    }
    if not cards:
        return DeckDensity(
            interaction_density=0.0,
            interaction_per_pair=0.0,
            top_cards=[],
            weakest_cards=[],
            weights=weights,
        )
    scores = [score_card(c, cards) for c in cards]
    total = sum(s.score for s in scores)
    density = round(total / len(scores), 2)
    pair_count = max(len(cards) - 1, 1)
    per_pair = round(density / pair_count, 3)
    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    return DeckDensity(
        interaction_density=density,
        interaction_per_pair=per_pair,
        top_cards=ranked[:top_n],
        weakest_cards=ranked[-top_n:][::-1],
        weights=weights,
    )


@mcp.tool(
    description=(
        "Score pairwise interaction density across a set of cards. Each "
        "CardSignature carries typed provides/needs/traits tokens. Per-pair "
        "normalization: tribal/combo decks score ~2-5, goodstuff piles "
        "~0.3-0.8. Pure math — caller supplies the graph. Use "
        "urza_synergy_build_graph to source real data from Scryfall + Spellbook."
    )
)
def urza_synergy_score(cards: list[CardSignature], top_n: int = 5) -> DeckDensity:
    return compute_density(cards, top_n=top_n)
