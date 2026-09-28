"""Upgrade tripwires for the SDK's MCP surface (V5-09, D-V5-11, research-v4 tools §4.3.10).

`lkap_agent.tools.mcp_client.GuardedMCPServerHTTP` overrides a private method of
livekit-agents' `MCPServerHTTP` (`_create_http_client`) because the 1.8.3 constructor
takes no `auth` and hard-codes `follow_redirects=True`. LKAP tracks the SDK rather than
forking it, so these tests pin every fact the override relies on and fail loudly on the
release that changes one.

**When one of these fails** (it is not a flaky test; it means the SDK moved):

1. Read the new `livekit/agents/llm/mcp.py` and the livekit-agents release notes.
2. If `MCPServerHTTP.__init__` gained `auth` (and ideally a transport/client factory):
   shrink `GuardedMCPServerHTTP` to the transport override only, pass the V5-16
   bearer through the constructor, and keep `follow_redirects=False`.
3. If livekit-agents now allows `mcp>=2`: move the api's OAuth helpers to mcp 2.x
   (`AuthorizationCodeResult`, the built-in RFC 9207 check, `application_type`); the
   worker and the api may pin different `mcp` versions (they exchange only JSON).
4. If `_create_http_client` was renamed or `client_streams` stopped calling it, the
   guarded transport and the no-redirect client **no longer apply**: fix
   `mcp_client.py` before shipping the upgrade (`test_mcp_guard.py` then proves it).
5. Update the expectations below in the same change, with the new version in the
   message, so the next upgrade trips again.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import typing

from livekit.agents.llm import mcp as lk_mcp
from packaging.requirements import Requirement

SHRINK = (
    "shrink `GuardedMCPServerHTTP` per T §4.3.10 "
    "(research-v4 tools-and-integrations; see this file's docstring)"
)


def test_mcp_server_http_init_has_no_auth_parameter() -> None:
    parameters = inspect.signature(lk_mcp.MCPServerHTTP.__init__).parameters

    assert "auth" not in parameters, f"MCPServerHTTP.__init__ now takes `auth`: {SHRINK}"
    assert "transport" not in parameters, f"MCPServerHTTP.__init__ now takes `transport`: {SHRINK}"
    # The arguments GuardedMCPServerHTTP forwards by name.
    for name in (
        "url",
        "transport_type",
        "allowed_tools",
        "headers",
        "timeout",
        "sse_read_timeout",
        "client_session_timeout_seconds",
        # V5-27 (S5-6): the `<untrusted>` fence of every MCP result goes in through this.
        "tool_result_resolver",
    ):
        assert name in parameters, f"MCPServerHTTP.__init__ lost `{name}`: {SHRINK}"


def test_create_http_client_is_the_seam_the_override_uses() -> None:
    method = getattr(lk_mcp.MCPServerHTTP, "_create_http_client", None)
    assert method is not None, f"MCPServerHTTP._create_http_client is gone: {SHRINK}"
    assert list(inspect.signature(method).parameters) == ["self", "headers", "timeout", "auth"], (
        f"MCPServerHTTP._create_http_client changed its signature: {SHRINK}"
    )
    source = inspect.getsource(lk_mcp.MCPServerHTTP.client_streams)
    assert "self._create_http_client()" in source, (
        f"client_streams no longer builds the streamable-HTTP client through _create_http_client: {SHRINK}"
    )
    assert "httpx_client_factory=self._create_http_client" in source, (
        f"client_streams no longer hands _create_http_client to the SSE client: {SHRINK}"
    )


def test_livekit_agents_still_pins_mcp_below_2() -> None:
    requirements = [
        Requirement(raw)
        for raw in importlib.metadata.requires("livekit-agents") or []
        if Requirement(raw).name == "mcp"
    ]

    assert requirements, "livekit-agents no longer declares an `mcp` requirement: re-check the MCP extra"
    for requirement in requirements:
        assert not requirement.specifier.contains("2.0.0"), (
            f"livekit-agents now allows mcp 2.x ({requirement.specifier}): {SHRINK}"
        )
    installed = importlib.metadata.version("mcp")
    assert int(installed.split(".")[0]) < 2, f"mcp {installed} is installed: {SHRINK}"


def test_mcp_toolset_surface_used_by_build_mcp_toolsets() -> None:
    parameters = inspect.signature(lk_mcp.MCPToolset.__init__).parameters
    for name in ("id", "mcp_server", "tool_options"):
        assert name in parameters, f"MCPToolset.__init__ lost `{name}`: update declarative.build_mcp_toolsets"
    keys = set(typing.get_type_hints(lk_mcp.MCPToolOptions))
    assert {"flags", "on_duplicate", "duplicate_scope", "report_progress"} <= keys, (
        f"MCPToolOptions keys changed ({sorted(keys)}): update declarative.build_mcp_toolsets"
    )


def test_the_pinned_livekit_agents_version_is_the_one_verified() -> None:
    """The facts above were read from 1.8.3; a new version means re-reading them (step 1)."""
    version = importlib.metadata.version("livekit-agents")

    assert version == "1.8.3", f"livekit-agents {version}: re-verify mcp_client.py, then bump this test"


# ------------------------------------------------------------------ speech (V6-02)
#: What V6-02 relies on from livekit-agents 1.8.3. When one fails, re-read the plugin source,
#: then fix the registry entry (`contracts/src/lkap_contracts/providers.py`) or the worker hunk
#: named in the message, and update the expectation here.
SPEECH_FIX = "re-read the 1.8.3 plugin source and update the V6-02 registry entry / worker hunk"


def test_stt_turn_detection_is_a_turn_detection_mode() -> None:
    """`session_builder.STT_TURN_DETECTION` (Deepgram Flux ends the turn itself)."""
    from livekit.agents.voice import turn  # noqa: PLC0415

    from lkap_agent.session_builder import STT_TURN_DETECTION  # noqa: PLC0415

    modes: set[str] = set()
    for member in typing.get_args(turn.TurnDetectionMode):
        modes |= {arg for arg in typing.get_args(member) if isinstance(arg, str)}
    assert STT_TURN_DETECTION in modes, f"TurnDetectionMode lost 'stt' ({sorted(modes)}): {SPEECH_FIX}"


def test_the_speech_kwargs_the_registry_passes_still_exist() -> None:
    from livekit.plugins import deepgram, elevenlabs, openai  # noqa: PLC0415

    expected: dict[type, set[str]] = {
        deepgram.STTv2: {"model", "api_key", "eot_threshold", "eager_eot_threshold", "eot_timeout_ms"},
        openai.STT: {"use_realtime", "model", "language", "detect_language"},
        elevenlabs.TTS: {"encoding", "voice_id", "model"},
    }
    for cls, names in expected.items():
        parameters = set(inspect.signature(cls.__init__).parameters)
        missing = names - parameters
        assert not missing, f"{cls.__qualname__}.__init__ lost {sorted(missing)}: {SPEECH_FIX}"


def test_the_streaming_defaults_the_registry_records_still_hold() -> None:
    """`openai-stt` streams only with `use_realtime`; Flux always streams (placeholder keys, offline)."""
    from livekit.plugins import deepgram, openai  # noqa: PLC0415

    batch = openai.STT(api_key="sk-placeholder", model="gpt-4o-mini-transcribe")
    live = openai.STT(api_key="sk-placeholder", model="gpt-4o-mini-transcribe", use_realtime=True)
    flux = deepgram.STTv2(api_key="dg-placeholder-key")
    assert batch.capabilities.streaming is False, SPEECH_FIX
    assert live.capabilities.streaming is True, SPEECH_FIX
    assert flux.capabilities.streaming is True, SPEECH_FIX
