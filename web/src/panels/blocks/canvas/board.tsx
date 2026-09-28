"use client";

/**
 * The drawing surface itself (V6-14, D-V6-16): an SVG the console owns, `perfect-freehand`
 * outlines for `pen`/`highlighter`, hand-drawn rectangles/arrows for `box`/`arrow`, pointer
 * events for mouse, touch and stylus (with pressure when the device reports one), agent
 * shapes (`CanvasShape`) rendered from state, and a local "still drawing" layer so a
 * stroke appears the instant the caller draws it rather than after the worker's ~100 ms
 * patch (CONTRACTS §10: "replace the optimistic local stroke with the state's copy").
 *
 * **Reconciliation.** A pending local stroke is dropped as soon as the confirmed
 * `strokes` prop carries at least as many points for the same id (the worker's patch has
 * caught up), and in any case after `PENDING_STROKE_TTL_MS` past its `endStroke()` — a
 * message the worker silently dropped (`tool_not_offered`, `session_limit`: the latter
 * has no state signal at all) must not leave a ghost stroke on screen forever.
 *
 * `text` never appears here as a tool (D-V6-16: a stroke never carries text).
 */
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { CanvasShape, InkStroke } from "@/contracts/lkap-contracts";
import type { InkSender, WireInkTool } from "@/panels/composite/ink-stream";

import {
  arrowGeometry,
  ellipseAttrs,
  normalizePoint,
  pointFromClient,
  polylinePath,
  rectPath,
  toBoardPixels,
  type InkPoint,
} from "./geometry";
import { preloadFreehand, strokePathD } from "./freehand";
import type { CanvasToolChoice } from "./permissions";

const TOOL_WIDTH: Record<WireInkTool, number> = { pen: 3, highlighter: 16, box: 3, arrow: 3 };
/** How long a pending local stroke is kept once the pointer lifts, if the state patch never confirms it (a dropped message). */
const PENDING_STROKE_TTL_MS = 3000;

interface PendingStroke {
  tool: WireInkTool;
  color: string;
  width: number;
  points: InkPoint[];
  /** Set once the pointer lifts; the stroke is dropped `PENDING_STROKE_TTL_MS` after this. */
  endedAt: number | null;
}

interface DraftShape {
  tool: "box" | "arrow";
  from: InkPoint;
  to: InkPoint;
}

export interface CanvasBoardProps {
  width: number;
  height: number;
  strokes: readonly InkStroke[];
  shapes: readonly CanvasShape[];
  tool: CanvasToolChoice;
  color: string;
  callerCanDraw: boolean;
  limitReached: boolean;
  sender: InkSender | undefined;
  /** Records a stroke id the caller just sent, for the toolbar's "undo" target. */
  onStrokeSent: (strokeId: string) => void;
  /** A smaller board with a baseline and no shapes (reserved for the signature block, V6-23). */
  signatureMode?: boolean;
}

function shapeOpacity(tool: WireInkTool): number {
  return tool === "highlighter" ? 0.4 : 1;
}

function pixelsOf(width: number, height: number) {
  return (point: InkPoint): [number, number] => toBoardPixels(point, width, height);
}

/** `box`/`arrow` are drawn as a stroked outline, never a freehand fill (`strokeShapeD` below). */
function isShapeTool(tool: WireInkTool): tool is "box" | "arrow" {
  return tool === "box" || tool === "arrow";
}

/** `box`/`arrow` are "first and last point" shapes (CONTRACTS §10) — a stroked outline, not a freehand fill. */
function strokeShapeD(tool: "box" | "arrow", points: readonly InkPoint[], toPixels: (p: InkPoint) => [number, number], width: number): string {
  const from = toPixels(points[0]);
  const to = toPixels(points[points.length - 1]);
  if (tool === "arrow") {
    const { leftWing, rightWing } = arrowGeometry(from, to, width);
    return `M ${from[0]} ${from[1]} L ${to[0]} ${to[1]} M ${leftWing[0]} ${leftWing[1]} L ${to[0]} ${to[1]} L ${rightWing[0]} ${rightWing[1]}`;
  }
  const x = Math.min(from[0], to[0]);
  const y = Math.min(from[1], to[1]);
  return rectPath(x, y, Math.abs(to[0] - from[0]), Math.abs(to[1] - from[1]));
}

function ShapeView({ shape, width, height }: { shape: CanvasShape; width: number; height: number }) {
  const color = shape.color ?? "#dc2626";
  const strokeWidth = shape.width ?? 4;
  switch (shape.kind) {
    case "box": {
      if (shape.x == null || shape.y == null || shape.w == null || shape.h == null) return null;
      const x = shape.x * width;
      const y = shape.y * height;
      return <path d={rectPath(x, y, shape.w * width, shape.h * height)} fill="none" stroke={color} strokeWidth={strokeWidth} />;
    }
    case "circle": {
      if (shape.x == null || shape.y == null || shape.w == null || shape.h == null) return null;
      const { cx, cy, rx, ry } = ellipseAttrs(shape.x * width, shape.y * height, shape.w * width, shape.h * height);
      return <ellipse cx={cx} cy={cy} rx={Math.max(rx, 0.01)} ry={Math.max(ry, 0.01)} fill="none" stroke={color} strokeWidth={strokeWidth} />;
    }
    case "arrow": {
      const points = shape.points ?? [];
      if (points.length !== 2) return null;
      const from: [number, number] = [points[0][0] * width, points[0][1] * height];
      const to: [number, number] = [points[1][0] * width, points[1][1] * height];
      const { leftWing, rightWing } = arrowGeometry(from, to, strokeWidth);
      const d = `M ${from[0]} ${from[1]} L ${to[0]} ${to[1]} M ${leftWing[0]} ${leftWing[1]} L ${to[0]} ${to[1]} L ${rightWing[0]} ${rightWing[1]}`;
      return <path d={d} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />;
    }
    case "path": {
      const points = (shape.points ?? []).map(([x, y]): [number, number] => [x * width, y * height]);
      if (points.length < 2) return null;
      return <path d={polylinePath(points)} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" />;
    }
    case "text": {
      if (shape.x == null || shape.y == null || !shape.text) return null;
      return (
        <text x={shape.x * width} y={shape.y * height} fill={color} fontSize={Math.max(12, strokeWidth * 4)} dominantBaseline="hanging">
          {shape.text}
        </text>
      );
    }
    default:
      return null;
  }
}

export function CanvasBoard({
  width,
  height,
  strokes,
  shapes,
  tool,
  color,
  callerCanDraw,
  limitReached,
  sender,
  onStrokeSent,
  signatureMode,
}: CanvasBoardProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const board = signatureMode ? { width, height: Math.min(height, Math.round(width * 0.35)) } : { width, height };
  const toPixels = useMemo(() => pixelsOf(board.width, board.height), [board.width, board.height]);
  const [, forceRender] = useState(0);
  const pendingRef = useRef<Map<string, PendingStroke>>(new Map());
  const draftShapeRef = useRef<DraftShape | null>(null);
  const [draftShape, setDraftShape] = useState<DraftShape | null>(null);
  const activeStrokeIdRef = useRef<string | null>(null);

  useEffect(() => {
    void preloadFreehand();
  }, []);

  // Drop a pending stroke once the confirmed copy has caught up, or its TTL has lapsed.
  useEffect(() => {
    const byId = new Map(strokes.map((s) => [s.id, s]));
    let changed = false;
    const now = Date.now();
    for (const [id, pending] of pendingRef.current) {
      const confirmed = byId.get(id);
      const caughtUp = confirmed && confirmed.points.length >= pending.points.length;
      const expired = pending.endedAt !== null && now - pending.endedAt > PENDING_STROKE_TTL_MS;
      if (caughtUp || expired) {
        pendingRef.current.delete(id);
        changed = true;
      }
    }
    if (changed) forceRender((n) => n + 1);
  }, [strokes]);

  function rectOf(): DOMRect {
    return svgRef.current?.getBoundingClientRect() ?? new DOMRect(0, 0, 0, 0);
  }

  function eventPoint(event: React.PointerEvent<SVGSVGElement>): InkPoint {
    const pressure = event.pointerType === "pen" ? event.pressure : undefined;
    return pointFromClient(event.clientX, event.clientY, rectOf(), pressure);
  }

  function beginFreehand(tool: "pen" | "highlighter", point: InkPoint): void {
    if (!sender) return;
    const width = TOOL_WIDTH[tool];
    const strokeId = sender.beginFreehandStroke(tool, color, width, point);
    activeStrokeIdRef.current = strokeId;
    pendingRef.current.set(strokeId, { tool, color, width, points: [normalizePoint(point)], endedAt: null });
    forceRender((n) => n + 1);
  }

  function continueFreehand(points: InkPoint[]): void {
    const strokeId = activeStrokeIdRef.current;
    if (!strokeId || !sender || points.length === 0) return;
    sender.addPoints(strokeId, points);
    const pending = pendingRef.current.get(strokeId);
    if (pending) pending.points.push(...points.map(normalizePoint));
    forceRender((n) => n + 1);
  }

  function endFreehand(): void {
    const strokeId = activeStrokeIdRef.current;
    activeStrokeIdRef.current = null;
    if (!strokeId) return;
    sender?.endStroke();
    const pending = pendingRef.current.get(strokeId);
    if (pending) pending.endedAt = Date.now();
    onStrokeSent(strokeId);
    forceRender((n) => n + 1);
  }

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>): void {
    if (!callerCanDraw || limitReached || tool === "eraser" || !sender) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const point = eventPoint(event);
    if (tool === "pen" || tool === "highlighter") {
      beginFreehand(tool, point);
    } else {
      draftShapeRef.current = { tool, from: point, to: point };
      setDraftShape(draftShapeRef.current);
    }
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>): void {
    if (activeStrokeIdRef.current) {
      const coalesced = event.nativeEvent.getCoalescedEvents?.() ?? [event.nativeEvent];
      const points = coalesced.map((native) => {
        const pressure = event.pointerType === "pen" ? native.pressure : undefined;
        return pointFromClient(native.clientX, native.clientY, rectOf(), pressure);
      });
      continueFreehand(points);
      return;
    }
    if (draftShapeRef.current) {
      const point = eventPoint(event);
      draftShapeRef.current = { ...draftShapeRef.current, to: point };
      setDraftShape(draftShapeRef.current);
    }
  }

  function endDraftShape(): void {
    const draft = draftShapeRef.current;
    draftShapeRef.current = null;
    setDraftShape(null);
    if (!draft || !sender) return;
    const [fx, fy] = draft.from;
    const [tx, ty] = draft.to;
    if (Math.hypot(tx - fx, ty - fy) < 0.005) return; // a tap, not a drag: nothing to send
    const width = TOOL_WIDTH[draft.tool];
    const strokeId = sender.sendShapeStroke(draft.tool, color, width, draft.from, draft.to);
    sender.endStroke(); // flush at once — the pointer already lifted, like a freehand stroke's own endStroke
    pendingRef.current.set(strokeId, {
      tool: draft.tool,
      color,
      width,
      points: [normalizePoint(draft.from), normalizePoint(draft.to)],
      endedAt: Date.now(),
    });
    onStrokeSent(strokeId);
    forceRender((n) => n + 1);
  }

  function onPointerUp(event: React.PointerEvent<SVGSVGElement>): void {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (activeStrokeIdRef.current) endFreehand();
    else if (draftShapeRef.current) endDraftShape();
  }

  function onPointerCancel(event: React.PointerEvent<SVGSVGElement>): void {
    onPointerUp(event);
  }

  function eraseAt(strokeId: string) {
    return (event: React.PointerEvent) => {
      if (tool !== "eraser") return;
      event.stopPropagation();
      sender?.erase(strokeId);
    };
  }

  const pendingStrokes = [...pendingRef.current.entries()];

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${board.width} ${board.height}`}
      role="img"
      aria-label={callerCanDraw ? "Drawing board — draw with your mouse, finger or stylus" : "Drawing board"}
      data-slot="canvas-board"
      className="absolute inset-0 size-full"
      style={{ touchAction: callerCanDraw ? "none" : undefined, pointerEvents: callerCanDraw ? "auto" : "none" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      {signatureMode && (
        <line
          x1={board.width * 0.05}
          y1={board.height * 0.8}
          x2={board.width * 0.95}
          y2={board.height * 0.8}
          stroke="#9ca3af"
          strokeWidth={1.5}
          strokeDasharray="6 4"
        />
      )}
      {strokes.map((stroke) => {
        const strokeTool = (stroke.tool as WireInkTool | undefined) ?? "pen";
        const shapeStroke = isShapeTool(strokeTool);
        const d = shapeStroke
          ? strokeShapeD(strokeTool, stroke.points as InkPoint[], toPixels, stroke.width ?? 3)
          : strokePathD(stroke.points as InkPoint[], toPixels, stroke.width ?? 3);
        return (
          <path
            key={stroke.id}
            d={d}
            fill={shapeStroke ? "none" : (stroke.color ?? "#1f2937")}
            stroke={shapeStroke ? (stroke.color ?? "#1f2937") : undefined}
            strokeWidth={shapeStroke ? (stroke.width ?? 3) : undefined}
            fillOpacity={shapeStroke ? undefined : shapeOpacity(strokeTool)}
            data-slot="canvas-stroke"
            data-stroke-id={stroke.id}
            style={{ pointerEvents: callerCanDraw && tool === "eraser" ? "visiblePainted" : "none", cursor: tool === "eraser" ? "pointer" : undefined }}
            onPointerDown={eraseAt(stroke.id)}
          />
        );
      })}
      {pendingStrokes.map(([id, pending]) => {
        const pendingTool = pending.tool;
        const shapeStroke = isShapeTool(pendingTool);
        const d = shapeStroke
          ? strokeShapeD(pendingTool, pending.points, toPixels, pending.width)
          : strokePathD(pending.points, toPixels, pending.width);
        return (
          <path
            key={`pending-${id}`}
            d={d}
            fill={shapeStroke ? "none" : pending.color}
            stroke={shapeStroke ? pending.color : undefined}
            strokeWidth={shapeStroke ? pending.width : undefined}
            fillOpacity={shapeStroke ? undefined : shapeOpacity(pendingTool)}
            data-slot="canvas-stroke-pending"
          />
        );
      })}
      {!signatureMode && shapes.map((shape) => <ShapeView key={shape.id} shape={shape} width={board.width} height={board.height} />)}
      {draftShape && (
        <g data-slot="canvas-draft-shape" opacity={0.85}>
          {draftShape.tool === "box" ? (
            <path
              d={rectPath(
                Math.min(draftShape.from[0], draftShape.to[0]) * board.width,
                Math.min(draftShape.from[1], draftShape.to[1]) * board.height,
                Math.abs(draftShape.to[0] - draftShape.from[0]) * board.width,
                Math.abs(draftShape.to[1] - draftShape.from[1]) * board.height,
              )}
              fill="none"
              stroke={color}
              strokeWidth={TOOL_WIDTH.box}
              strokeDasharray="4 3"
            />
          ) : (
            <line
              x1={draftShape.from[0] * board.width}
              y1={draftShape.from[1] * board.height}
              x2={draftShape.to[0] * board.width}
              y2={draftShape.to[1] * board.height}
              stroke={color}
              strokeWidth={TOOL_WIDTH.arrow}
              strokeDasharray="4 3"
            />
          )}
        </g>
      )}
    </svg>
  );
}
