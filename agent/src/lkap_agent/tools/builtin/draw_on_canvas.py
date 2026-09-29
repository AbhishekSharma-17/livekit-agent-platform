"""`draw_on_canvas` built-in tool (V6-12, D-V6-16): the agent marks up a drawing board.

Registered for a panel with a `canvas` block. The model draws boxes, circles (the ellipse
inside a box: "circle the dent"), arrows, paths and short text labels, every coordinate 0..1
of the board; a shape is upserted by `id` (reuse one to move or change it), `replace=true`
swaps all of the agent's shapes. `background` puts a picture of this session behind the
marks (a pinned frame, a gallery picture: `asset:<id>` in the state), the live camera, or
nothing. Annotating a pinned frame is `pin_frame(canvas_block_id=...)` then this tool.

The caller's strokes are never touched here (`clear_canvas` clears them). A write: blocking,
instant, never in the background; it answers nothing on a realtime model (a half cascade
too), like the other panel writes. A signature board (`signature_mode`) takes no shapes.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import (
    CANVAS_ASSET_BACKGROUND_PREFIX,
    DEFAULT_SHAPE_COLOR,
    MAX_CANVAS_SHAPES,
    MAX_SHAPE_LABEL_CHARS,
    MAX_SHAPE_TEXT_CHARS,
    CanvasShape,
    CanvasShapeKind,
    UiPatchOp,
)
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.json_args import json_list
from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import canvas_config, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = [
    "MAX_SHAPES_PER_CALL",
    "PointIn",
    "ShapeIn",
    "build_draw_on_canvas_tool",
    "canvas_background",
    "resolve_canvas",
]

#: The most shapes one call draws.
MAX_SHAPES_PER_CALL: Final[int] = 50


class PointIn(BaseModel):
    """One point on the board: 0 is the left (top) edge, 1 the right (bottom)."""

    x: float = Field(ge=0, le=1, description="0 = left edge, 1 = right edge.")
    y: float = Field(ge=0, le=1, description="0 = top edge, 1 = bottom edge.")


#: One point and one mark as the model writes them (V6-30).
POINT_EXAMPLE: Final[str] = '{"x": 0.2, "y": 0.3}'
SHAPE_EXAMPLE: Final[str] = '{"kind": "box", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.2, "label": "Leak"}'
#: JSON text of the points of a mark is read too (V6-30, F-2).
PointList = Annotated[list[PointIn], json_list(POINT_EXAMPLE)]


class ShapeIn(BaseModel):
    """One mark to draw."""

    kind: CanvasShapeKind = Field(description="box, circle, arrow, text or path.")
    id: str = Field(default="", description="Optional id; reuse one to move or change that mark.")
    x: float | None = Field(default=None, ge=0, le=1, description="box, circle, text: the left edge.")
    y: float | None = Field(default=None, ge=0, le=1, description="box, circle, text: the top edge.")
    w: float | None = Field(default=None, ge=0, le=1, description="box, circle: the width.")
    h: float | None = Field(default=None, ge=0, le=1, description="box, circle: the height.")
    points: PointList = Field(
        default=[], description="arrow: from and to (two points); path: two or more points."
    )
    text: str = Field(default="", description="text: the words to write.")
    label: str = Field(default="", description="Optional short caption next to the mark.")
    color: str = Field(default="", description="Optional colour as #rrggbb (default red).")


#: JSON text of the marks is read too (V6-30, F-2).
ShapeList = Annotated[list[ShapeIn], json_list(SHAPE_EXAMPLE)]


def _clean(text: str, limit: int) -> str:
    return " ".join(strip_control(text).split())[:limit]


def resolve_canvas(ctx: PackSessionContext, block_id: str) -> tuple[str, dict[str, Any]]:
    """The canvas block a call names (or the only one) and its current state.

    Raises:
        ToolError: With a model-readable message when the board is unknown or ambiguous.
    """
    specs = session_block_specs(ctx.ui, ctx.config.panel)
    try:
        target = pick_block(specs, "canvas", block_id or None)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    state = ctx.ui.state.blocks.get(target)
    return target, state if isinstance(state, dict) else {}


def canvas_background(ctx: PackSessionContext, value: str) -> str:
    """A `background` argument as a state value: `none`, `live_camera` or `asset:<id>`.

    Raises:
        ToolError: When it names no picture of this session.
    """
    wanted = value.strip()
    if wanted in ("none", "live_camera"):
        return wanted
    asset_id = wanted.removeprefix(CANVAS_ASSET_BACKGROUND_PREFIX)
    ref = next((a for a in ctx.ui.state.assets if a.asset_id == asset_id), None)
    if ref is None or not ref.mime.startswith("image/"):
        raise ToolError(
            f"There is no picture {asset_id!r} in this call; use a pinned frame's or a gallery "
            "picture's asset id, 'live_camera' or 'none'."
        )
    return f"{CANVAS_ASSET_BACKGROUND_PREFIX}{asset_id}"


def _shape(index: int, item: ShapeIn, now: float) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id.strip() or f"m:{uuid.uuid4().hex[:8]}",
        "kind": item.kind,
        "x": item.x,
        "y": item.y,
        "w": item.w,
        "h": item.h,
        "points": [[p.x, p.y] for p in item.points],
        "text": _clean(item.text, MAX_SHAPE_TEXT_CHARS) or None,
        "label": _clean(item.label, MAX_SHAPE_LABEL_CHARS) or None,
        "color": item.color.strip() or DEFAULT_SHAPE_COLOR,
        "ts": now,
    }
    try:
        return CanvasShape.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        error = exc.errors()[0]
        raise ToolError(f"Shape {index + 1} ({item.kind}): {error['msg']}.") from exc


def build_draw_on_canvas_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `draw_on_canvas` tool bound to `ctx`."""
    boards = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["canvas"])

    async def draw_on_canvas(
        context: RunContext[Any],
        shapes: ShapeList,
        block_id: str = "",
        background: str = "",
        replace: bool = False,
    ) -> str | None:
        """Draw marks on the drawing board: boxes, circles, arrows, paths and short labels.

        Args:
            shapes: The marks to draw; coordinates run from 0 to 1 across the board.
            block_id: The drawing board; leave empty when there is only one.
            background: Optional: a picture's asset id to draw over (a pinned frame or a gallery
                picture), "live_camera", or "none"; leave empty to keep the current one.
            replace: True to remove your earlier marks first.
        """
        target, state = resolve_canvas(ctx, block_id)
        spec = next(s for s in session_block_specs(ctx.ui, ctx.config.panel) if s.id == target)
        if canvas_config(spec).signature_mode and shapes:
            raise ToolError(f"{target} is a signature board; nothing can be drawn on it.")
        if not shapes and not background.strip() and not replace:
            raise ToolError("Pass the shapes to draw, or a background.")
        if len(shapes) > MAX_SHAPES_PER_CALL:
            raise ToolError(f"Draw at most {MAX_SHAPES_PER_CALL} marks at a time.")
        now = time.time()
        drawn = [_shape(i, item, now) for i, item in enumerate(shapes)]
        ids = [shape["id"] for shape in drawn]
        if len(set(ids)) != len(ids):
            raise ToolError("Give each mark its own id.")
        ops: list[UiPatchOp] = []
        if replace:
            ops.append(UiPatchOp(op="set", path="/shapes", value=drawn))
        else:
            current = {s.get("id") for s in state.get("shapes") or [] if isinstance(s, dict)}
            if len(current | set(ids)) > MAX_CANVAS_SHAPES:
                raise ToolError(
                    f"The board holds at most {MAX_CANVAS_SHAPES} marks; replace or clear some first."
                )
            ops += [UiPatchOp(op="upsert", path="/shapes", value=shape, key=shape["id"]) for shape in drawn]
        if background.strip():
            ops.append(UiPatchOp(op="set", path="/background", value=canvas_background(ctx, background)))
        ops.append(UiPatchOp(op="set", path="/updated_at", value=now))
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:
            raise ToolError(f"That does not fit the drawing board: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.draw_on_canvas",
            call_id=context.function_call.call_id,
            block_id=target,
            shapes=len(drawn),
            kinds=sorted({shape["kind"] for shape in drawn}),
            replace=replace,
            background=bool(background.strip()),
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"Drew {len(drawn)} mark{'s' if len(drawn) != 1 else ''} on {target} (ids: {', '.join(ids)})."

    return function_tool(
        draw_on_canvas,
        description=(
            "Mark up the drawing board the caller sees: boxes, circles, arrows, paths and short "
            "labels (coordinates 0 to 1), optionally over a picture from this call. Do it quietly. "
            f"Marks are objects, e.g. shapes=[{SHAPE_EXAMPLE}]; an arrow or a path takes "
            f"points=[{POINT_EXAMPLE}, …]. Boards: {boards}."
        ),
    )
