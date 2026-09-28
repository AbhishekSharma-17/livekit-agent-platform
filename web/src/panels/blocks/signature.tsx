"use client";

/**
 * `signature` block — wording the caller signs by hand on screen
 * (`request_signature`, V6-23, D-V6-20, ask #215).
 *
 * The board here is **local-only**: unlike `canvas` (V6-14), a signature's
 * strokes never travel on `lkap.ui.ink` and never touch `CanvasBlockState`
 * (ask #212 — V6-23 gave the signature its own block, not a `canvas` in
 * disguise). The caller draws entirely inside this component's own React
 * state; nothing is sent until they tap Sign, at which point:
 *
 * 1. `block_submit {values: {signed: true}}` goes out through the shared
 *    `useBlockRequest` hook, the same as every other requestable block.
 * 2. The worker's `request_signature` tool then asks this page for the
 *    picture through the **same** snapshot path `canvas`'s `read_canvas`
 *    uses (`UiRequest.method="snapshot"`, `composite/requests.ts`): this
 *    component answers it by rasterising its own local strokes (never the
 *    (non-existent) block state) into a PNG of at most `MAX_SIGNATURE_BYTES`
 *    (1 MiB) and streaming it on `lkap.ui.upload` with `block_id`/`name`.
 * 3. Only once that upload lands does the worker settle the block — `signed`,
 *    `asset_id`, `at`, `text_hash` — and this component shows "Signed".
 *
 * "Not now" skips step 2 entirely (`{signed: false}`; nothing is asked for).
 * A `cancelled` status (barge-in, timeout, or a tap that never produced a
 * picture) offers nothing to press, matching `consent.tsx`'s dismissed state.
 */
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMaybeRoomContext } from "@livekit/components-react";

import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { SignatureBlockState } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";
import { normalizePoint, pointFromClient, toBoardPixels, type InkPoint } from "@/panels/blocks/canvas/geometry";
import { strokePathD, preloadFreehand } from "@/panels/blocks/canvas/freehand";
import { rasteriseBoard, type RasterStroke } from "@/panels/blocks/canvas/rasterise";
import { subscribeCompositeRequests } from "@/panels/composite/requests";
import { sendUploadFile, type UploadRoom } from "@/panels/composite/upload";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const PAD_WIDTH = 640;
const PAD_HEIGHT = 220;
const INK_COLOR = "#1f2937";
const STROKE_WIDTH = 3;
/** Mirrors `lkap_contracts.ui_protocol.MAX_SIGNATURE_BYTES` (1 MiB) — not exported to TS. */
const MAX_SIGNATURE_BYTES = 1024 * 1024;

interface LocalStroke {
  id: number;
  points: InkPoint[];
}

/** One downscale retry when the PNG lands over the 1 MiB cap, mirroring `canvas.tsx`. */
async function rasteriseSignatureWithinBudget(strokes: RasterStroke[]): Promise<Blob | null> {
  const board = { width: PAD_WIDTH, height: PAD_HEIGHT, background: { kind: "none" as const }, strokes, shapes: [] };
  let blob = await rasteriseBoard(board);
  if (blob && blob.size > MAX_SIGNATURE_BYTES) {
    const scale = 0.6;
    blob = await rasteriseBoard({ ...board, width: Math.round(board.width * scale), height: Math.round(board.height * scale) });
  }
  return blob;
}

/**
 * The signing pad itself: pointer input collected into local strokes only —
 * never sent anywhere. `onStrokesChange` hands the latest strokes up so the
 * parent can answer a snapshot request at any moment, including right after
 * the pointer lifts and before React has re-rendered.
 */
function SignaturePad({
  disabled,
  onStrokesChange,
}: {
  disabled: boolean;
  onStrokesChange: (strokes: LocalStroke[]) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [strokes, setStrokes] = useState<LocalStroke[]>([]);
  const drawingRef = useRef<LocalStroke | null>(null);
  const nextId = useRef(0);
  const toPixels = useMemo(() => (point: InkPoint) => toBoardPixels(point, PAD_WIDTH, PAD_HEIGHT), []);

  useEffect(() => {
    void preloadFreehand();
  }, []);

  useEffect(() => {
    onStrokesChange(strokes);
  }, [strokes, onStrokesChange]);

  function rectOf(): DOMRect {
    return svgRef.current?.getBoundingClientRect() ?? new DOMRect(0, 0, 0, 0);
  }

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (disabled) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const point = normalizePoint(pointFromClient(event.clientX, event.clientY, rectOf()));
    drawingRef.current = { id: nextId.current++, points: [point] };
    setStrokes((current) => [...current, drawingRef.current!]);
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>) {
    const drawing = drawingRef.current;
    if (!drawing) return;
    const point = normalizePoint(pointFromClient(event.clientX, event.clientY, rectOf()));
    drawing.points.push(point);
    setStrokes((current) => current.map((s) => (s.id === drawing.id ? { ...drawing, points: [...drawing.points] } : s)));
  }

  function endStroke() {
    drawingRef.current = null;
  }

  function clear() {
    if (disabled) return;
    setStrokes([]);
  }

  return (
    <div data-slot="signature-pad" className="flex flex-col gap-2">
      <div className="border-border bg-card relative w-full overflow-hidden rounded-md border" style={{ aspectRatio: `${PAD_WIDTH} / ${PAD_HEIGHT}` }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${PAD_WIDTH} ${PAD_HEIGHT}`}
          role="img"
          aria-label={disabled ? "Signature" : "Sign here with your mouse, finger or stylus"}
          data-slot="signature-svg"
          className="absolute inset-0 size-full"
          style={{ touchAction: disabled ? undefined : "none", pointerEvents: disabled ? "none" : "auto" }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endStroke}
          onPointerCancel={endStroke}
        >
          <line
            x1={PAD_WIDTH * 0.05}
            y1={PAD_HEIGHT * 0.8}
            x2={PAD_WIDTH * 0.95}
            y2={PAD_HEIGHT * 0.8}
            stroke="#9ca3af"
            strokeWidth={1.5}
            strokeDasharray="6 4"
          />
          {strokes.map((stroke) => (
            <path
              key={stroke.id}
              d={strokePathD(stroke.points, toPixels, STROKE_WIDTH)}
              fill={INK_COLOR}
              data-slot="signature-stroke"
            />
          ))}
        </svg>
      </div>
      {!disabled && (
        <div>
          <Button type="button" size="sm" variant="ghost" disabled={strokes.length === 0} onClick={clear}>
            Clear
          </Button>
        </div>
      )}
    </div>
  );
}

function SignatureQuestion({
  blockId,
  text,
  allowDecline,
  perform,
  uploadRoom,
}: {
  blockId: string;
  text: string;
  allowDecline: boolean;
  perform: BlockRenderProps["panel"]["perform"];
  uploadRoom: UploadRoom | undefined;
}) {
  const { sending, error, submit } = useBlockRequest(blockId, "requested", perform);
  const busy = sending !== null;

  // Read by the snapshot subscription without re-subscribing on every stroke.
  const latestStrokes = useRef<LocalStroke[]>([]);
  const handleStrokesChange = useCallback((strokes: LocalStroke[]) => {
    latestStrokes.current = strokes;
  }, []);

  const answerSnapshot = useCallback(async () => {
    if (!uploadRoom) return;
    const rasterStrokes: RasterStroke[] = latestStrokes.current.map((stroke) => ({
      points: stroke.points,
      color: INK_COLOR,
      width: STROKE_WIDTH,
    }));
    const blob = await rasteriseSignatureWithinBudget(rasterStrokes);
    if (!blob) return;
    const file = new File([blob], `${blockId}.png`, { type: "image/png" });
    try {
      await sendUploadFile(uploadRoom, file, { blockId });
    } catch {
      // The worker's request times out and the model is told plainly — nothing more to do here.
    }
  }, [uploadRoom, blockId]);

  useEffect(
    () =>
      subscribeCompositeRequests((event) => {
        if (event.kind !== "snapshot" || event.blockId !== blockId) return false;
        void answerSnapshot();
        return true;
      }),
    [blockId, answerSnapshot],
  );

  return (
    <div data-slot="block-signature" className="flex flex-col gap-3">
      {text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{text}</p>}
      <SignaturePad disabled={busy} onStrokesChange={handleStrokesChange} />
      {error && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={() => void submit({ signed: true })}>
          {busy ? "Signing…" : "Sign"}
        </Button>
        {allowDecline && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void submit({ signed: false })}>
            Not now
          </Button>
        )}
      </div>
    </div>
  );
}

function SignedAnswer({ data, panel }: { data: SignatureBlockState; panel: BlockRenderProps["panel"] }) {
  if (data.signed === false) {
    return <StatusChip tone="neutral" size="sm">Not signed</StatusChip>;
  }
  if (data.signed !== true) {
    // Tapped Sign, but the picture never made it back (`NO_PICTURE`); the block is reset to idle
    // by the worker shortly after — a transitional message here, nothing to press.
    return <PanelEmpty>Saving your signature…</PanelEmpty>;
  }
  const url = typeof data.asset_id === "string" ? panel.assets.get(data.asset_id) : undefined;
  return (
    <div data-slot="block-signature-answered" className="flex flex-col gap-2">
      <StatusChip tone="success" size="sm" dot>
        Signed
      </StatusChip>
      {typeof data.at === "number" && <p className="text-muted-foreground text-[0.8125rem]">{formatDateTime(data.at, { seconds: true })}</p>}
      {url && (
        // eslint-disable-next-line @next/next/no-img-element -- a resolved session-asset blob URL
        <img src={url} alt="The signature" className="border-border max-h-24 w-fit rounded-md border bg-white" />
      )}
    </div>
  );
}

export function SignatureBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<SignatureBlockState>) {
  const roomContext = useMaybeRoomContext();
  const uploadRoom = roomContext as UploadRoom | undefined;
  const status = data.status ?? "idle";
  const text = typeof data.disclosure_text === "string" ? data.disclosure_text : "";
  const allowDecline = (spec.config as { allow_decline?: unknown } | null)?.allow_decline !== false;

  let body: React.ReactNode;
  if (status === "requested") {
    body = <SignatureQuestion blockId={spec.id} text={text} allowDecline={allowDecline} perform={panel.perform} uploadRoom={uploadRoom} />;
  } else if (status === "submitted") {
    body = <SignedAnswer data={data} panel={panel} />;
  } else if (status === "cancelled") {
    body = <PanelEmpty>You didn&rsquo;t finish signing. Ask to sign again if you still need to.</PanelEmpty>;
  } else {
    body = <PanelEmpty>You&rsquo;ll be asked to sign here when it&rsquo;s needed.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {body}
    </BlockFrame>
  );
}

export default SignatureBlock;
