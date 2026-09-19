"""Tests for the generic pack: manifest shape and Pack-protocol conformance.

``PACK`` isn't checked with ``isinstance`` (``Pack`` is a plain ``Protocol``,
not ``@runtime_checkable``, per ``packs.base``); the real conformance check is
that ``PACK: Pack = GenericPack()`` type-checks under ``mypy --strict``. These
tests exercise the runtime behaviour a fake ``PackSessionContext`` would see.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from packs.base import PackSessionContext
from packs.generic.manifest import MANIFEST
from packs.generic.pack import PACK, GenericPack


def test_manifest_has_no_pack_specific_tools_or_state() -> None:
    assert MANIFEST.id == "generic"
    assert MANIFEST.tool_names == []
    assert MANIFEST.builtin_tools_disabled == []
    assert MANIFEST.kb_seeds == []


def test_manifest_recommended_pipeline_uses_livekit_inference_defaults() -> None:
    pipeline = MANIFEST.recommended_pipeline
    assert pipeline.mode == "cascaded"
    assert pipeline.stt is not None and pipeline.stt.provider_id == "livekit-inference-stt"
    assert pipeline.llm is not None and pipeline.llm.provider_id == "livekit-inference-llm"
    assert pipeline.tts is not None and pipeline.tts.provider_id == "livekit-inference-tts"
    # No credential is required for these slots, so seeding needs no vendor key.
    assert pipeline.stt.credential_id is None
    assert pipeline.llm.credential_id is None
    assert pipeline.tts.credential_id is None


def test_pack_manifest_is_the_module_level_manifest() -> None:
    assert PACK.manifest is MANIFEST


def test_pack_tools_and_tool_meta_are_empty() -> None:
    ctx = cast(PackSessionContext, object())
    assert PACK.tools(ctx) == []
    assert PACK.tool_meta() == []


def test_pack_initial_state_is_empty_dict() -> None:
    ctx = cast(PackSessionContext, object())
    assert PACK.initial_state(ctx) == {}


@pytest.mark.asyncio
async def test_pack_lifecycle_hooks_are_no_ops() -> None:
    """The hooks return ``None`` (statically, via their signature); this just checks they don't raise."""
    pack = GenericPack()
    ctx = cast(PackSessionContext, object())
    await pack.on_session_start(ctx)
    await pack.on_user_turn_completed(ctx, cast(Any, object()), cast(Any, object()))
    await pack.on_agent_turn_completed(ctx, "hello", False)
    await pack.on_session_end(ctx, "caller_hangup")


@pytest.mark.asyncio
async def test_pack_on_ui_action_reports_no_handler() -> None:
    pack = GenericPack()
    ctx = cast(PackSessionContext, object())
    result = await pack.on_ui_action(ctx, "confirm_sketch", {})
    assert result["ok"] is False
    assert "confirm_sketch" in result["error"]
