"""Collection ingest + owned-filter for Urza."""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel

from urza.server import mcp

FORMATS = ("moxfield", "archidekt", "manabox", "auto")


class CollectionCard(BaseModel):
    name: str
    quantity: int = 1
    set_code: str | None = None
    collector_number: str | None = None
    foil: bool = False


class CollectionStats(BaseModel):
    unique_cards: int
    total_cards: int
    loaded_from: str | None
    format_detected: str | None
    top_sets: dict[str, int]


class OwnedFilterResult(BaseModel):
    owned: dict[str, int]
    missing: list[str]
    total_queried: int


_COLLECTION: dict[str, list[CollectionCard]] = defaultdict(list)
_LOADED_FROM: str | None = None
_FORMAT: str | None = None


def _normalize_name(raw: str) -> str:
    # DFCs/MDFCs: sites disagree on "Front // Back" — match on front face
    front = raw.split("//", 1)[0].strip()
    return front.lower()


def _detect_format(headers: list[str]) -> str:
    h = {col.strip() for col in headers}
    if "ManaBox ID" in h or "Scryfall ID" in h:
        return "manabox"
    if "Edition" in h and "Count" in h:
        return "moxfield"
    if "Finish" in h and ("Quantity" in h or "Count" in h):
        return "archidekt"
    raise ValueError(f"Unrecognized CSV format. Headers: {sorted(h)}")


def _parse_row(row: dict[str, str], fmt: str) -> CollectionCard | None:
    if fmt == "moxfield":
        name = row.get("Name", "").strip()
        qty_raw = row.get("Count", "0")
        set_code = (row.get("Edition") or "").strip() or None
        num = (row.get("Collector Number") or "").strip() or None
        foil = (row.get("Foil") or "").strip().lower() in {"foil", "etched", "true", "1", "yes"}
    elif fmt == "archidekt":
        name = row.get("Name", "").strip()
        qty_raw = row.get("Quantity") or row.get("Count") or "0"
        set_code = (row.get("Set Code") or row.get("Edition") or "").strip() or None
        num = (row.get("Collector Number") or "").strip() or None
        foil = (row.get("Finish") or "").strip().lower() in {"foil", "etched", "true"}
    elif fmt == "manabox":
        name = row.get("Name", "").strip()
        qty_raw = row.get("Quantity", "0")
        set_code = (row.get("Set code") or "").strip() or None
        num = (row.get("Collector number") or "").strip() or None
        foil = (row.get("Foil") or "").strip().lower() in {"foil", "etched", "true"}
    else:
        return None
    try:
        qty = int(qty_raw or 0)
    except ValueError:
        return None
    if not name or qty <= 0:
        return None
    return CollectionCard(
        name=name, quantity=qty, set_code=set_code, collector_number=num, foil=foil
    )


def _build_stats() -> CollectionStats:
    set_counts: Counter[str] = Counter()
    total = 0
    for printings in _COLLECTION.values():
        for card in printings:
            total += card.quantity
            if card.set_code:
                set_counts[card.set_code] += card.quantity
    return CollectionStats(
        unique_cards=len(_COLLECTION),
        total_cards=total,
        loaded_from=_LOADED_FROM,
        format_detected=_FORMAT,
        top_sets=dict(set_counts.most_common(10)),
    )


def _ingest_csv(content: str, fmt: str, source: str) -> CollectionStats:
    global _LOADED_FROM, _FORMAT
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")
    detected = _detect_format(list(reader.fieldnames)) if fmt == "auto" else fmt
    _COLLECTION.clear()
    for row in reader:
        card = _parse_row(row, detected)
        if card is None:
            continue
        _COLLECTION[_normalize_name(card.name)].append(card)
    _LOADED_FROM = source
    _FORMAT = detected
    return _build_stats()


def owned_counts(card_names: list[str]) -> OwnedFilterResult:
    owned: dict[str, int] = {}
    missing: list[str] = []
    for name in card_names:
        key = _normalize_name(name)
        if key in _COLLECTION:
            owned[name] = sum(c.quantity for c in _COLLECTION[key])
        else:
            missing.append(name)
    return OwnedFilterResult(owned=owned, missing=missing, total_queried=len(card_names))


@mcp.tool(
    description=(
        "Load an MTG collection from a CSV file. Auto-detects Moxfield, "
        "Archidekt, or ManaBox export formats. Replaces any previously loaded "
        "collection."
    )
)
def urza_collection_load(csv_path: str, format: str = "auto") -> CollectionStats:
    if format not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    path = Path(csv_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    return _ingest_csv(path.read_text(encoding="utf-8"), format, str(path))


@mcp.tool(
    description=(
        "Load an MTG collection by pasting CSV content inline. Use when the "
        "user pastes their export directly into chat. Auto-detects format."
    )
)
def urza_collection_paste(csv_content: str, format: str = "auto") -> CollectionStats:
    if format not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    return _ingest_csv(csv_content, format, "paste")


@mcp.tool(description="Current collection stats: unique cards, total cards, top sets, source.")
def urza_collection_stats() -> CollectionStats:
    return _build_stats()


@mcp.tool(
    description=(
        "Filter a list of card names to only those the user owns. Returns per-"
        "card owned counts and missing cards. Front-face matching for DFCs. "
        "Other Urza brewing tools call this for collection-aware suggestions."
    )
)
def urza_collection_only_owned(card_names: list[str]) -> OwnedFilterResult:
    return owned_counts(card_names)


@mcp.tool(description="Clear the loaded collection.")
def urza_collection_clear() -> dict[str, str]:
    global _LOADED_FROM, _FORMAT
    _COLLECTION.clear()
    _LOADED_FROM = None
    _FORMAT = None
    return {"status": "cleared"}
