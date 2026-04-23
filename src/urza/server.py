"""Urza — Commander-first MTG capapp."""
from __future__ import annotations

from fastmcp import FastMCP
from mtg_mcp_server.server import mcp as mtg_mcp

from urza import __version__

mcp = FastMCP(
    "Urza",
    version=__version__,
    instructions=(
        "Commander-first Magic: The Gathering capapp. Wraps j4th/mtg-mcp-server "
        "with collection-aware brewing, stateful deck sessions, and synergy "
        "scoring. All j4th tools are available; capapp additions are prefixed "
        "urza_*."
    ),
)

mcp.mount(mtg_mcp)


@mcp.tool(description="Return the Urza capapp version.")
def urza_version() -> str:
    return __version__


from urza import brew, collection, sessions, synergy, synergy_graph  # noqa: E402,F401  — registers @mcp.tool decorators


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
