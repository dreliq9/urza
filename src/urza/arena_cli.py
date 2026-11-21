"""Local CLI for Arena data operations; avoids booting the full MCP server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from urza.arena_bridge import (
    audit_deck,
    collection_summary,
    normalize_name,
    read_collection,
    run_macos_export,
    tapps_ranked_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="urza-arena", description="Local MTG Arena bridge for Urza")
    subs = parser.add_subparsers(dest="action", required=True)
    collect = subs.add_parser("collect", help="Run the local macOS collection exporter")
    collect.add_argument("--exporter", default="mtga-collection-export")
    collect.add_argument("--out", default="~/Documents/Urza/Arena/collection.csv")
    collect.add_argument("--allow-memory-scan", action="store_true", help="Explicitly authorize local Arena memory scan")
    inspect = subs.add_parser("inspect", help="Read a preexisting collection CSV")
    inspect.add_argument("--collection", required=True)
    audit = subs.add_parser("audit", help="Compare an Arena deck text file to collection")
    audit.add_argument("--collection", required=True)
    audit.add_argument("--deck", required=True)
    tapps = subs.add_parser("tapps", help="Report on ranked Brawl games in a local Tapps instance")
    tapps.add_argument("--port", type=int, default=8765)
    tapps.add_argument("--deck", default="", help="Exact deck name to filter")
    args = parser.parse_args()
    if args.action == "collect":
        cards, path = run_macos_export(
            exporter=args.exporter, output_csv=args.out,
            confirm_memory_scan=args.allow_memory_scan,
        )
        report: dict[str, object] = {"file": str(path), **collection_summary(cards)}
    elif args.action == "inspect":
        cards, fmt = read_collection(args.collection)
        report = {"format": fmt, **collection_summary(cards)}
    elif args.action == "audit":
        cards, fmt = read_collection(args.collection)
        counts: dict[str, int] = {}
        for card in cards:
            key = normalize_name(card.name)
            counts[key] = counts.get(key, 0) + card.quantity
        text = Path(args.deck).expanduser().read_text(encoding="utf-8-sig")
        report = {"collection_format": fmt, **audit_deck(text, counts)}
    else:
        report = tapps_ranked_report(port=args.port, deck_name=args.deck)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
