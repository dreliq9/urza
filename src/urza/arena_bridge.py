"""Local, read-only MTG Arena collection and Tapps Tracker adapters.

No background services or network access are started at import time. The only
HTTP request this module makes is an opt-in GET to Tapps on 127.0.0.1.
"""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_CSV_BYTES = 32 * 1024 * 1024
MAX_GAMES_BYTES = 32 * 1024 * 1024
BASIC_LANDS = frozenset({"plains", "island", "swamp", "mountain", "forest", "wastes"})
HEADINGS = frozenset({"commander", "deck", "sideboard", "companion", "maybeboard", "about"})
MAIN_SECTIONS = frozenset({"commander", "deck"})
DECK_LINE = re.compile(
    r"^(?P<count>[1-9][0-9]*)\s+(?P<name>.+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,8})\)\s+(?P<number>[A-Za-z0-9*.-]+))?$"
)


@dataclass(frozen=True)
class ArenaCard:
    name: str
    quantity: int
    set_code: str = ""
    collector_number: str = ""
    arena_id: int | None = None


@dataclass(frozen=True)
class DeckCard:
    name: str
    quantity: int
    section: str


def normalize_name(name: str) -> str:
    """Case-insensitive front-face matching for DFCs/MDFCs."""
    return name.split("//", 1)[0].strip().casefold()


def _csv_format(headers: set[str]) -> str:
    if {"count", "name", "set", "collector_number", "arena_id"} <= headers:
        return "arena_raw"
    if {"Name", "Count", "Edition"} <= headers:
        return "moxfield"
    if {"Name", "Quantity", "Set code"} <= headers:
        return "manabox"
    raise ValueError(f"Unrecognized collection CSV headers: {sorted(headers)}")


def read_collection(path: str | Path) -> tuple[list[ArenaCard], str]:
    """Read bynov's raw CSV, or its Moxfield/ManaBox export formats.

    Rejects corrupt inputs instead of silently producing incorrect ownership.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Collection CSV not found: {source}")
    if source.stat().st_size > MAX_CSV_BYTES:
        raise ValueError("Collection CSV is unexpectedly large")
    content = source.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if reader.fieldnames is None:
        raise ValueError("Collection CSV is empty")
    fmt = _csv_format(set(reader.fieldnames))
    cards: list[ArenaCard] = []
    seen_ids: set[int] = set()
    for number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values() if isinstance(value, str)):
            continue
        if None in row:
            raise ValueError(f"Malformed CSV row {number}: extra columns")
        if fmt == "arena_raw":
            name = (row.get("name") or "").strip()
            quantity = (row.get("count") or "").strip()
            set_code = row.get("set") or ""
            collector = row.get("collector_number") or ""
            card_id_raw = (row.get("arena_id") or "").strip()
        elif fmt == "moxfield":
            name = (row.get("Name") or "").strip()
            quantity = (row.get("Count") or "").strip()
            set_code = row.get("Edition") or ""
            collector = row.get("Collector Number") or ""
            card_id_raw = ""
        else:
            name = (row.get("Name") or "").strip()
            quantity = (row.get("Quantity") or "").strip()
            set_code = row.get("Set code") or ""
            collector = row.get("Collector number") or ""
            card_id_raw = ""
        if not name or not quantity.isdecimal():
            raise ValueError(f"Invalid card name or count in CSV row {number}")
        owned = int(quantity)
        if owned > 10000:
            raise ValueError(f"Implausible quantity in CSV row {number}")
        if owned == 0:
            continue
        arena_id = None
        if card_id_raw:
            if not card_id_raw.isdecimal() or int(card_id_raw) <= 0:
                raise ValueError(f"Invalid Arena card ID in CSV row {number}")
            arena_id = int(card_id_raw)
            if arena_id in seen_ids:
                raise ValueError(f"Duplicate Arena card ID {arena_id} in CSV row {number}")
            seen_ids.add(arena_id)
        cards.append(ArenaCard(name, owned, set_code.strip(), collector.strip(), arena_id))
    if not cards:
        raise ValueError("Collection CSV contains no owned cards")
    return cards, fmt


def collection_summary(cards: list[ArenaCard]) -> dict[str, object]:
    unique = {normalize_name(c.name) for c in cards}
    return {
        "printings": len(cards),
        "distinct_names": len(unique),
        "copies": sum(c.quantity for c in cards),
    }


def to_moxfield_csv(cards: list[ArenaCard]) -> str:
    """Normalize to Urza's existing collection loader without copying upstream code."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("Count", "Name", "Edition", "Collector Number"))
    for card in cards:
        writer.writerow((card.quantity, card.name, card.set_code, card.collector_number))
    return output.getvalue()


def parse_deck(deck_text: str) -> list[DeckCard]:
    """Parse Arena's Commander/Deck import text; ignore sideboard entries."""
    section = "deck"
    result: list[DeckCard] = []
    for lineno, raw in enumerate(deck_text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        normalized = line.casefold()
        if normalized in HEADINGS:
            section = normalized
            continue
        if section == "about" and normalized.startswith("name "):
            continue
        match = DECK_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"Invalid Arena deck entry at line {lineno}: {line}")
        if section in MAIN_SECTIONS:
            result.append(DeckCard(match.group("name"), int(match.group("count")), section))
    if not result:
        raise ValueError("No main-deck or commander cards found")
    return result


def audit_deck(deck_text: str, owned: Mapping[str, int]) -> dict[str, object]:
    """Check actual card ownership; does not claim to check format legality."""
    deck = parse_deck(deck_text)
    required: Counter[str] = Counter()
    names: dict[str, str] = {}
    commander_slots = 0
    for card in deck:
        key = normalize_name(card.name)
        required[key] += card.quantity
        names.setdefault(key, card.name)
        if card.section == "commander":
            commander_slots += card.quantity
    missing: list[dict[str, object]] = []
    owned_slots = 0
    for key, quantity in sorted(required.items(), key=lambda x: x[0]):
        count = quantity if key in BASIC_LANDS else max(0, int(owned.get(key, 0)))
        have = min(quantity, count)
        owned_slots += have
        if have < quantity:
            missing.append({"name": names[key], "needed": quantity, "owned": have, "missing": quantity - have})
    return {
        "required_slots": sum(required.values()),
        "commander_slots": commander_slots,
        "owned_slots": owned_slots,
        "missing_slots": sum(int(item["missing"]) for item in missing),
        "missing": missing,
        "is_100_cards": sum(required.values()) == 100,
        "note": "Ownership only; Arena availability, color identity, and banned-list legality are not verified.",
    }


def run_macos_export(
    *, exporter: str, output_csv: str | Path, confirm_memory_scan: bool = False,
    timeout_seconds: int = 180,
) -> tuple[list[ArenaCard], Path]:
    """Run the user's installed bynov exporter only after explicit opt-in.

    The exporter reads MTG Arena memory in an Urza-managed temporary directory.
    """
    if not confirm_memory_scan:
        raise PermissionError("Set confirm_memory_scan=True to authorize a read-only Arena memory scan")
    if platform.system() != "Darwin":
        raise RuntimeError("Live collection export requires macOS with Arena running")
    binary = shutil.which(exporter)
    if not binary or not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"Executable not found: {exporter}")
    destination = Path(output_csv).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".urza-arena-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "arena-collection.csv"
        try:
            # bynov can delete its inputs before processing them. Keep them
            # in a private directory that Urza cleans up after the subprocess.
            with tempfile.TemporaryDirectory(prefix="urza-arena-export-") as scratch:
                subprocess.run(
                    [binary, "--out", str(staging), "--format", "raw", "--keep-core"],
                    env={**os.environ, "TMPDIR": scratch},
                    check=True, capture_output=True, text=True, timeout=timeout_seconds,
                )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Collection exporter failed: {exc.stderr[-700:]}") from exc
        cards, fmt = read_collection(staging)
        if fmt != "arena_raw":
            raise ValueError("Exporter did not produce the expected Arena raw format")
        staging.chmod(0o600)
        os.replace(staging, destination)
    destination.chmod(0o600)
    return cards, destination


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl) -> None:
        return None


def _read_local_tapps_games(port: int) -> list[dict[str, object]]:
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    url = f"http://127.0.0.1:{port}/api/games"
    # Bypass environment proxies and restrict the request to this endpoint.
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=5) as response:
        payload = response.read(MAX_GAMES_BYTES + 1)
    if len(payload) > MAX_GAMES_BYTES:
        raise ValueError("Tapps response exceeds size limit")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("games"), list):
        raise ValueError("Unexpected Tapps /api/games response")
    games = parsed["games"]
    if any(not isinstance(item, dict) for item in games):
        raise ValueError("Unexpected game entry from Tapps")
    return games


def summarize_tapps_games(
    games: list[dict[str, object]], *, ranked_only: bool = True, deck_name: str = "",
) -> dict[str, object]:
    """Aggregate without returning opponent usernames or raw match IDs."""
    selected: list[dict[str, object]] = []
    for game in games:
        fmt = str(game.get("format_label") or game.get("raw_format") or "").casefold()
        if "brawl" not in fmt:
            continue
        if ranked_only and not any(term in fmt for term in ("rank", "competitive")):
            continue
        if deck_name and str(game.get("deck_name") or "").casefold() != deck_name.casefold():
            continue
        selected.append(game)
    played = [x for x in selected if str(x.get("outcome") or "").casefold() in {"win", "loss"}]
    wins = sum(str(x.get("outcome") or "").casefold() == "win" for x in played)
    deck_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for game in played:
        name = str(game.get("deck_name") or "(unknown)")
        deck_counts[name][str(game.get("outcome")).casefold()] += 1
    by_deck = []
    for name, counts in sorted(deck_counts.items(), key=lambda pair: -(pair[1]["win"] + pair[1]["loss"])):
        total = counts["win"] + counts["loss"]
        by_deck.append({
            "deck": name, "games": total, "wins": counts["win"], "losses": counts["loss"],
            "win_rate_pct": round(100 * counts["win"] / total, 1),
        })
    return {
        "queue": "ranked Competitive Brawl" if ranked_only else "all Brawl queues",
        "matches_in_sample": len(selected),
        "decided_games": len(played),
        "wins": wins,
        "losses": len(played) - wins,
        "win_rate_pct": round(100 * wins / len(played), 1) if played else None,
        "mana_flood_flags": sum(x.get("is_flood") is True for x in selected),
        "mana_screw_flags": sum(x.get("is_screw") is True for x in selected),
        "by_deck": by_deck[:20],
        "note": "Based on games tracked by Tapps; excludes games not recorded. No opponent identifiers included.",
    }


def tapps_ranked_report(port: int = 8765, deck_name: str = "") -> dict[str, object]:
    return summarize_tapps_games(_read_local_tapps_games(port), deck_name=deck_name)
