"""Pure-math tests for urza.collection. No network required."""
from __future__ import annotations

import pytest

from urza import collection


MOXFIELD_CSV = """Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price
4,0,Sol Ring,cmm,NM,en,,,,101,"",""
1,0,Mana Crypt,2xm,NM,en,Foil,,,233,"",""
3,0,Counterspell,cmr,NM,en,,,,242,"",""
2,0,Fable of the Mirror-Breaker // Reflection of Kiki-Jiki,neo,NM,en,,,,141,"",""
"""

ARCHIDEKT_CSV = """Quantity,Name,Finish,Condition,Language,Set Code,Collector Number
2,Sol Ring,Normal,NM,English,cmm,101
1,Mana Crypt,Foil,NM,English,2xm,233
1,Fable of the Mirror-Breaker,Etched,NM,English,neo,141
"""

MANABOX_CSV = """Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID
Sol Ring,cmm,Commander Masters,101,normal,uncommon,3,1,abc-123
Lightning Bolt,sta,Strixhaven Mystical Archive,42,foil,uncommon,2,2,def-456
"""


@pytest.fixture(autouse=True)
def clear_collection():
    collection._COLLECTION.clear()
    collection._LOADED_FROM = None
    collection._FORMAT = None
    yield
    collection._COLLECTION.clear()


def test_moxfield_format_detected_and_totals_correct():
    stats = collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    assert stats.format_detected == "moxfield"
    assert stats.unique_cards == 4
    assert stats.total_cards == 4 + 1 + 3 + 2  # 10


def test_archidekt_format_detected():
    stats = collection._ingest_csv(ARCHIDEKT_CSV, "auto", "test")
    assert stats.format_detected == "archidekt"
    assert stats.unique_cards == 3
    assert stats.total_cards == 4


def test_manabox_format_detected():
    stats = collection._ingest_csv(MANABOX_CSV, "auto", "test")
    assert stats.format_detected == "manabox"
    assert stats.unique_cards == 2
    assert stats.total_cards == 5


def test_top_sets_tallied_by_quantity():
    stats = collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    # cmm:4, cmr:3, neo:2, 2xm:1
    assert stats.top_sets["cmm"] == 4
    assert stats.top_sets["cmr"] == 3
    assert stats.top_sets["neo"] == 2
    assert stats.top_sets["2xm"] == 1


def test_owned_counts_exact_match():
    collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    result = collection.owned_counts(["Sol Ring", "Mana Crypt"])
    assert result.owned == {"Sol Ring": 4, "Mana Crypt": 1}
    assert result.missing == []


def test_owned_counts_missing_card():
    collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    result = collection.owned_counts(["Sol Ring", "Lightning Bolt"])
    assert result.owned == {"Sol Ring": 4}
    assert result.missing == ["Lightning Bolt"]
    assert result.total_queried == 2


def test_owned_counts_case_insensitive():
    collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    result = collection.owned_counts(["SOL RING", "mana crypt"])
    assert result.owned == {"SOL RING": 4, "mana crypt": 1}


def test_dfc_front_face_matching():
    # CSV has "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"
    # Query with just the front face should match
    collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    result = collection.owned_counts(["Fable of the Mirror-Breaker"])
    assert result.owned == {"Fable of the Mirror-Breaker": 2}


def test_unrecognized_csv_format_raises():
    weird_csv = "Color,Shape,Size\nred,square,large\n"
    with pytest.raises(ValueError, match="Unrecognized CSV format"):
        collection._ingest_csv(weird_csv, "auto", "test")


def test_quantity_aggregates_across_printings():
    # Two Sol Ring entries in different sets — owned_counts sums them
    csv = """Count,Name,Edition,Condition,Language,Foil,Collector Number
2,Sol Ring,cmm,NM,en,,101
1,Sol Ring,c21,NM,en,Foil,259
"""
    collection._ingest_csv(csv, "auto", "test")
    result = collection.owned_counts(["Sol Ring"])
    assert result.owned["Sol Ring"] == 3


def test_zero_quantity_and_blank_names_skipped():
    csv = """Count,Name,Edition,Condition,Language,Foil,Collector Number
0,Sol Ring,cmm,NM,en,,101
,,,,,,
3,Counterspell,cmr,NM,en,,242
"""
    stats = collection._ingest_csv(csv, "auto", "test")
    assert stats.unique_cards == 1
    assert stats.total_cards == 3


def test_clear_resets_state():
    collection._ingest_csv(MOXFIELD_CSV, "auto", "test")
    assert collection._COLLECTION  # not empty
    collection._COLLECTION.clear()
    collection._LOADED_FROM = None
    collection._FORMAT = None
    assert not collection._COLLECTION
    result = collection.owned_counts(["Sol Ring"])
    assert result.owned == {}
    assert result.missing == ["Sol Ring"]
