"""Unit tests for `packs.base` (W0-SCAFFOLD).

Depends on `lkap_contracts` (W0-CONTRACTS) for `AgentConfig`/`PackManifest`/
`UiState` types referenced by the Protocols.
"""

from __future__ import annotations

from typing import cast

from livekit import rtc

from packs.base import (
    BackgroundRunner,
    FrameBufferProto,
    FrameSnapshot,
    ImageGen,
    KbClient,
    Pack,
    PackSessionContext,
    StructuredLLM,
    ToolMeta,
    UiChannel,
)

PROTOCOLS = (
    UiChannel,
    FrameBufferProto,
    KbClient,
    StructuredLLM,
    BackgroundRunner,
    ImageGen,
    PackSessionContext,
    Pack,
)


def test_all_interfaces_are_protocol_classes() -> None:
    for proto in PROTOCOLS:
        assert getattr(proto, "_is_protocol", False) is True, proto


def test_frame_snapshot_is_a_plain_dataclass() -> None:
    dummy_frame = cast(rtc.VideoFrame, object())
    snap = FrameSnapshot(frame=dummy_frame, source="camera", age_s=0.5)
    assert snap.source == "camera"
    assert snap.age_s == 0.5
    assert snap.frame is dummy_frame


def test_tool_meta_defaults() -> None:
    meta = ToolMeta(name="pin_frame")
    assert meta.silent_reply is False
    assert meta.activity_label is None


def test_tool_meta_round_trip() -> None:
    meta = ToolMeta(name="sync_claim_packet", silent_reply=True, activity_label="Policy desk")
    assert ToolMeta.model_validate(meta.model_dump()) == meta
