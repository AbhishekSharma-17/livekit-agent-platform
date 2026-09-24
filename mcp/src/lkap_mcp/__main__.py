"""``lkap-mcp``: run the LKAP MCP server.

stdio by default (the coding agent spawns this process; ``LKAP_API_URL`` and
``LKAP_API_KEY`` come from its MCP config). ``--http`` is the remote
streamable-HTTP mode, owned by V3-06.
"""

from __future__ import annotations

import argparse
import logging
import sys

import anyio

from lkap_mcp import __version__
from lkap_mcp.log import configure_logging
from lkap_mcp.server import build_server
from lkap_mcp.settings import McpSettings, load_settings

log = logging.getLogger("lkap_mcp")


async def run_stdio(settings: McpSettings) -> None:
    """Serve over stdio until the client disconnects, then run the shutdown hooks."""
    server = build_server(settings)
    try:
        await server.run_stdio_async()
    finally:
        await server.aclose()


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``lkap-mcp`` script."""
    parser = argparse.ArgumentParser(prog="lkap-mcp", description="LKAP MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP (remote mode, V3-06)")
    parser.add_argument("--version", action="version", version=f"lkap-mcp {__version__}")
    args = parser.parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level)
    if args.http:
        sys.stderr.write("lkap-mcp: --http (remote mode) is not available in this build yet\n")
        return 2
    if settings.api_key is None:
        sys.stderr.write("lkap-mcp: LKAP_API_KEY is not set (mint an agent key in the console)\n")
        return 2
    log.info("lkap_mcp_starting", extra={"api_url": settings.api_url, "transport": "stdio"})
    anyio.run(run_stdio, settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
