"""V5-19: caller files in the worker.

The `lkap.ui.upload` receiver on the **real** `UiChannel` over `FakeRoom` with a
fake api (`FakeAssetApi`), `request_upload`, form `file` fields, the lazy
`open_citation` copy (R-V5-5) and `pin_frame`'s stored frames. No network.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest
import respx
from fakes.fake_ctx import FakeBackgroundRunner, FakeLogger, FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit.agents import RunContext
from lkap_contracts.agent_config import CapabilitiesConfig, PanelLayout
from lkap_contracts.api_models import SessionAssetOut
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    TOPIC_UI_ASSET,
    AgentAction,
    BlockSpec,
    KbCitation,
    UiPatchOp,
)

from lkap_agent.config_client import AssetRejectedError, ConfigClient
from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin.pin_frame import build_pin_frame_tool
from lkap_agent.tools.builtin.request_form import FormField, build_request_form_tool, fields_to_schema
from lkap_agent.tools.builtin.request_upload import NOT_SENT, build_request_upload_tool
from lkap_agent.ui.blocks import open_citation
from lkap_agent.ui.channel import BARGE_IN, UiChannel, rejection_message

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + bytes(range(40))
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(60))
PDF = b"%PDF-1.4\n%%EOF\n"
HTML = b"<html><script>alert(1)</script></html>"
CALLER = "web-ui"


# ================================================================ fakes


@dataclass
class _Posted:
    session_id: str
    data: bytes
    name: str
    mime: str
    kind: str
    meta: dict[str, str]


@dataclass
class FakeAssetApi:
    """The api's session-file routes, in memory."""

    reject_status: int | None = None
    documents: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    posted: list[_Posted] = field(default_factory=list)
    copies: list[str] = field(default_factory=list)
    stored: dict[str, bytes] = field(default_factory=dict)

    def _out(
        self, asset_id: str, name: str, mime: str, data: bytes, kind: str, meta: dict[str, str]
    ) -> SessionAssetOut:
        self.stored[asset_id] = data
        return SessionAssetOut(
            id=asset_id,
            session_id="sess-1",
            kind=kind,  # type: ignore[arg-type]
            name=name,
            mime=mime,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            meta=meta,
            created_at=dt.datetime(2026, 9, 27, tzinfo=dt.UTC),
        )

    async def post_asset(
        self,
        session_id: str,
        data: bytes,
        *,
        name: str,
        mime: str,
        kind: str = "upload",
        meta: dict[str, str] | None = None,
    ) -> SessionAssetOut:
        self.posted.append(_Posted(session_id, data, name, mime, kind, dict(meta or {})))
        if self.reject_status is not None:
            raise AssetRejectedError("refused", status=self.reject_status)
        return self._out(f"asset{len(self.posted)}", name, mime, data, kind, dict(meta or {}))

    async def asset_from_document(self, session_id: str, document_id: str) -> SessionAssetOut:
        self.copies.append(document_id)
        if document_id not in self.documents:
            raise AssetRejectedError("unknown", status=404)
        mime, data = self.documents[document_id]
        if mime == "text/html":
            raise AssetRejectedError("no preview", status=415, reason="no_preview")
        return self._out(
            f"doc-{document_id}", "policy.pdf", mime, data, "document", {"document_id": document_id}
        )

    async def asset_content(self, session_id: str, asset_id: str) -> bytes:
        if asset_id not in self.stored:
            raise AssetRejectedError("gone", status=404)
        return self.stored[asset_id]


@dataclass
class _Info:
    name: str
    size: int | None
    attributes: dict[str, str]
    mime_type: str = "application/octet-stream"


class FakeReader:
    """Duck-types `rtc.ByteStreamReader`: `.info`, async iteration, `close()`."""

    def __init__(
        self,
        data: bytes,
        *,
        block_id: str = "docs",
        name: str = "photo.png",
        size: int | None = None,
        field: str | None = None,
        chunk: int = 16,
    ) -> None:
        attributes = {"block_id": block_id, "name": name, **({"field": field} if field else {})}
        self.info = _Info(name=name, size=len(data) if size is None else size, attributes=attributes)
        self._chunks = [data[i : i + chunk] for i in range(0, len(data), chunk)]
        self.read_chunks = 0
        self.closed = False

    def __aiter__(self) -> FakeReader:
        return self

    async def __anext__(self) -> bytes:
        if self.read_chunks >= len(self._chunks):
            raise StopAsyncIteration
        self.read_chunks += 1
        return self._chunks[self.read_chunks - 1]

    def close(self) -> None:
        self.closed = True


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _RunCtx:
    function_call: _Call = field(default_factory=_Call)


def _run_ctx() -> RunContext[Any]:
    return cast(RunContext[Any], _RunCtx())


UPLOAD = BlockSpec(
    id="docs", type="upload", config={"accept": ["image/*"], "max_files": 3, "max_bytes": 1000}
)


def _setup(
    blocks: list[BlockSpec] | None = None,
    *,
    api: FakeAssetApi | None = None,
    mode: Any = "cascaded",
    channel_name: str = "web",
    capabilities: CapabilitiesConfig | None = None,
) -> tuple[FakePackSessionContext, UiChannel, FakeRoom, FakeAssetApi]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant(CALLER))
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps({"ok": True, "payload": {}})
    fake_api = api or FakeAssetApi()
    ui = UiChannel(room, "sess-1", asset_api=fake_api)  # type: ignore[arg-type]
    ui.start()
    specs = blocks if blocks is not None else [UPLOAD, BlockSpec(id="photos", type="gallery")]
    ui.init_blocks(specs)
    config = default_agent_config(
        panel=PanelLayout(blocks=specs), capabilities=capabilities or CapabilitiesConfig()
    )
    ctx = FakePackSessionContext(
        pipeline_mode=mode, config=config, ui=cast(Any, ui), room=cast(Any, room), log=FakeLogger()
    )
    ctx.channel = channel_name  # type: ignore[attr-defined]
    events: list[tuple[str, dict[str, Any]]] = []
    ui.bind(record_event=lambda kind, payload: events.append((kind, payload)))
    ctx.events = events
    return ctx, ui, room, fake_api


async def _request(ui: UiChannel, block_id: str = "docs") -> None:
    await ui.patch([UiPatchOp(op="set", path=f"/blocks/{block_id}/status", value="requested")])


async def _submit(room: FakeRoom, block_id: str, values: dict[str, Any]) -> None:
    raw = AgentAction(
        action="block_submit", payload={"block_id": block_id, "values": values}
    ).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)


async def _until(predicate: Any) -> None:
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def _block(ui: UiChannel, block_id: str = "docs") -> dict[str, Any]:
    return cast(dict[str, Any], ui.state.blocks[block_id])


# ================================================================ the receiver


async def test_three_files_within_limits_are_stored_with_matching_sha256_and_patch_files() -> None:
    _ctx, ui, room, api = _setup()
    await _request(ui)
    files = [PNG, JPEG, PNG + b"!"]
    for i, data in enumerate(files):
        stored = await ui.receive_upload(FakeReader(data, name=f"damage-{i}.png"), CALLER)
        assert stored is not None
    block = _block(ui)
    assert [f["sha256"] for f in block["files"]] == [hashlib.sha256(d).hexdigest() for d in files]
    assert [p.meta for p in api.posted] == [{"block_id": "docs"}] * 3
    assert [p.mime for p in api.posted] == ["image/png", "image/jpeg", "image/png"]
    assert block["rejected"] == [] and block["progress"] is None
    # Stored refs, the display path under the stored ids, and the gallery.
    assert [a.asset_id for a in ui.state.assets] == ["asset1", "asset2", "asset3"]
    assert all(a.stored and a.size for a in ui.state.assets)
    streamed = [s for s in room.local_participant.byte_streams if s.topic == TOPIC_UI_ASSET]
    assert [s.attributes["asset_id"] for s in streamed] == ["asset1", "asset2", "asset3"]
    assert b"".join(streamed[0].chunks) == PNG
    assert _block(ui, "photos")["asset_ids"] == ["asset1", "asset2", "asset3"]


async def test_a_declared_size_over_max_bytes_is_refused_before_reading_or_any_api_call() -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    reader = FakeReader(PNG, size=5000)
    assert await ui.receive_upload(reader, CALLER) is None
    assert reader.read_chunks == 0 and reader.closed
    assert api.posted == []
    (rejected,) = _block(ui)["rejected"]
    assert rejected["reason"] == "too_large"
    assert rejected["message"] == rejection_message("too_large", max_bytes=1000)


async def test_a_lying_declared_size_is_caught_while_reading() -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    reader = FakeReader(PNG + bytes(2000), size=10)
    assert await ui.receive_upload(reader, CALLER) is None
    assert reader.read_chunks < len(PNG + bytes(2000)) // 16
    assert api.posted == []
    assert _block(ui)["rejected"][0]["reason"] == "too_large"


async def test_a_stream_shorter_than_its_declared_size_is_discarded() -> None:
    """S5-46: a caller cancelling mid-send closes the stream cleanly; the partial file is not kept."""
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    reader = FakeReader(PNG, size=len(PNG) + 100)
    assert await ui.receive_upload(reader, CALLER) is None
    assert api.posted == [] and reader.closed
    assert _block(ui)["files"] == []
    assert _block(ui)["rejected"][0]["reason"] == "failed"
    # Without a declared size (0), today's behaviour stands: the file is stored.
    assert await ui.receive_upload(FakeReader(PNG, size=0), CALLER) is not None
    assert len(api.posted) == 1


@pytest.mark.parametrize(("data", "name"), [(PDF, "claim.pdf"), (HTML, "photo.png"), (b"GIF8", "x.gif")])
async def test_a_type_outside_accept_is_refused_whatever_the_name(data: bytes, name: str) -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    assert await ui.receive_upload(FakeReader(data, name=name), CALLER) is None
    assert api.posted == []
    assert _block(ui)["rejected"][0]["reason"] == "type_not_allowed"


async def test_one_file_too_many_is_refused() -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    for _ in range(3):
        assert await ui.receive_upload(FakeReader(PNG), CALLER) is not None
    assert await ui.receive_upload(FakeReader(PNG), CALLER) is None
    assert len(api.posted) == 3
    assert _block(ui)["rejected"][0]["reason"] == "too_many_files"


async def test_parallel_streams_cannot_slip_past_max_files_together() -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    results = await asyncio.gather(*(ui.receive_upload(FakeReader(PNG), CALLER) for _ in range(5)))
    assert sum(r is not None for r in results) == 3
    assert len(api.posted) == 3 and len(_block(ui)["files"]) == 3


async def test_the_rejection_list_keeps_only_the_newest() -> None:
    _ctx, ui, _room, _api = _setup()
    await _request(ui)
    for i in range(14):
        await ui.receive_upload(FakeReader(HTML, name=f"bad-{i}.png"), CALLER)
    rejected = _block(ui)["rejected"]
    assert len(rejected) == 10 and rejected[-1]["name"] == "bad-13.png"


async def test_a_file_nobody_asked_for_is_refused() -> None:
    _ctx, ui, _room, api = _setup()
    assert _block(ui)["status"] == "idle"
    assert await ui.receive_upload(FakeReader(PNG), CALLER) is None
    assert api.posted == []
    assert _block(ui)["rejected"][0]["reason"] == "not_requested"


async def test_a_stream_from_anyone_but_the_caller_is_dropped() -> None:
    _ctx, ui, _room, api = _setup()
    await _request(ui)
    reader = FakeReader(PNG)
    assert await ui.receive_upload(reader, "some-avatar") is None
    assert reader.closed and api.posted == []
    assert _block(ui)["rejected"] == []


@pytest.mark.parametrize(("status", "reason"), [(415, "type_not_allowed"), (413, "too_large"), (0, "failed")])
async def test_an_api_refusal_shows_as_a_rejection(status: int, reason: str) -> None:
    _ctx, ui, _room, _api = _setup(api=FakeAssetApi(reject_status=status))
    await _request(ui)
    assert await ui.receive_upload(FakeReader(PNG), CALLER) is None
    assert _block(ui)["rejected"][0]["reason"] == reason
    assert ui.state.assets == []


async def test_events_carry_no_filename() -> None:
    ctx, ui, _room, _api = _setup()
    await _request(ui)
    await ui.receive_upload(FakeReader(PNG, name="jane-doe-licence.png"), CALLER)
    await ui.receive_upload(FakeReader(HTML, name="jane-doe-notes.png"), CALLER)
    assert "jane-doe" not in json.dumps(ctx.events)
    ops = [p["op"] for kind, p in ctx.events if kind == "block_update"]
    assert "file_received" in ops and "file_rejected" in ops


async def test_a_browser_answer_never_rewrites_the_worker_owned_files() -> None:
    _ctx, ui, room, _api = _setup()
    await _request(ui)
    await ui.receive_upload(FakeReader(PNG), CALLER)
    spoofed = [{"asset_id": "evil", "name": "x", "mime": "image/png", "size": 1, "sha256": "0" * 64}]
    await _submit(room, "docs", {"files": spoofed})
    assert [f["asset_id"] for f in _block(ui)["files"]] == ["asset1"]
    assert _block(ui)["status"] == "submitted"


def test_the_upload_handler_is_registered_when_the_room_supports_it() -> None:
    registered: dict[str, Any] = {}

    class _Room(FakeRoom):
        def register_byte_stream_handler(self, topic: str, handler: Any) -> None:
            registered[topic] = handler

        def unregister_byte_stream_handler(self, topic: str) -> None:
            registered.pop(topic, None)

    ui = UiChannel(_Room(), "sess-1")  # type: ignore[arg-type]
    ui.start()
    assert "lkap.ui.upload" in registered
    ui.close()
    assert registered == {}


# ================================================================ request_upload


async def test_request_upload_resolves_with_the_stored_files() -> None:
    ctx, ui, room, _api = _setup()
    task = asyncio.create_task(
        build_request_upload_tool(ctx)(context=_run_ctx(), prompt="A photo of the damage")
    )
    await _until(lambda: _block(ui)["status"] == "requested")
    assert _block(ui)["prompt"] == "A photo of the damage"
    await ui.receive_upload(FakeReader(PNG, name="damage.png"), CALLER)
    await _submit(room, "docs", {"files": ["asset1"]})
    result = json.loads(await task)
    assert result == {
        "files": [{"asset_id": "asset1", "name": "damage.png", "mime": "image/png", "size": len(PNG)}]
    }


async def test_request_upload_returns_not_sent_on_cancel_and_on_barge_in() -> None:
    ctx, ui, room, _api = _setup()
    tool = build_request_upload_tool(ctx)
    task = asyncio.create_task(tool(context=_run_ctx(), prompt="Your licence"))
    await _until(lambda: _block(ui)["status"] == "requested")
    raw = AgentAction(
        action="block_submit", payload={"block_id": "docs", "cancelled": True}
    ).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)
    assert await task == NOT_SENT
    task = asyncio.create_task(tool(context=_run_ctx(), prompt="Your licence"))
    await _until(lambda: _block(ui)["status"] == "requested")
    assert ui.cancel_pending(BARGE_IN, methods=["request"]) == ["docs"]
    assert await task == NOT_SENT
    await _until(lambda: _block(ui)["status"] == "cancelled")


async def test_a_new_request_clears_the_last_ones_files() -> None:
    ctx, ui, room, _api = _setup()
    await _request(ui)
    await ui.receive_upload(FakeReader(PNG), CALLER)
    task = asyncio.create_task(build_request_upload_tool(ctx)(context=_run_ctx(), prompt="Another one"))
    await _until(lambda: _block(ui)["status"] == "requested" and _block(ui)["files"] == [])
    await _submit(room, "docs", {"files": []})
    assert await task == NOT_SENT


@pytest.mark.parametrize("channel", ["sip_in", "sip_out"])
async def test_request_upload_shows_nothing_on_a_phone_call(channel: str) -> None:
    ctx, ui, _room, _api = _setup(channel_name=channel)
    result = json.loads(await build_request_upload_tool(ctx)(context=_run_ctx(), prompt="A photo") or "{}")
    assert result["channel"] == "voice_only"
    assert _block(ui)["status"] == "idle"


async def test_request_upload_in_realtime_waits_in_the_background() -> None:
    ctx, ui, room, _api = _setup(mode="realtime")
    assert await build_request_upload_tool(ctx)(context=_run_ctx(), prompt="A photo") is None
    background = cast(FakeBackgroundRunner, ctx.background)
    await _until(lambda: _block(ui)["status"] == "requested")
    await ui.receive_upload(FakeReader(JPEG, name="car.jpg"), CALLER)
    await _submit(room, "docs", {"files": ["asset1"]})
    await background.wait_idle()
    (urgent,) = background.urgent_events
    assert "car.jpg" in (urgent[2] or "")


def test_request_upload_registers_only_with_an_upload_block() -> None:
    ctx, _ui, _room, _api = _setup()
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
    assert "request_upload" in names
    bare, _ui, _room, _api = _setup([BlockSpec(id="notes", type="notes")])
    assert "request_upload" not in {
        t.info.name for t in build_builtin_tools(bare, disabled=[], http_enabled=False)
    }
    off = {t.info.name for t in build_builtin_tools(ctx, disabled=["request_upload"], http_enabled=False)}
    assert "request_upload" not in off


# ================================================================ form fields


def test_the_new_form_field_types_map_onto_the_schema() -> None:
    schema = fields_to_schema(
        [
            FormField(name="phone", label="Phone", type="phone"),
            FormField(name="story", label="What happened", type="textarea"),
            FormField(name="licence", label="Licence", type="file", accept=["image/*"], max_files=2),
        ]
    )
    props = schema["properties"]
    assert props["phone"] == {"title": "Phone", "type": "string", "format": "phone"}
    assert props["story"] == {"title": "What happened", "type": "string", "x-lkap-widget": "textarea"}
    assert props["licence"]["type"] == "array" and props["licence"]["maxItems"] == 2
    assert props["licence"]["x-lkap-widget"] == "file"
    assert props["licence"]["x-lkap-upload"] == {"accept": ["image/*"], "max_files": 2, "max_bytes": 10485760}


async def test_a_form_file_field_goes_through_the_upload_path_and_keeps_only_stored_ids() -> None:
    form = BlockSpec(id="intake", type="form")
    ctx, ui, room, api = _setup([form])
    fields = [
        FormField(name="name", label="Name"),
        FormField(name="licence", label="Licence", type="file", accept=["image/*"], max_files=1),
    ]
    task = asyncio.create_task(
        build_request_form_tool(ctx)(context=_run_ctx(), block_id="intake", fields=fields)
    )
    await _until(lambda: _block(ui, "intake").get("status") == "requested")
    # The wrong field and a PDF are refused; the photo for `licence` is stored.
    assert await ui.receive_upload(FakeReader(PNG, block_id="intake", field="name"), CALLER) is None
    assert await ui.receive_upload(FakeReader(PDF, block_id="intake", field="licence"), CALLER) is None
    stored = await ui.receive_upload(FakeReader(PNG, block_id="intake", field="licence"), CALLER)
    assert stored is not None and api.posted[-1].meta == {"block_id": "intake", "field": "licence"}
    assert (
        await ui.receive_upload(FakeReader(JPEG, block_id="intake", field="licence"), CALLER) is None
    )  # max 1
    await _submit(room, "intake", {"name": "Ada", "licence": [stored.asset_id, "not-ours"]})
    result = json.loads(await task or "{}")
    assert result["values"] == {"name": "Ada", "licence": [stored.asset_id]}


def test_a_form_file_field_refuses_an_unsafe_accept() -> None:
    with pytest.raises(ValueError, match="not a file type"):
        fields_to_schema([FormField(name="f", label="F", type="file", accept=["text/html"])])


# ================================================================ open_citation (R-V5-5)


def _citation_blocks() -> list[BlockSpec]:
    return [BlockSpec(id="sources", type="kb_citations"), BlockSpec(id="viewer", type="document")]


async def _cite(ui: UiChannel, document_id: str) -> None:
    citation = KbCitation(
        chunk_id="c1",
        filename="policy.pdf",
        score=0.9,
        text="Covered.",
        document_id=document_id,
        page=3,
        heading_path=["Cover", "Fire"],
    )
    await ui.set_block("sources", {"items": [citation.model_dump(mode="json", exclude_none=True)]})


async def test_open_citation_copies_an_unheld_document_once_then_opens_it() -> None:
    api = FakeAssetApi(documents={"doc1": ("application/pdf", PDF)})
    _ctx, ui, room, _api = _setup(_citation_blocks(), api=api)
    await _cite(ui, "doc1")
    first = await open_citation(ui, "sources", {"chunk_id": "c1"})
    assert first == {"opened": "document", "block_id": "viewer", "page": 3}
    assert _block(ui, "viewer")["asset_id"] == "doc-doc1"
    ref = next(a for a in ui.state.assets if a.asset_id == "doc-doc1")
    assert ref.stored and ref.meta == {"document_id": "doc1"}
    assert any(s.attributes.get("asset_id") == "doc-doc1" for s in room.local_participant.byte_streams)
    again = await open_citation(ui, "sources", {"chunk_id": "c1"})
    assert again["opened"] == "document"
    assert api.copies == ["doc1"]  # lazily, once


@pytest.mark.parametrize(
    ("documents", "reason"), [({}, "no_source"), ({"doc1": ("text/html", HTML)}, "no_preview")]
)
async def test_open_citation_answers_why_it_could_not_open(
    documents: dict[str, tuple[str, bytes]], reason: str
) -> None:
    _ctx, ui, _room, _api = _setup(_citation_blocks(), api=FakeAssetApi(documents=documents))
    await _cite(ui, "doc1")
    result = await open_citation(ui, "sources", {"chunk_id": "c1"})
    assert result["opened"] is False and result["reason"] == reason
    assert result["citation"]["chunk_id"] == "c1"


# ================================================================ pin_frame compatibility


class _Frames:
    def __init__(self) -> None:
        self.snapshot = type("Snap", (), {"source": "camera"})()

    async def latest_jpeg(self, max_age_s: float | None = None) -> tuple[bytes, Any]:
        return JPEG, self.snapshot


@pytest.mark.parametrize("reject", [None, 503])
async def test_pin_frame_still_renders_from_the_envelope_bytes(reject: int | None) -> None:
    ctx, ui, room, api = _setup(
        [BlockSpec(id="photos", type="gallery")],
        api=FakeAssetApi(reject_status=reject),
        capabilities=CapabilitiesConfig(camera=True),
    )
    ctx.frames = _Frames()  # type: ignore[assignment]
    result = json.loads(await build_pin_frame_tool(ctx)(context=_run_ctx(), caption="Dent"))
    (ref,) = ui.state.assets
    assert result == {"pinned": True, "asset_id": ref.asset_id}
    (stream,) = [s for s in room.local_participant.byte_streams if s.topic == TOPIC_UI_ASSET]
    assert b"".join(stream.chunks) == JPEG and stream.attributes["asset_id"] == ref.asset_id
    assert _block(ui, "photos")["asset_ids"] == [ref.asset_id]
    assert ref.meta == {"source": "camera", "confirmed": "false"}
    assert api.posted[0].kind == "frame"
    assert ref.stored is (reject is None)


# ================================================================ the api client (config_client)

BASE = "http://api.test"


def _asset_json(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "a1",
        "session_id": "s1",
        "kind": "upload",
        "name": "x.png",
        "mime": "image/png",
        "size": len(PNG),
        "sha256": hashlib.sha256(PNG).hexdigest(),
        "meta": {"block_id": "docs"},
        "created_at": "2026-09-27T00:00:00Z",
    }
    return {**row, **overrides}


@respx.mock
async def test_post_asset_sends_multipart_with_the_service_token() -> None:
    route = respx.post(f"{BASE}/internal/v1/sessions/s1/assets").mock(
        return_value=httpx.Response(201, json=_asset_json())
    )
    client = ConfigClient(BASE, "svc-token")
    out = await client.post_asset("s1", PNG, name="x.png", mime="image/png", meta={"block_id": "docs"})
    assert out.id == "a1"
    request = route.calls.last.request
    assert request.headers["X-Service-Token"] == "svc-token"
    body = request.content
    assert b'name="file"' in body and PNG in body and b'"block_id": "docs"' in body
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(("status", "reason"), [(415, "no_preview"), (404, None)])
async def test_asset_errors_carry_the_status_and_reason(status: int, reason: str | None) -> None:
    details = {"reason": reason} if reason else None
    respx.post(f"{BASE}/internal/v1/sessions/s1/assets/from-document").mock(
        return_value=httpx.Response(status, json={"error": {"code": "x", "message": "m", "details": details}})
    )
    client = ConfigClient(BASE, "svc-token")
    with pytest.raises(AssetRejectedError) as caught:
        await client.asset_from_document("s1", "doc1")
    assert caught.value.status == status and caught.value.reason == reason
    await client.aclose()


@respx.mock
async def test_asset_content_and_an_unreachable_api() -> None:
    respx.get(f"{BASE}/internal/v1/sessions/s1/assets/a1/content").mock(
        return_value=httpx.Response(200, content=PNG)
    )
    respx.post(f"{BASE}/internal/v1/sessions/s1/assets").mock(side_effect=httpx.ConnectError("refused"))
    client = ConfigClient(BASE, "svc-token")
    assert await client.asset_content("s1", "a1") == PNG
    with pytest.raises(AssetRejectedError) as caught:
        await client.post_asset("s1", PNG, name="x.png", mime="image/png")
    assert caught.value.status == 0
    await client.aclose()
