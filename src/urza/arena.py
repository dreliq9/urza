"""Urza MCP tools for local Arena collection and Competitive Brawl telemetry."""
from __future__ import annotations

from pathlib import Path

from urza import collection
from urza.arena_bridge import (
    audit_deck,
    collection_summary,
    normalize_name,
    parse_deck,
    read_collection,
    run_macos_export,
    tapps_ranked_report,
    to_moxfield_csv,
)
from urza.server import mcp


def _load_into_urza(path: str | Path) -> dict[str, object]:
    cards, fmt = read_collection(path)
    p = Path(path).expanduser().resolve()
    stats = collection._ingest_csv(to_moxfield_csv(cards), "moxfield", str(p))
    return {"status": "loaded", "source_format": fmt, **collection_summary(cards),
            "urza_collection": stats.model_dump()}


@mcp.tool(description=(
    "Import a validated bynov MTG Arena collection CSV (raw, Moxfield, or "
    "ManaBox) into Urza's current collection-aware brewing session. "
    "No credentials or memory access needed; use a CSV you exported locally."
))
def urza_arena_import_collection(csv_path: str) -> dict[str, object]:
    return _load_into_urza(csv_path)


@mcp.tool(description=(
    "On the user's macOS machine only, explicitly authorize a read-only MTG Arena "
    "memory scan using a separately installed bynov exporter. No automatic "
    "execution; confirm_memory_scan must be true. Writes a local private CSV "
    "and loads it into Urza."
))
def urza_arena_collect_and_import(
    confirm_memory_scan: bool = False,
    exporter: str = "mtga-collection-export",
    output_csv: str = "~/Documents/Urza/Arena/collection.csv",
) -> dict[str, object]:
    _, output = run_macos_export(
        exporter=exporter, output_csv=output_csv,
        confirm_memory_scan=confirm_memory_scan,
    )
    return _load_into_urza(output)


@mcp.tool(description=(
    "Check an Arena-format decklist against the user's currently loaded Urza "
    "collection, including their commander and unlimited regular basic lands. "
    "Reports exact missing card quantities; does not assert Arena availability "
    "or format legality."
))
def urza_arena_audit_deck(arena_deck_text: str) -> dict[str, object]:
    if collection._build_stats().loaded_from is None:
        raise ValueError("Load a collection with urza_arena_import_collection first")
    names = [item.name for item in parse_deck(arena_deck_text)]
    result = collection.owned_counts(names)
    counts = {normalize_name(name): count for name, count in result.owned.items()}
    return audit_deck(arena_deck_text, counts)


@mcp.tool(description=(
    "Read Tapps Tracker's local /api/games endpoint on 127.0.0.1 (port 8765), "
    "and summarize ranked Competitive Brawl win rate, flood/screw flags, "
    "and results per deck. Never returns raw opponent identifiers."
))
def urza_arena_ranked_report(port: int = 8765, deck_name: str = "") -> dict[str, object]:
    return tapps_ranked_report(port=port, deck_name=deck_name)
