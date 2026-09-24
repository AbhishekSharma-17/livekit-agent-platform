"""The stdio transport end to end: spawn ``python -m lkap_mcp`` and talk MCP over its pipes.

The api url is a closed loopback port, so the process never reaches a real api:
the identity lookup fails, only the key-independent tools are listed, and
``me`` relays ``api_unreachable``. Logs go to stderr, never onto the stdio wire.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


async def test_stdio_server_lists_public_tools_and_relays_an_unreachable_api(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "LKAP_API_URL": f"http://127.0.0.1:{_closed_port()}",
        "LKAP_API_KEY": "lkap_stdio_test_key_0123456789",
        "LKAP_MCP_LOG_LEVEL": "DEBUG",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "lkap_mcp"], env=env, cwd=str(tmp_path)
    )

    with (tmp_path / "stderr.log").open("w") as errlog:
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write, client_info=Implementation(name="stdio-test", version="0")) as session,
        ):
            init = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            me = await session.call_tool("me", {})

    assert init.serverInfo.name == "lkap"
    assert {"lkap_guide", "me", "api_request"} <= tools and "agent_create" not in tools
    assert me.structuredContent is not None
    assert me.structuredContent["error"]["code"] == "api_unreachable"
    assert "lkap_stdio_test_key_0123456789" not in (tmp_path / "stderr.log").read_text()


def test_main_without_api_key_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_mcp.__main__ import main

    monkeypatch.delenv("LKAP_API_KEY", raising=False)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        assert main([]) == 2
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
