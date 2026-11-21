# MTG Arena integration for Urza (macOS)

Urza now supports two local, independent sources:

1. [bynov/mtga-collection-export](https://github.com/bynov/mtga-collection-export) for validated card ownership, counts, and printing IDs.
2. [Tapps Tracker](https://github.com/pattont/MTGA-Tapps) for ranked Competitive Brawl results.

**No Arena passwords, account tokens, remote requests, or automatic memory scans.** Everything here runs on your own machine. The collection bridge is MIT-licensed code; neither third-party project is bundled with Urza.

## Install

In a checkout of this feature branch, with Python >=3.12 and uv installed:

~~~bash
uv sync
uv run urza-arena --help
~~~

To extract a collection on macOS, first install Xcode Command Line Tools and Go 1.26+. Build the MIT-licensed exporter yourself:

~~~bash
xcode-select --install
git clone https://github.com/bynov/mtga-collection-export.git
cd mtga-collection-export
CGO_ENABLED=0 go build -o bin/mtga-collection-export ./cmd/mtga-collection-export
~~~

Launch MTG Arena and open the Collection tab. For stronger validation, enable Detailed Logs (Plugin Support) in Arena settings and restart Arena. Back in the Urza directory:

~~~bash
uv run urza-arena collect \
  --exporter ../mtga-collection-export/bin/mtga-collection-export \
  --out ~/Documents/Urza/Arena/collection.csv \
  --allow-memory-scan
~~~

This explicitly opts into using the upstream utility to take a read-only memory snapshot with LLDB. It pauses Arena briefly. Wizards has not sanctioned memory scanning; it carries risk and should be used only if you accept that risk. Urza keeps the snapshot in a private system temporary directory while the exporter runs. It removes that directory when the exporter finishes or reports an error. The bridge validates the resulting CSV and saves it with owner-only permissions. Never upload a .core dump.

### Use an existing collection CSV instead

If you have already exported a collection, no memory scan is necessary:

~~~bash
uv run urza-arena inspect --collection ~/Documents/Urza/Arena/collection.csv
~~~

The bridge accepts bynov raw CSV as well as Moxfield and ManaBox exports.

## Deck ownership audit

Save an Arena-format import list to a text file, then:

~~~bash
uv run urza-arena audit \
  --collection ~/Documents/Urza/Arena/collection.csv \
  --deck ~/Documents/Urza/Arena/my-deck.txt
~~~

It includes the commander and handles unlimited standard basic lands. It identifies unowned cards and exact missing quantities. **This is not yet a legality validator**: card availability on Arena, Competitive Brawl bans, Alchemy rebalances, and color identity require another verified data source. Do not craft wildcards based solely on an ownership audit.

## Tapps Tracker ranked report

Install and start Tapps Tracker. Its local dashboard normally runs at http://127.0.0.1:8765, with the read-only endpoint GET /api/games.

~~~bash
uv run urza-arena tapps
uv run urza-arena tapps --deck 'Sarek Competitive Brawl'
~~~

Urza only requests 127.0.0.1 and returns aggregated ranked Brawl statistics. No opponent usernames, raw match IDs, or full match records are returned or synchronized. Urza does not install or launch Tapps.

## MCP tools

When Urza is running as an MCP server on the Mac:

- **urza_arena_import_collection(csv_path)** loads a validated local collection into Urza's collection-aware brewing tools.
- **urza_arena_collect_and_import(confirm_memory_scan=True, exporter=..., output_csv=...)** expressly authorizes a local collection scan; it never runs automatically.
- **urza_arena_audit_deck(arena_deck_text)** compares a Brawl list with the loaded collection.
- **urza_arena_ranked_report(port=8765, deck_name="")** summarizes recorded ranked Competitive Brawl results from Tapps.

This ChatGPT conversation does not automatically connect to the Mac's local files or localhost. Upload the collection CSV here or place a sanitized copy in connected Google Drive for chat analysis.

## Development

~~~bash
uv run pytest tests/test_arena_bridge.py
~~~

The bridge can be tested without Arena, macOS, or Tapps. The tests cover import validation, Arena card/quantity parsing, deck deficits, standard basic lands, explicit consent, atomic CSV save, and ranked-match aggregation.

**Not yet implemented:** background collection refresh, Google Drive sync, in-chat local connector installation, verified Arena legality, or wildcard cost estimation.
