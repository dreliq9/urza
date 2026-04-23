# Urza

Commander brewing capapp for Magic: The Gathering — a workflow layer over [j4th/mtg-mcp-server](https://github.com/j4th/mtg-mcp-server) that adds collection-aware suggestions, stateful deck sessions, and pairwise interaction-density scoring.

**What it is:** an MCP server that extends j4th with four new capabilities:

1. **Collection-aware brewing** — ingest a Moxfield, Archidekt, or ManaBox CSV export, then get suggestions that boost cards you already own.
2. **Pairwise interaction density** — score every card pair for provides/needs overlap via Scryfall types + Commander Spellbook combo graph. Tribal decks land around 0.5 per pair, combo decks 1.5+, goodstuff piles ~0.1. Novel signal for brewing.
3. **Stateful Commander deck sessions** — create, add, remove, validate, and export decks across calls. More natural than passing the full list to every stateless tool.
4. **Composite brew orchestrator** — one call chains EDHREC staples + Spellbook combos + your collection + synergy density into a ranked suggestion list with rationale strings.

**What it is not:** a replacement for j4th. Urza mounts j4th underneath and inherits all 69 of its tools. Card search, combo lookup, draft analytics, rules engine, and metagame data all flow through unchanged.

## Quick start

```bash
git clone https://github.com/<your-user>/urza.git
cd urza
uv sync
uv run urza
```

### Claude Desktop / Claude Code config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (Desktop) or `~/.mcp.json` (Code):

```json
{
  "mcpServers": {
    "urza": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/urza", "run", "urza"]
    }
  }
}
```

Restart the client. You should see 88 tools — 69 from j4th + 19 Urza-specific, all prefixed `urza_*`.

## Urza tools

### Collection
| Tool | Purpose |
|------|---------|
| `urza_collection_load` | Load from a CSV file path |
| `urza_collection_paste` | Load from an inline paste |
| `urza_collection_stats` | Unique/total/top sets |
| `urza_collection_only_owned` | Filter a candidate list to owned cards |
| `urza_collection_clear` | Reset |

CSV formats auto-detected: Moxfield, Archidekt, ManaBox. Double-faced cards match on the front face (case-insensitive).

### Sessions
| Tool | Purpose |
|------|---------|
| `urza_session_create` | New Commander session (commander + optional partner/background) |
| `urza_session_list` | Summary of all sessions |
| `urza_session_get` | Full session state |
| `urza_session_add` | Add cards (list of `{name, count}`) |
| `urza_session_remove` | Remove cards |
| `urza_session_delete` | Drop a session |
| `urza_session_validate` | Structural check (total == 100, singleton, basic-land bypass) |
| `urza_session_export` | Emit a Moxfield-format decklist |

v0.1 validation is structural only — full color-identity and format-legality checks are deferred to v0.2.

### Synergy
| Tool | Purpose |
|------|---------|
| `urza_synergy_score` | Pure math on a list of typed `CardSignature` objects |
| `urza_synergy_build_graph` | Build signatures from Scryfall bulk data + Spellbook combos |
| `urza_synergy_analyze` | One-shot: build graph + compute density |

The math: for each card pair, count overlap across three channels — `payoff` (other needs what I provide), `enabler` (I need what other provides), `trait` (shared types/subtypes). Each overlap caps at 1 per pair. Weights: 8 / 8 / 0.5. Ported from the r2-d2 SWU capapp; see `src/urza/synergy.py` for the formula.

### Brew
| Tool | Purpose |
|------|---------|
| `urza_brew_suggest` | Ranked suggestions. Chains EDHREC + Spellbook + collection + synergy. Optional `session_id` to score against an existing deck. |
| `urza_brew_evaluate` | Score a single candidate card against a commander (or session). |

Composite score: `synergy + edhrec_inclusion/5 + edhrec_synergy * 10 + (5 if owned)`. Rationale strings flag which factors dominated.

## Architecture

```
Claude client (Desktop / Code / any MCP client)
        │
        │ stdio
        ▼
┌───────────────────────────────┐
│ Urza (FastMCP, 88 tools)      │
│                               │
│  urza_collection_*            │
│  urza_session_*               │
│  urza_synergy_*               │
│  urza_brew_*                  │
│                               │
│  ┌─────────────────────────┐  │
│  │ mounts j4th/mtg-mcp-    │  │
│  │ server — 69 tools:      │  │
│  │ scryfall_*, spellbook_*,│  │
│  │ edhrec_*, moxfield_*,   │  │
│  │ draft_*, goldfish_*,    │  │
│  │ spicerack_*, bulk_*,    │  │
│  │ plus workflow tools     │  │
│  └─────────────────────────┘  │
└───────────────────────────────┘
        │
        ▼
  Scryfall, Commander Spellbook, EDHREC, 17Lands,
  Moxfield, MTGGoldfish, Spicerack — via j4th
```

Urza depends on `mtg-mcp-server >= 3.0.0` as a library and mounts its FastMCP instance. Upstream fixes in j4th flow to Urza users without a fork.

## Development

```bash
uv sync
uv run pytest          # unit tests
uv run urza            # start stdio server
```

Pure-math tests cover synergy scoring and collection CSV parsing. Graph builder and brew tools are verified by end-to-end live tests against Scryfall + Spellbook + EDHREC (not included in the default test run; run manually).

## Attribution

Urza is built on data from multiple sources, each with their own usage terms:

- **[Scryfall](https://scryfall.com)** — card data via [j4th's Scryfall adapter](https://github.com/j4th/mtg-mcp-server). Don't paywall Scryfall data; attribute back to Scryfall where displayed.
- **[Commander Spellbook](https://commanderspellbook.com)** — combo graph. MIT-licensed backend.
- **[EDHREC](https://edhrec.com)** — commander staples and synergy signals. Undocumented JSON endpoints; cached aggressively and behind a feature flag in j4th.
- **[j4th/mtg-mcp-server](https://github.com/j4th/mtg-mcp-server)** by Justin Forth — the foundation. MIT-licensed. 69 tools across 8 backends.

Urza itself provides the brewing workflow layer (collection, sessions, synergy density, brew orchestration); all underlying data access is j4th's. If you find Urza useful, star j4th too.

## License

MIT. See [LICENSE](LICENSE).
