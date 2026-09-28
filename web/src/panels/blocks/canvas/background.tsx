"use client";

/**
 * The board's background layer (V6-14, D-V6-16): `none` (blank paper — nothing rendered
 * here, the board container's own colour shows), `asset:<id>` (a picture of this session —
 * a pinned frame, a gallery picture — `panel.assets.get(id)`) or `live_camera` (the
 * caller's own camera, overlaid by the marks). Sits absolutely behind the SVG board,
 * cover-fit into it (`geometry.ts::coverFit`) — the exact same fit `rasterise.ts` uses for
 * the snapshot, so an agent's shape lines up with the picture in both.
 *
 * `onElementReady` hands the rasteriser a live reference to the actual `<img>`/`<video>`
 * element (`drawImage` reads current pixels straight off it — no extra fetch, no canvas
 * taint for a same-session asset or a local camera track).
 *
 * `live_camera`'s track resolution (`useLocalTrackRef`, which needs a `RoomContext`) is
 * confined to `LiveCameraBackground`, mounted only when `hasRoom` is true — the same
 * discipline `blocks/video.tsx` uses ("never calls a LiveKit hook" outside a room), so the
 * composer preview and the console's read-only session snapshot never call it at all.
 */
import * as React from "react";
import { useEffect, useRef } from "react";
import { VideoTrack } from "@livekit/components-react";
import { Track } from "livekit-client";

import { useLocalTrackRef } from "@/components/session/agent-stage";

export type CanvasBackgroundKind = "none" | { asset: string } | "live_camera";

/** Parses `CanvasBlockState.background` (`none | live_camera | asset:<id>`). */
export function parseBackground(value: unknown): CanvasBackgroundKind {
  if (typeof value !== "string") return "none";
  if (value === "live_camera") return "live_camera";
  if (value.startsWith("asset:")) return { asset: value.slice("asset:".length) };
  return "none";
}

type ElementReadyHandler = (element: HTMLImageElement | HTMLVideoElement | null) => void;

function CameraOffPlaceholder() {
  return (
    <div
      data-slot="canvas-background-placeholder"
      className="text-muted-foreground bg-muted/40 absolute inset-0 flex items-center justify-center text-center text-sm"
    >
      Turn on your camera to show it here.
    </div>
  );
}

function LiveCameraBackground({ onElementReady }: { onElementReady?: ElementReadyHandler }) {
  const cameraTrack = useLocalTrackRef(Track.Source.Camera);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    onElementReady?.(cameraTrack ? videoRef.current : null);
  });

  if (!cameraTrack) return <CameraOffPlaceholder />;
  return (
    <div data-slot="canvas-background-camera" className="absolute inset-0">
      <VideoTrack ref={videoRef} trackRef={cameraTrack} className="size-full object-cover" muted playsInline />
    </div>
  );
}

export interface CanvasBackgroundProps {
  background: CanvasBackgroundKind;
  /** `panel.assets`: `asset_id` → object URL (or a signed URL in the read-only session view). */
  assets: ReadonlyMap<string, string>;
  /** Whether a LiveKit room is present (see file docblock — gates the one hook call). */
  hasRoom: boolean;
  onElementReady?: ElementReadyHandler;
}

export function CanvasBackground({ background, assets, hasRoom, onElementReady }: CanvasBackgroundProps) {
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (background === "none" || background === "live_camera") return;
    onElementReady?.(imgRef.current);
  });

  if (background === "none") {
    return null;
  }
  if (background === "live_camera") {
    return hasRoom ? <LiveCameraBackground onElementReady={onElementReady} /> : <CameraOffPlaceholder />;
  }

  const url = assets.get(background.asset);
  if (!url) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- an arbitrary session asset, not a static app image.
    <img ref={imgRef} src={url} alt="" data-slot="canvas-background-asset" className="absolute inset-0 size-full object-cover" />
  );
}
