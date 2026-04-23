"""Pure-math tests for urza.synergy. No network required."""
from __future__ import annotations

from urza.synergy import (
    INTERACTION_ENABLER_W,
    INTERACTION_PAYOFF_W,
    INTERACTION_TRAIT_W,
    CardSignature,
    compute_density,
    score_card,
)


def _goblin(name: str) -> CardSignature:
    return CardSignature(
        name=name,
        provides=set(),
        needs=set(),
        traits={"subtype:Goblin", "type:Creature"},
    )


def _combo_pair() -> tuple[CardSignature, CardSignature]:
    a = CardSignature(
        name="Kiki-Jiki",
        provides={"combo:kiki-zealous"},
        needs={"combo:kiki-zealous"},
        traits={"type:Creature"},
    )
    b = CardSignature(
        name="Zealous Conscripts",
        provides={"combo:kiki-zealous"},
        needs={"combo:kiki-zealous"},
        traits={"type:Creature"},
    )
    return a, b


def test_empty_deck_returns_zero_density():
    result = compute_density([])
    assert result.interaction_density == 0.0
    assert result.interaction_per_pair == 0.0
    assert result.top_cards == []


def test_single_card_no_pairs():
    result = compute_density([_goblin("Goblin #1")])
    # One card, no pairs → 0 score
    assert result.interaction_density == 0.0


def test_tribal_per_pair_equals_trait_weight():
    # N goblins sharing one trait → every pair contributes TRAIT_W × 1
    # per-card score = (N-1) × TRAIT_W; density = mean = (N-1) × TRAIT_W
    # per-pair = density / (N-1) = TRAIT_W
    goblins = [_goblin(f"Goblin #{i}") for i in range(5)]
    result = compute_density(goblins)
    assert result.interaction_per_pair == INTERACTION_TRAIT_W


def test_combo_pair_scores_payoff_plus_enabler():
    # A, B each in combo together, share creature trait
    a, b = _combo_pair()
    score_a = score_card(a, [a, b])
    expected_payoff = INTERACTION_PAYOFF_W  # B needs combo, A provides
    expected_enabler = INTERACTION_ENABLER_W  # A needs combo, B provides
    expected_trait = INTERACTION_TRAIT_W  # shared type:Creature
    assert score_a.as_payoff == expected_payoff
    assert score_a.as_enabler == expected_enabler
    assert score_a.trait_overlap == expected_trait
    assert score_a.score == expected_payoff + expected_enabler + expected_trait


def test_unrelated_cards_score_zero():
    a = CardSignature(name="Sol Ring", provides={"type:Artifact"}, traits={"type:Artifact"})
    b = CardSignature(name="Lightning Bolt", provides={"type:Instant"}, traits={"type:Instant"})
    score = score_card(a, [a, b])
    assert score.score == 0.0


def test_cap_per_pair_prevents_runaway_on_massive_overlap():
    # Two cards share 5 provides/needs overlaps but cap limits to 1
    # → score must equal exactly PAYOFF + ENABLER + TRAIT (not 5x that)
    shared = {"combo:1", "combo:2", "combo:3", "combo:4", "combo:5"}
    a = CardSignature(name="A", provides=shared, needs=shared, traits=shared)
    b = CardSignature(name="B", provides=shared, needs=shared, traits=shared)
    score = score_card(a, [a, b])
    expected = INTERACTION_PAYOFF_W + INTERACTION_ENABLER_W + INTERACTION_TRAIT_W
    assert score.score == expected


def test_self_not_counted():
    # A card's signature shouldn't score against itself
    a = CardSignature(name="A", provides={"x"}, needs={"x"}, traits={"x"})
    score_alone = score_card(a, [a])
    assert score_alone.score == 0.0
    # Against a twin with a different name it would score
    twin = CardSignature(name="B", provides={"x"}, needs={"x"}, traits={"x"})
    score_paired = score_card(a, [a, twin])
    assert score_paired.score > 0.0


def test_top_and_weakest_ranking():
    a, b = _combo_pair()
    c = CardSignature(
        name="Sol Ring", provides={"type:Artifact"}, traits={"type:Artifact"}
    )
    result = compute_density([a, b, c], top_n=2)
    assert len(result.top_cards) == 2
    assert result.top_cards[0].score >= result.top_cards[1].score
    # Kiki and Zealous tie at 16.5, Sol Ring at 0 → Sol Ring must be in weakest
    weakest_names = {x.name for x in result.weakest_cards}
    assert "Sol Ring" in weakest_names


def test_density_discriminates_deck_archetypes():
    # Goodstuff pile: zero-trait distinct cards
    goodstuff = [
        CardSignature(name=f"Card{i}", provides={f"t{i}"}, traits={f"t{i}"})
        for i in range(5)
    ]
    # Tribal: all share one trait
    tribal = [_goblin(f"G{i}") for i in range(5)]
    # Combo: everyone in one combo
    combo = [
        CardSignature(
            name=f"C{i}",
            provides={"combo:x"},
            needs={"combo:x"},
            traits={"type:Creature"},
        )
        for i in range(5)
    ]
    g = compute_density(goodstuff).interaction_per_pair
    t = compute_density(tribal).interaction_per_pair
    c = compute_density(combo).interaction_per_pair
    assert g < t < c, f"expected goodstuff < tribal < combo, got {g=} {t=} {c=}"
