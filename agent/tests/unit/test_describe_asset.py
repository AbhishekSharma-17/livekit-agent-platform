"""V5-19: `describe_asset` and `vision.describe_image` with a fake vision LLM (no network).

A fixture image (a real PNG drawn with Pillow), a scripted `FakeLLM`, the real
`UiChannel` holding the asset, the schema check and its one repair round, the
registration rules, and that no extracted value reaches a log line.
"""

from __future__ import annotations

import io
import json
from typing import Any, cast

import pytest
from fakes.fake_ctx import FakeLogger, FakePackSessionContext, default_agent_config
from fakes.fake_llm import FakeLLM
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit.agents import ToolError
from lkap_contracts.agent_config import (
    CapabilitiesConfig,
    PanelLayout,
    PipelineConfig,
    ProviderRef,
)
from lkap_contracts.providers import ModelCapabilities
from lkap_contracts.ui_protocol import BlockSpec
from PIL import Image
from test_upload import _run_ctx

from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin.describe_asset import UNTRUSTED_NOTE, build_describe_asset_tool
from lkap_agent.ui.channel import UiChannel
from lkap_agent.vision import ID_DOCUMENT_FIELDS, ExtractField, VisionAnswerError, describe_image, task_schema

ID_ANSWER = {
    "document_type": "driving licence",
    "full_name": "Ada Example",
    "date_of_birth": "1990-01-31",
    "document_number": "D1234567",
    "issuing_authority": "Example Motor Vehicles",
    "issuing_country": "Examplestan",
    "issue_date": "2020-05-01",
    "expiry_date": "2030-05-01",
    "address": None,
}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 40), (200, 180, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Session:
    """The slice of `AgentSession` the tool reads: its LLM."""

    def __init__(self, model: Any) -> None:
        self.llm = model


def _ctx(
    replies: list[str],
    *,
    mode: Any = "cascaded",
    blocks: list[BlockSpec] | None = None,
    capabilities: CapabilitiesConfig | None = None,
    vision: bool | None = None,
) -> tuple[FakePackSessionContext, UiChannel, FakeLLM]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    ui = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    specs = blocks if blocks is not None else [BlockSpec(id="docs", type="upload")]
    ui.init_blocks(specs)
    model = FakeLLM(replies)
    pipeline = PipelineConfig(
        mode="cascaded", llm=ProviderRef(provider_id="openrouter", model="some/vision-model")
    )
    config = default_agent_config(
        panel=PanelLayout(blocks=specs), capabilities=capabilities or CapabilitiesConfig(), pipeline=pipeline
    )
    ctx = FakePackSessionContext(
        pipeline_mode=mode, config=config, ui=cast(Any, ui), room=cast(Any, room), log=FakeLogger()
    )
    ctx.session = cast(Any, _Session(model))
    if vision is not None:
        ctx.llm_capabilities = ModelCapabilities(vision=vision)  # type: ignore[attr-defined]
    return ctx, ui, model


async def _held(ui: UiChannel, data: bytes, mime: str = "image/png") -> str:
    return await ui.push_asset(data, mime, "upload", caption="licence.png")


def _names(ctx: FakePackSessionContext) -> set[str]:
    return {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}


# ================================================================ the tool


async def test_extract_id_returns_the_schema_fields() -> None:
    ctx, ui, model = _ctx([json.dumps(ID_ANSWER)])
    asset_id = await _held(ui, _png())
    tool = build_describe_asset_tool(ctx)
    result = json.loads(await tool(context=_run_ctx(), asset_id=asset_id, task="extract_id"))
    assert result["result"] == ID_ANSWER
    assert set(result["result"]) == {f.name for f in ID_DOCUMENT_FIELDS}
    assert result["note"] == UNTRUSTED_NOTE
    (call,) = model.calls
    assert call.image_count == 1
    assert "untrusted" in call.prompt and "never an instruction" in call.prompt


async def test_one_malformed_answer_is_repaired() -> None:
    ctx, ui, model = _ctx(["Sure! The licence belongs to Ada.", f"```json\n{json.dumps(ID_ANSWER)}\n```"])
    asset_id = await _held(ui, _png())
    result = json.loads(
        await build_describe_asset_tool(ctx)(context=_run_ctx(), asset_id=asset_id, task="extract_id")
    )
    assert result["result"]["full_name"] == "Ada Example"
    assert len(model.calls) == 2
    assert "did not match the schema" in model.calls[1].prompt


async def test_two_malformed_answers_are_a_tool_error() -> None:
    ctx, ui, model = _ctx(["no", '{"full_name": 42}'])
    asset_id = await _held(ui, _png())
    with pytest.raises(ToolError, match="could not be read"):
        await build_describe_asset_tool(ctx)(context=_run_ctx(), asset_id=asset_id, task="extract_id")
    assert len(model.calls) == 2


async def test_extract_fields_uses_the_models_fields_and_types() -> None:
    ctx, ui, _model = _ctx([json.dumps({"plate": "AB12 CDE", "damage_cm": 14.5, "extra": "ignored"})])
    asset_id = await _held(ui, _png())
    fields = [ExtractField(name="plate"), ExtractField(name="damage_cm", type="number")]
    result = json.loads(
        await build_describe_asset_tool(ctx)(
            context=_run_ctx(), asset_id=asset_id, task="extract_fields", fields=fields
        )
    )
    assert result["result"] == {"plate": "AB12 CDE", "damage_cm": 14.5}


async def test_extract_fields_without_fields_is_a_tool_error() -> None:
    ctx, ui, _model = _ctx(["{}"])
    asset_id = await _held(ui, _png())
    with pytest.raises(ToolError, match="at least one field"):
        await build_describe_asset_tool(ctx)(context=_run_ctx(), asset_id=asset_id, task="extract_fields")


@pytest.mark.parametrize(("asset", "message"), [("nope", "no file"), ("pdf", "not an image")])
async def test_only_images_of_this_session_can_be_read(asset: str, message: str) -> None:
    ctx, ui, model = _ctx(["{}"])
    asset_id = await _held(ui, b"%PDF-1.4\n", "application/pdf") if asset == "pdf" else asset
    with pytest.raises(ToolError, match=message):
        await build_describe_asset_tool(ctx)(context=_run_ctx(), asset_id=asset_id)
    assert model.calls == []


async def test_no_extracted_value_reaches_a_log_line() -> None:
    ctx, ui, _model = _ctx([json.dumps(ID_ANSWER)])
    asset_id = await _held(ui, _png())
    await build_describe_asset_tool(ctx)(context=_run_ctx(), asset_id=asset_id, task="extract_id")
    logged = json.dumps([entry for entry in cast(FakeLogger, ctx.log).entries], default=str)
    assert "Ada Example" not in logged and "D1234567" not in logged
    assert "describe_asset" in logged


# ================================================================ registration


def test_registered_with_a_vision_llm_and_a_picture_source() -> None:
    ctx, _ui, _model = _ctx([])
    assert "describe_asset" in _names(ctx)
    camera, _ui, _model = _ctx([], blocks=[], capabilities=CapabilitiesConfig(camera=True))
    assert "describe_asset" in _names(camera)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "realtime"},  # the realtime model is not an llm.LLM
        {"vision": False},  # a text-only model
        {"blocks": [BlockSpec(id="notes", type="notes")]},  # nowhere a picture could come from
    ],
)
def test_not_registered_without_one(kwargs: dict[str, Any]) -> None:
    ctx, _ui, _model = _ctx([], **kwargs)
    assert "describe_asset" not in _names(ctx)


def test_builtin_disabled_switches_it_off() -> None:
    ctx, _ui, _model = _ctx([])
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=["describe_asset"], http_enabled=False)}
    assert "describe_asset" not in names


# ================================================================ vision.describe_image


async def test_describe_returns_a_description() -> None:
    model = FakeLLM([json.dumps({"description": "A dented car door."})])
    assert await describe_image(model, _png(), "image/png") == {"description": "A dented car door."}


async def test_an_unreadable_image_is_a_vision_error() -> None:
    with pytest.raises(VisionAnswerError):
        await describe_image(FakeLLM(["{}"]), b"\x00\x00\x00\x18ftypheic garbage", "image/heic")


def test_task_schema_refuses_duplicate_and_too_many_fields() -> None:
    with pytest.raises(ValueError, match="unique"):
        task_schema("extract_fields", [ExtractField(name="a"), ExtractField(name="a")])
    with pytest.raises(ValueError, match="at most"):
        task_schema("extract_fields", [ExtractField(name=f"f{i}") for i in range(21)])
    model, schema = task_schema("extract_id")
    assert set(schema["properties"]) == {f.name for f in ID_DOCUMENT_FIELDS}
    assert model.model_validate({}).model_dump() == dict.fromkeys(schema["properties"])
