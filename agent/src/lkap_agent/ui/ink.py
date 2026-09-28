"""The caller's ink on a ``canvas`` block (V6-12, D-V6-16): the limits and one message's ops.

Pure helpers for `UiChannel`'s ``lkap.ui.ink`` handler (nothing here talks to LiveKit):

* :class:`InkBudget` — the rate limit, a token bucket of
  :data:`~lkap_contracts.ui_protocol.MAX_INK_MESSAGES_PER_S` a second (burst of the
  same), taken *before* a message is read;
* :func:`apply_ink` — what one checked :class:`~lkap_contracts.ui_protocol.InkMessage` does
  to a canvas's state: the block-relative ops to send, or why it is dropped. It enforces the
  board's tools and every size limit: points a stroke (1,000), strokes a board (the config's
  ``max_strokes``, at most 2,000), points a board (20,000) and the points the session may still
  take (50,000 in all, counted by the channel).

A caller only ever adds, continues, erases or clears their own strokes: the agent's shapes
are out of reach, and a stroke carries no text (security row 7). Points are rounded to four
decimals (a tenth of a pixel on a 1,600-pixel board) so the state stays small.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from lkap_contracts.blocks import CanvasBlockConfig
from lkap_contracts.ui_protocol import (
    MAX_CANVAS_POINTS,
    MAX_INK_MESSAGES_PER_S,
    MAX_STROKE_POINTS,
    InkMessage,
    InkStroke,
    UiPatchOp,
)

__all__ = [
    "INK_DROP_REASONS",
    "InkBudget",
    "InkOutcome",
    "apply_ink",
    "caller_stroke",
    "stroke_points",
]

#: Why a message was dropped (the channel counts each and records the first of each kind).
INK_DROP_REASONS: Final[tuple[str, ...]] = (
    "not_caller",
    "rate",
    "too_large",
    "malformed",
    "not_a_canvas",
    "drawing_off",
    "tool_not_offered",
    "unknown_stroke",
    "stroke_too_long",
    "canvas_full",
    "session_limit",
    "failed",
)
_DECIMALS: Final[int] = 4


@dataclass
class InkBudget:
    """A token bucket: ``rate`` messages a second, bursts of up to ``rate``."""

    rate: float = float(MAX_INK_MESSAGES_PER_S)
    tokens: float = float(MAX_INK_MESSAGES_PER_S)
    last: float = field(default_factory=time.monotonic)

    def take(self, now: float | None = None) -> bool:
        """Spend one token if there is one (refilled by the time since the last call)."""
        current = time.monotonic() if now is None else now
        self.tokens = min(self.rate, self.tokens + max(0.0, current - self.last) * self.rate)
        self.last = current
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


@dataclass(frozen=True)
class InkOutcome:
    """What one message does: block-relative ``ops`` to send, or the ``drop`` reason.

    ``limit_hit`` is set when the message was dropped because the board is full, so the
    channel can tell the page (``limit_reached``) and the team feed once. ``points`` is how
    many points the message added (for the session's count).
    """

    ops: list[UiPatchOp] = field(default_factory=list)
    drop: str | None = None
    limit_hit: bool = False
    points: int = 0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def caller_stroke(state: Mapping[str, Any], stroke_id: str) -> dict[str, Any] | None:
    """The caller's stroke ``stroke_id`` on the board, if there is one."""
    for stroke in _list(state.get("strokes")):
        if isinstance(stroke, dict) and stroke.get("id") == stroke_id and stroke.get("author") == "caller":
            return stroke
    return None


def stroke_points(state: Mapping[str, Any]) -> int:
    """How many stroke points the board holds."""
    return sum(len(_list(s.get("points"))) for s in _list(state.get("strokes")) if isinstance(s, dict))


def _rounded(points: list[list[float]]) -> list[list[float]]:
    return [[round(value, _DECIMALS) for value in point] for point in points]


def _full(state: Mapping[str, Any]) -> InkOutcome:
    if state.get("limit_reached") is True:
        return InkOutcome(drop="canvas_full", limit_hit=True)
    ops = [UiPatchOp(op="set", path="/limit_reached", value=True)]
    return InkOutcome(ops=ops, drop="canvas_full", limit_hit=True)


def apply_ink(
    message: InkMessage,
    state: Mapping[str, Any],
    config: CanvasBlockConfig,
    *,
    session_points_left: int,
    now: float,
) -> InkOutcome:
    """The block-relative ops one checked message makes to a canvas's state, or why it is dropped.

    Args:
        message: A message that validated (:class:`InkMessage`), for this canvas.
        state: The canvas's current state (``CanvasBlockState`` as JSON).
        config: The canvas's config (its tools and ``max_strokes``).
        session_points_left: How many more points the session may take from the caller.
        now: The time stamped on a new stroke and on ``updated_at``.

    Returns:
        The outcome. A dropped message may still carry ops: the first time the board is
        full it sets ``limit_reached`` so the page can say so.
    """
    stamp = UiPatchOp(op="set", path="/updated_at", value=now)
    match message.op:
        case "erase":
            if message.stroke_id is None or caller_stroke(state, message.stroke_id) is None:
                return InkOutcome(drop="unknown_stroke")
            ops = [UiPatchOp(op="remove", path="/strokes", key=message.stroke_id), stamp]
            if state.get("limit_reached") is True:
                ops.append(UiPatchOp(op="set", path="/limit_reached", value=False))
            return InkOutcome(ops=ops)
        case "clear":
            kept = [
                s for s in _list(state.get("strokes")) if isinstance(s, dict) and s.get("author") != "caller"
            ]
            return InkOutcome(
                ops=[
                    UiPatchOp(op="set", path="/strokes", value=kept),
                    UiPatchOp(op="set", path="/limit_reached", value=False),
                    stamp,
                ]
            )
        case _:
            pass
    if message.stroke_id is None:  # pragma: no cover - InkMessage refuses an add without one
        return InkOutcome(drop="malformed")
    points = _rounded(message.points)
    count = len(points)
    if count > session_points_left:
        return InkOutcome(drop="session_limit")
    if stroke_points(state) + count > MAX_CANVAS_POINTS:
        return _full(state)
    existing = caller_stroke(state, message.stroke_id)
    if existing is not None:
        if len(_list(existing.get("points"))) + count > MAX_STROKE_POINTS:
            return InkOutcome(drop="stroke_too_long")
        stroke = {**existing, "points": [*_list(existing.get("points")), *points]}
    else:
        strokes = _list(state.get("strokes"))
        if any(isinstance(s, dict) and s.get("id") == message.stroke_id for s in strokes):
            return InkOutcome(drop="unknown_stroke")  # an id that is not the caller's
        if message.tool not in config.tools:
            # The first message of a stroke sets its tool; the pieces that follow keep it.
            return InkOutcome(drop="tool_not_offered")
        if len(strokes) >= config.max_strokes:
            return _full(state)
        if count > MAX_STROKE_POINTS:  # pragma: no cover - a message holds at most 128 points
            return InkOutcome(drop="stroke_too_long")
        stroke = InkStroke(
            id=message.stroke_id,
            tool=message.tool,
            points=points,
            color=message.color,
            width=message.width,
            ts=now,
        ).model_dump(mode="json")
    return InkOutcome(
        ops=[UiPatchOp(op="upsert", path="/strokes", value=stroke, key=message.stroke_id), stamp],
        points=count,
    )
