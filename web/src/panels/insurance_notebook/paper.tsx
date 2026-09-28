"use client";

/**
 * The paper itself: the handwritten note lines, the taped pinboard, the rubber
 * stamp and the scribbling pen — a port of the Gemini demo's `renderNotes`,
 * `renderPinboard`, `renderStamp` and `setWriting`.
 *
 * Every piece is a pure function of the `UiState` envelope. The pack composes
 * the note lines server-side (`ui_state.build_ui_state`), so this module only
 * decides *how* a line is drawn, never *what* it says.
 */
import * as React from "react";
import { useEffect, useMemo, useRef } from "react";

import type {
  ActivityEvent,
  AssetRef,
  Note,
  StatusStamp,
} from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

/** Note kinds the pen knows how to draw; anything else falls back to a plain line. */
const NOTE_KINDS = new Set(["title", "check", "flag", "aside", "blank"]);

/** The six-step tilt cycle the demo used, so a frame never jitters between renders. */
const TILTS = [-2.5, 2, -1.5, 2.5, -2, 1.5];

function noteKey(note: Note): string {
  return note.key ?? note.id;
}

/**
 * The notebook page's handwriting.
 *
 * New lines (by `key`) ink in once; lines that were already on the page stay
 * put, which is what makes the notebook feel written rather than re-rendered.
 */
export function Notes({ notes }: { notes: Note[] }) {
  const seen = useRef<Set<string>>(new Set());
  const keys = useMemo(() => notes.map(noteKey), [notes]);
  const fresh = keys.map((key) => !seen.current.has(key));

  useEffect(() => {
    for (const key of keys) seen.current.add(key);
  }, [keys]);

  if (notes.length === 0) return null;

  return (
    <ul className="notes" data-testid="notebook-notes">
      {notes.map((note, index) => {
        const kind = note.kind && NOTE_KINDS.has(note.kind) ? note.kind : "";
        return (
          <li
            key={keys[index]}
            data-key={keys[index]}
            data-kind={note.kind ?? "note"}
            className={cn(
              "note",
              kind,
              note.tone === "danger" && "urgent",
              fresh[index] && "ink-in",
            )}
          >
            {note.text}
            {kind === "blank" && <span aria-hidden className="blank-line" />}
          </li>
        );
      })}
    </ul>
  );
}

function tilt(index: number): string {
  return `${TILTS[index % TILTS.length]}deg`;
}

/** `meta` is a `dict[str, str]` on the wire, so flags arrive as strings. */
function metaFlag(asset: AssetRef, name: string): boolean {
  return asset.meta?.[name] === "true";
}

function EvidenceFrame({
  asset,
  url,
  index,
}: {
  asset: AssetRef;
  url: string | undefined;
  index: number;
}) {
  const said = asset.meta?.claimant_description;
  const confirmed = metaFlag(asset, "confirmed");
  const capturedAt = asset.meta?.captured_at ?? "";
  const evidenceType = asset.meta?.evidence_type ?? "evidence";

  return (
    <figure
      className="frame"
      style={{ "--tilt": tilt(index) } as React.CSSProperties}
      data-kind="evidence"
      data-asset={asset.asset_id}
    >
      {url ? (
        /* eslint-disable-next-line @next/next/no-img-element -- blob: object URL from the lkap.ui.asset byte stream */
        <img
          src={url}
          alt={asset.caption ?? "Camera frame pinned as evidence"}
          loading="lazy"
          decoding="async"
        />
      ) : (
        <div className="missing">developing…</div>
      )}
      {asset.caption && <figcaption>{asset.caption}</figcaption>}
      {said && (
        <span className={cn("tag", !confirmed && "unconfirmed")}>
          Claimant says: {said}
          {confirmed ? ", confirmed" : ", not confirmed on camera"}
        </span>
      )}
      <span className="tag">
        Seen on camera {capturedAt} · {evidenceType}
      </span>
    </figure>
  );
}

function SketchFrame({
  asset,
  url,
  index,
}: {
  asset: AssetRef;
  url: string | undefined;
  index: number;
}) {
  const version = asset.meta?.version ?? "1";
  const confirmed = metaFlag(asset, "confirmed");

  return (
    <figure
      className="frame sketch"
      style={{ "--tilt": tilt(index + 3) } as React.CSSProperties}
      data-kind="sketch"
      data-asset={asset.asset_id}
    >
      {url ? (
        /* eslint-disable-next-line @next/next/no-img-element -- blob: object URL from the lkap.ui.asset byte stream */
        <img
          src={url}
          alt="Sketch of the incident, drawn from what the claimant described"
          loading="lazy"
          decoding="async"
        />
      ) : (
        <div className="missing">sketching…</div>
      )}
      <figcaption>{asset.caption ?? "Does this look right?"}</figcaption>
      <span className="tag">Sketch v{version}, drawn from what was described</span>
      {!confirmed && <span className="tag unconfirmed">not confirmed yet</span>}
    </figure>
  );
}

/** The taped-up camera frames and the pen sketch. */
export function Pinboard({
  assets,
  urls,
}: {
  assets: AssetRef[];
  urls: Map<string, string>;
}) {
  if (assets.length === 0) return null;

  return (
    <div className="board" data-testid="notebook-board">
      {assets.map((asset, index) =>
        asset.kind === "sketch" ? (
          <SketchFrame
            key={asset.asset_id}
            asset={asset}
            url={urls.get(asset.asset_id)}
            index={index}
          />
        ) : (
          <EvidenceFrame
            key={asset.asset_id}
            asset={asset}
            url={urls.get(asset.asset_id)}
            index={index}
          />
        ),
      )}
    </div>
  );
}

/**
 * The routing decision, stamped on the page.
 *
 * Remounting on `key` change replays the stamp animation and re-announces the
 * new route, exactly like the demo's `dataset.route` reset.
 */
export function Stamp({ status }: { status: StatusStamp | null | undefined }) {
  if (!status) return null;
  const tone = status.tone ?? "neutral";
  return (
    <div
      key={status.key ?? status.label}
      className="stamp"
      data-testid="notebook-stamp"
      data-tone={tone}
      data-route={status.key ?? ""}
    >
      {status.label}
    </div>
  );
}

/** Background work is "the claim team writing"; the policy desk is instant. */
export function isWriting(activity: ActivityEvent[]): boolean {
  return activity.some(
    (event) => event.phase === "running" && event.source !== "lookup_policy",
  );
}

/** The nib that scribbles while a background tool is running. */
export function Pen({ writing }: { writing: boolean }) {
  if (!writing) return null;
  return (
    <p className="pen" data-testid="notebook-pen">
      <span aria-hidden className="pen-nib" />
      writing…
    </p>
  );
}
