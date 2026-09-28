"use client";

/**
 * Pure avatar-framing math + a track-dimension hook (V6-26, PLAN-V6 §3
 * "Avatar framing"): the session stage used to draw every avatar/agent video
 * in a hard-coded 16:9 well with `object-cover`, so a portrait or square
 * avatar (many vendors stream one) had its head and shoulders cropped.
 *
 * `resolveFrame()` picks the well's aspect ratio and the video's fit/position
 * from, in order: the real track dimensions once known (ground truth) → the
 * admin's explicit `AvatarOptions.framing` (an override, e.g. for a provider
 * whose real aspect briefly disagrees with what was chosen) → the provider
 * registry's documented `avatar_aspect` (`ProviderCapabilities.avatar_aspect`,
 * a hint so the well doesn't jump before the first frame) → a 16:9 guess (the
 * pre-V6-26 assumption, kept as the last resort so nothing regresses for a
 * stored agent this package added no data for).
 *
 * `contain` is the default fit (the whole avatar visible, letterboxed on a
 * neutral fill) — `cover` is opt-in and biases its crop to the upper third so
 * a face is the last thing lost.
 */
import * as React from "react";

import type { AgentAvatarFraming } from "@/contracts/lkap-contracts";

/** `AvatarOptions.framing` (`contracts/src/lkap_contracts/agent_config.py`). */
export type AvatarFraming = "auto" | "portrait" | "landscape" | "square";

/** `AvatarOptions.fit`. */
export type AvatarFit = "contain" | "cover";

export interface VideoDimensions {
  width: number;
  height: number;
}

/** The pre-V6-26 assumption: what every avatar well used to render as. */
export const DEFAULT_ASPECT_RATIO = 16 / 9;

/** `cover`'s focal point: the upper third, so a face is the last thing a crop removes. */
export const COVER_FOCAL_POINT = "50% 33%";
const CENTER_FOCAL_POINT = "50% 50%";

const ASPECT_RATIO_FOR: Record<Exclude<AvatarFraming, "auto">, number> = {
  portrait: 9 / 16,
  square: 1,
  landscape: 16 / 9,
};

/** Classifies a measured (or declared) size the same way on every surface. */
export function classifyAspect(width: number, height: number): AvatarFraming {
  if (!width || !height) return "auto";
  const ratio = width / height;
  if (ratio < 0.9) return "portrait";
  if (ratio > 1.15) return "landscape";
  return "square";
}

export interface ResolveFrameOptions {
  /** `AvatarOptions.framing`; `undefined`/`"auto"` defers to `declaredAspect` then the guess. */
  framing?: AvatarFraming | null;
  /** `AvatarOptions.fit`; `undefined` renders like `"contain"` (the crop-free default). */
  fit?: AvatarFit | null;
  /** The provider registry's `ProviderCapabilities.avatar_aspect`, when known. */
  declaredAspect?: AvatarFraming | null;
  /** The track's real `videoWidth`/`videoHeight`, once a frame has decoded. */
  measured?: VideoDimensions;
  /** A viewer's "enlarge" tap: fills the well edge to edge regardless of the resolved fit. */
  forceCover?: boolean;
}

export interface ResolvedFrame {
  /** The well's `aspect-ratio` (always a concrete number — see module docs). */
  aspectRatio: number;
  objectFit: AvatarFit;
  objectPosition: string;
  /** `true` once a real measurement was used, i.e. this is no longer a pre-connect guess. */
  measured: boolean;
}

export function resolveFrame({
  framing,
  fit,
  declaredAspect,
  measured,
  forceCover,
}: ResolveFrameOptions): ResolvedFrame {
  const objectFit: AvatarFit = forceCover ? "cover" : (fit ?? "contain");
  const objectPosition = objectFit === "cover" ? COVER_FOCAL_POINT : CENTER_FOCAL_POINT;

  if (measured && measured.width > 0 && measured.height > 0) {
    return { aspectRatio: measured.width / measured.height, objectFit, objectPosition, measured: true };
  }
  if (framing && framing !== "auto") {
    return { aspectRatio: ASPECT_RATIO_FOR[framing], objectFit, objectPosition, measured: false };
  }
  if (declaredAspect && declaredAspect !== "auto") {
    return { aspectRatio: ASPECT_RATIO_FOR[declaredAspect], objectFit, objectPosition, measured: false };
  }
  return { aspectRatio: DEFAULT_ASPECT_RATIO, objectFit, objectPosition, measured: false };
}

/** The framing props `SessionRoom` hands to `AgentStage` → `StageView`. */
export interface StageAvatarFraming {
  framing?: AvatarFraming | null;
  fit?: AvatarFit | null;
  declaredAspect?: AvatarFraming | null;
}

/**
 * V6-26b (docs/v6/_asks.md #151): `AgentPublicOut.avatar_framing` → the stage's
 * props. `null`/absent (no avatar, or an api older than V6-26b) yields
 * `undefined`, i.e. the crop-free `auto` + `contain` default.
 */
export function stageAvatarFraming(
  value: AgentAvatarFraming | null | undefined,
): StageAvatarFraming | undefined {
  if (!value) return undefined;
  return {
    framing: value.framing ?? null,
    fit: value.fit ?? null,
    declaredAspect: value.declared_aspect ?? null,
  };
}

/** CSS `aspect-ratio` value as a string, e.g. `"1.7777777777777777"` — a number is valid CSS. */
export function aspectRatioStyle(ratio: number): string {
  return String(ratio);
}

/**
 * Measures a `<video>` element's real dimensions, updating on `loadedmetadata`
 * (the first frame) and `resize` (a provider that changes resolution
 * mid-call). Reset to `undefined` whenever `trackKey` changes, so a swapped
 * track never briefly renders at the previous one's aspect.
 */
export function useMeasuredVideoAspect(
  trackKey: unknown,
): [React.RefObject<HTMLVideoElement | null>, VideoDimensions | undefined] {
  const ref = React.useRef<HTMLVideoElement>(null);
  const [dims, setDims] = React.useState<VideoDimensions | undefined>(undefined);

  React.useEffect(() => {
    setDims(undefined);
    const el = ref.current;
    if (!el) return;
    const read = () => {
      if (el.videoWidth > 0 && el.videoHeight > 0) {
        setDims({ width: el.videoWidth, height: el.videoHeight });
      }
    };
    read();
    el.addEventListener("loadedmetadata", read);
    el.addEventListener("resize", read);
    return () => {
      el.removeEventListener("loadedmetadata", read);
      el.removeEventListener("resize", read);
    };
    // Re-attaches on track identity (`trackKey`), not on the DOM node itself.
  }, [trackKey]);

  return [ref, dims];
}
