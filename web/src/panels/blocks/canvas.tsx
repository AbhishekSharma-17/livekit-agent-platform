"use client";

/**
 * `canvas` block — the drawing board (V6-14, D-V6-16, ask #92): pen / highlighter / eraser
 * / box / arrow (whichever `config.tools` offers), touch/mouse/stylus input with pressure,
 * a background of nothing, a picture of this session or the caller's live camera, agent
 * shapes rendered from state, "the board is full", and the snapshot answer to
 * `read_canvas` (a PNG on the existing `lkap.ui.upload` path). The toolkit is split out:
 * `canvas/board.tsx` (the SVG + pointer handling), `canvas/toolbar.tsx`, `canvas/
 * background.tsx`, `canvas/{geometry,freehand,rasterise,permissions}.ts`.
 *
 * Loaded lazily by `<Block>` (`blocks/index.tsx`): `perfect-freehand` itself is a further
 * dynamic import inside `canvas/freehand.ts`, so `/s/[slug]`'s first load carries neither.
 *
 * **Permissions.** `caller_can_draw` is computed here from the whole panel
 * (`canvasCallerCanDraw`), not received as a prop — a notebook's `ink` section renders this
 * component through `<Block>` without passing its own flag (`notebook/sections.tsx`), so
 * the board must derive the "may the caller draw" rule itself, exactly as the worker does.
 * Outside a live room (the composer preview, the console's read-only session snapshot)
 * there is no `InkSender` to draw with, so the board is always read-only there regardless
 * of config — the same shape as `video`/`upload`'s "only live" states.
 */
import * as React from "react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useMaybeRoomContext } from "@livekit/components-react";

import type { CanvasBlockState } from "@/contracts/lkap-contracts";
import { useInkSender, type InkRoom } from "@/panels/composite/ink-stream";
import { panelLayoutOf } from "@/panels/composite/layout";
import { subscribeCompositeRequests } from "@/panels/composite/requests";
import { sendUploadFile, type UploadRoom } from "@/panels/composite/upload";

import { CanvasBackground, parseBackground } from "./canvas/background";
import { CanvasBoard } from "./canvas/board";
import { canvasCallerCanDraw, canvasSignatureMode, canvasTools, type CanvasToolChoice } from "./canvas/permissions";
import { rasteriseBoard, MAX_CANVAS_SNAPSHOT_BYTES, type RasterBoard } from "./canvas/rasterise";
import { CanvasToolbar } from "./canvas/toolbar";
import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const DEFAULT_INK_COLOR = "#1f2937";
/** Local-history cap: enough for a sensible undo trail without an unbounded ref. */
const MAX_LOCAL_HISTORY = 50;

type CanvasBlockRenderProps = BlockRenderProps<CanvasBlockState>;

/** One downscale retry when a rasterised PNG lands over the api's cap (`MAX_CANVAS_SNAPSHOT_BYTES`). */
async function rasteriseWithinBudget(board: RasterBoard): Promise<Blob | null> {
  let blob = await rasteriseBoard(board);
  if (blob && blob.size > MAX_CANVAS_SNAPSHOT_BYTES) {
    const scale = 0.6;
    blob = await rasteriseBoard({ ...board, width: Math.round(board.width * scale), height: Math.round(board.height * scale) });
  }
  return blob;
}

export function CanvasBlock({ spec, data, panel, title, highlighted }: CanvasBlockRenderProps) {
  const roomContext = useMaybeRoomContext();
  const inkRoom = roomContext as InkRoom | undefined;
  const uploadRoom = roomContext as UploadRoom | undefined;
  const sender = useInkSender(inkRoom, spec.id);

  const blocks = useMemo(() => panelLayoutOf(panel.agent).blocks, [panel.agent]);
  const configCallerCanDraw = canvasCallerCanDraw(spec.id, blocks);
  const callerCanDraw = configCallerCanDraw && sender !== undefined;
  const tools = useMemo(() => {
    const configured = canvasTools(spec.config);
    return configured.length > 0 ? configured : (["pen"] as const);
  }, [spec.config]);
  const signatureMode = canvasSignatureMode(spec.config);

  const [tool, setTool] = useState<CanvasToolChoice>(tools[0]);
  const [color, setColor] = useState(DEFAULT_INK_COLOR);
  const [localHistory, setLocalHistory] = useState<string[]>([]);

  const strokes = useMemo(() => data.strokes ?? [], [data.strokes]);
  const shapes = useMemo(() => data.shapes ?? [], [data.shapes]);
  const limitReached = data.limit_reached === true;
  const background = useMemo(() => parseBackground(data.background), [data.background]);

  const handleStrokeSent = useCallback((id: string) => {
    setLocalHistory((history) => [...history.slice(-(MAX_LOCAL_HISTORY - 1)), id]);
  }, []);

  const handleUndo = useCallback(() => {
    const targetId = localHistory[localHistory.length - 1] ?? strokes[strokes.length - 1]?.id;
    if (!targetId || !sender) return;
    sender.erase(targetId);
    setLocalHistory((history) => history.filter((id) => id !== targetId));
  }, [localHistory, strokes, sender]);

  const handleClear = useCallback(() => {
    sender?.clear();
    setLocalHistory([]);
  }, [sender]);

  // The board's currently-rendered background element (an `<img>` or the camera's
  // `<video>`), kept live so a snapshot rasterises exactly what is on screen.
  const backgroundElementRef = useRef<HTMLImageElement | HTMLVideoElement | null>(null);
  const handleBackgroundElement = useCallback((element: HTMLImageElement | HTMLVideoElement | null) => {
    backgroundElementRef.current = element;
  }, []);

  // Read by the snapshot subscription below without re-subscribing on every state tick.
  const latestRef = useRef({ strokes, shapes, background, width: data.width ?? 1600, height: data.height ?? 1200 });
  latestRef.current = { strokes, shapes, background, width: data.width ?? 1600, height: data.height ?? 1200 };

  const answerSnapshot = useCallback(async () => {
    if (!uploadRoom) return;
    const { strokes: currentStrokes, shapes: currentShapes, background: currentBackground, width, height } = latestRef.current;
    const element = currentBackground === "none" ? undefined : (backgroundElementRef.current ?? undefined);
    const naturalSize =
      element instanceof HTMLImageElement
        ? { w: element.naturalWidth || width, h: element.naturalHeight || height }
        : element instanceof HTMLVideoElement
          ? { w: element.videoWidth || width, h: element.videoHeight || height }
          : undefined;
    const board: RasterBoard = {
      width,
      height,
      background: {
        kind: currentBackground === "none" ? "none" : currentBackground === "live_camera" ? "video" : "image",
        source: element,
        naturalSize,
      },
      strokes: currentStrokes.map((stroke) => ({
        points: stroke.points,
        color: stroke.color ?? DEFAULT_INK_COLOR,
        width: stroke.width ?? 3,
      })),
      shapes: currentShapes,
    };
    const blob = await rasteriseWithinBudget(board);
    if (!blob) return;
    const file = new File([blob], `${spec.id}.png`, { type: "image/png" });
    try {
      await sendUploadFile(uploadRoom, file, { blockId: spec.id });
    } catch {
      // The worker's `request_canvas_snapshot` times out and `read_canvas` answers
      // plainly if nothing ever arrives — no further recovery to attempt client-side.
    }
  }, [uploadRoom, spec.id]);

  React.useEffect(
    () =>
      subscribeCompositeRequests((event) => {
        if (event.kind !== "snapshot" || event.blockId !== spec.id) return false;
        void answerSnapshot();
        return true;
      }),
    [spec.id, answerSnapshot],
  );

  const boardHeight = signatureMode ? "min-h-32" : "min-h-64 sm:min-h-96";

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {callerCanDraw && (
        <CanvasToolbar
          tools={tools}
          tool={tool}
          onToolChange={setTool}
          color={color}
          onColorChange={setColor}
          onUndo={handleUndo}
          canUndo={strokes.length > 0 || localHistory.length > 0}
          onClear={handleClear}
          canClear={strokes.length > 0}
        />
      )}
      <div data-slot="canvas-frame" className={`relative w-full overflow-hidden ${boardHeight}`}>
        <CanvasBackground background={background} assets={panel.assets} hasRoom={sender !== undefined} onElementReady={handleBackgroundElement} />
        <CanvasBoard
          width={data.width ?? 1600}
          height={data.height ?? 1200}
          strokes={strokes}
          shapes={shapes}
          tool={tool}
          color={color}
          callerCanDraw={callerCanDraw}
          limitReached={limitReached}
          sender={sender}
          onStrokeSent={handleStrokeSent}
          signatureMode={signatureMode}
        />
      </div>
      <div aria-live="polite" className="sr-only" data-slot="canvas-live-region">
        {limitReached ? "The board is full. Clear it to keep drawing." : ""}
      </div>
      {limitReached && (
        <p className="text-muted-foreground border-border border-t px-4 py-2 text-[0.8125rem]" data-slot="canvas-full-notice">
          The board is full. Clear it to keep drawing.
        </p>
      )}
      {configCallerCanDraw && !sender && (
        <p className="text-muted-foreground border-border border-t px-4 py-2 text-[0.8125rem]" data-slot="canvas-offline-notice">
          Drawing is available during a call.
        </p>
      )}
    </BlockFrame>
  );
}

export default CanvasBlock;
