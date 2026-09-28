"""V6-12 (D-V6-16): validating a drawing board, and storing its snapshot.

The validators run on in-memory configs; the snapshot goes through the worker's internal
upload route with the local storage backend, like every session file. No network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import inference_config
from connection_fakes import add_agent
from lkap_contracts.agent_config import (
    CapabilitiesConfig,
    PanelLayout,
    PipelineConfig,
    ProviderRef,
    ToolsConfig,
)
from lkap_contracts.ui_protocol import MAX_CANVAS_SNAPSHOT_BYTES, BlockSpec
from test_session_assets import JPEG, PNG, _rows, _upload

from lkap_api.config_service import CANVAS_NEEDS_CASCADED_MESSAGE, ValidationContext, validate
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.panels import DRAWING_ON_PHONE_MESSAGE

SESSION_ID = "c" * 32

BOARD = BlockSpec(id="board", type="canvas", config={"caller_can_draw": True})
CLOSED = BlockSpec(id="closed", type="canvas")
NOTEBOOK = BlockSpec(
    id="book",
    type="notebook",
    config={
        "caller_can_draw": True,
        "sections": [{"id": "notes"}, {"id": "sketch", "kind": "ink", "canvas_block_id": "hosted"}],
    },
)
HOSTED = BlockSpec(id="hosted", type="canvas")
VISION_LLM = ProviderRef(provider_id="livekit-inference-llm", model="google/gemini-3.5-flash")


def _issues(panel: PanelLayout, **kwargs: Any) -> list[tuple[str, str, str]]:
    kwargs.setdefault("pipeline", inference_config().pipeline.model_copy(update={"llm": VISION_LLM}))
    result = validate(ValidationContext(config=inference_config(panel=panel, **kwargs)))
    return [(i.path, i.severity, i.message) for i in result.issues if i.path.startswith("panel.")]


def _block(block_id: str, block_type: str, config: dict[str, Any] | None = None) -> BlockSpec:
    return BlockSpec(id=block_id, type=block_type, config=config or {})  # type: ignore[arg-type]


# ------------------------------------------------------------------ validation


def test_a_board_on_a_vision_model_validates_cleanly() -> None:
    assert _issues(PanelLayout(blocks=[BOARD, CLOSED, NOTEBOOK, HOSTED])) == []


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ({"colour": "red"}, "panel.blocks[0].config.colour"),
        ({"tools": ["crayon"]}, "panel.blocks[0].config.tools[0]"),
        ({"max_strokes": 5000}, "panel.blocks[0].config.max_strokes"),
        ({"background": "asset:abc"}, "panel.blocks[0].config.background"),
    ],
)
def test_a_bad_canvas_config_is_an_error_at_its_key(config: dict[str, Any], path: str) -> None:
    issues = _issues(PanelLayout(blocks=[_block("board", "canvas", config)]))
    assert [(p, sev) for p, sev, _ in issues][:1] == [(path, "error")]


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([NOTEBOOK], "there is no block 'hosted' on this panel"),
        ([NOTEBOOK, _block("hosted", "gallery")], "'hosted' is a gallery block"),
        (
            [NOTEBOOK, HOSTED, _block("tabs", "layout", {"children": [{"block_id": "hosted"}]})],
            "'hosted' is already shown inside a layout block",
        ),
    ],
)
def test_an_ink_section_must_show_one_real_board(blocks: list[BlockSpec], message: str) -> None:
    issues = _issues(PanelLayout(blocks=blocks))
    assert ("panel.blocks[0].config.sections[1].canvas_block_id", "error", message) in [
        (p, sev, m[: len(message)]) for p, sev, m in issues
    ]


def test_only_an_ink_section_names_a_board() -> None:
    notebook = _block("book", "notebook", {"sections": [{"id": "notes", "canvas_block_id": "board"}]})
    issues = _issues(PanelLayout(blocks=[notebook, BOARD]))
    assert issues[0][:2] == ("panel.blocks[0].config.sections[0]", "error")


def test_a_board_the_caller_draws_on_gets_the_phone_tip() -> None:
    panel = PanelLayout(blocks=[BOARD, CLOSED, NOTEBOOK, HOSTED])
    phone = _issues(panel, capabilities=CapabilitiesConfig(dtmf=True))
    assert [p for p, _, m in phone if m == DRAWING_ON_PHONE_MESSAGE] == [
        "panel.blocks[0].config.caller_can_draw",
        "panel.blocks[3].config.caller_can_draw",
    ]


def test_a_text_only_model_gets_the_vision_warning_naming_a_vision_model() -> None:
    panel = PanelLayout(blocks=[BOARD, CLOSED])
    issues = _issues(panel, pipeline=inference_config().pipeline)  # the Cloud default, Gemma: text only
    (issue,) = issues
    assert issue[:2] == ("panel.blocks[0]", "warning")
    assert "cannot see pictures" in issue[2] and "google/gemini-3.5-flash" in issue[2]


def test_a_realtime_pipeline_gets_the_cascaded_warning_for_every_open_board() -> None:
    pipeline = PipelineConfig(mode="realtime", realtime=ProviderRef(provider_id="openai-realtime"))
    issues = _issues(PanelLayout(blocks=[BOARD, NOTEBOOK, HOSTED]), pipeline=pipeline)
    boards = [(p, m) for p, _, m in issues if m == CANVAS_NEEDS_CASCADED_MESSAGE]
    assert [p for p, _ in boards] == ["panel.blocks[0]", "panel.blocks[2]"]


def test_no_vision_warning_without_an_open_board_or_with_read_canvas_off() -> None:
    text_only = inference_config().pipeline
    assert _issues(PanelLayout(blocks=[CLOSED]), pipeline=text_only) == []
    off = ToolsConfig(builtin_disabled=["read_canvas"])
    assert _issues(PanelLayout(blocks=[BOARD]), pipeline=text_only, tools=off) == []


# ------------------------------------------------------------------ the snapshot


@pytest.fixture
async def canvas_session(database: Database) -> str:
    async with database.session() as session:
        panel = PanelLayout(blocks=[BOARD, CLOSED, NOTEBOOK, HOSTED])
        agent = await add_agent(session, inference_config(panel=panel), connection_id=None)
        session.add(
            SessionRow(
                id=SESSION_ID,
                agent_id=agent.id,
                config_version=1,
                room_name="lkap-canvas",
                participant_identity="caller",
                participant_name="Caller",
                status="active",
                pipeline_mode="cascaded",
            )
        )
    return SESSION_ID


@pytest.mark.parametrize("board", ["board", "hosted"])
async def test_a_drawing_of_an_open_board_is_stored_as_an_ink_frame(
    service_client: httpx.AsyncClient, database: Database, canvas_session: str, board: str
) -> None:
    meta = {"block_id": board, "source": "ink"}
    response = await _upload(service_client, PNG, kind="frame", meta=meta, session_id=canvas_session)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "frame" and body["meta"] == meta and body["mime"] == "image/png"
    assert len(await _rows(database)) == 1


@pytest.mark.parametrize(
    ("data", "meta", "status"),
    [
        (PNG, {"block_id": "closed", "source": "ink"}, 422),  # the caller may not draw there
        (PNG, {"block_id": "book", "source": "ink"}, 422),  # a notebook is no board
        (PNG, {"block_id": "nowhere", "source": "ink"}, 422),
        (PNG, {"source": "ink"}, 422),  # no board named
        (JPEG, {"block_id": "board", "source": "ink"}, 415),  # a drawing is a PNG
        (
            b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_CANVAS_SNAPSHOT_BYTES,
            {"block_id": "board", "source": "ink"},
            413,
        ),
    ],
)
async def test_a_drawing_that_does_not_fit_is_refused(
    service_client: httpx.AsyncClient,
    database: Database,
    canvas_session: str,
    data: bytes,
    meta: dict[str, str],
    status: int,
) -> None:
    response = await _upload(service_client, data, kind="frame", meta=meta, session_id=canvas_session)
    assert response.status_code == status, response.text
    assert await _rows(database) == []


async def test_a_pinned_frame_is_still_stored_without_a_board(
    service_client: httpx.AsyncClient, canvas_session: str
) -> None:
    response = await _upload(
        service_client, JPEG, kind="frame", meta={"source": "camera"}, session_id=canvas_session
    )
    assert response.status_code == 201, response.text
