"use client";

/**
 * Panel blocks — the visual reference for the v2 `status` / `notes` /
 * `checklist` / `activity` blocks (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §3,
 * docs/UI_UX_SPEC.md §5.5).
 *
 * Each block is a pure renderer of one slot of the `UiState` envelope: it
 * takes the slot's data and nothing else (no room, no `perform`, no context),
 * so the generic panel, a pack panel and the v2 panel composer can all render
 * the same block from the same props.
 *
 * House rules that apply to every block here:
 * - tokens only (no `emerald-*`/`sky-*`/`amber-*`/`red-*`/`blue-*`);
 * - sentence-case `h3` titles, never uppercase tracked labels;
 * - no bordered card inside the panel column (cards do not nest, §2.4) —
 *   blocks are separated by the hairline `PanelBlock` draws;
 * - shared primitives are imported per file, never through the barrel and
 *   never from `components/console/**` (the session bundle rule, §7.0).
 */
import * as React from "react";

import { CheckIcon } from "lucide-react";

import type {
  ActivityEvent,
  AssetRef,
  ChecklistItem,
  Note,
  StatusStamp,
} from "@/contracts/lkap-contracts";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The envelope's tone vocabulary (`StatusStamp.tone`, `Note.tone`). */
export type PanelTone = NonNullable<StatusStamp["tone"]>;

/** Envelope tone → `StatusChip` tone. They share every name. */
const CHIP_TONE: Record<PanelTone, StatusTone> = {
  neutral: "neutral",
  info: "info",
  success: "success",
  warning: "warning",
  danger: "danger",
};

/** Solid token per tone, for the leading dots. */
const TONE_DOT: Record<PanelTone, string> = {
  neutral: "bg-muted-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
};

const PHASE_LABEL: Record<ActivityEvent["phase"], string> = {
  running: "Working",
  done: "Done",
  error: "Failed",
  cancelled: "Cancelled",
};

/** Phase → dot token for everything the meter does not draw. */
const PHASE_DOT: Record<Exclude<ActivityEvent["phase"], "running">, string> = {
  done: "bg-success",
  error: "bg-danger",
  cancelled: "bg-muted-foreground/50",
};

/**
 * One block of a panel: sentence-case `h3`, optional count, hairline above.
 *
 * Not the shared `Section` primitive on purpose — that one is a bordered card,
 * and the panel column is itself a card (§2.4, cards do not nest).
 */
export function PanelBlock({
  title,
  count,
  children,
  className,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      data-slot="panel-block"
      className={cn("border-border border-t px-4 py-4 first:border-t-0", className)}
    >
      <h3 className="mb-2.5 flex items-center gap-2 text-sm font-semibold">
        {title}
        {count !== undefined && count > 0 && (
          <span className="bg-muted text-muted-foreground rounded-xs px-1.5 py-px text-xs font-medium tabular-nums">
            {count}
          </span>
        )}
      </h3>
      {children}
    </section>
  );
}

/** In-block empty line (§6: one sentence, no dashed box). */
export function PanelEmpty({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground text-sm">{children}</p>;
}

/**
 * The checklist's box glyph.
 *
 * Deliberately not the Radix `Checkbox`: a checklist item is agent-owned
 * state, not a control the viewer can toggle, so it must not be focusable or
 * announced as a checkbox. The look matches `components/ui/checkbox.tsx`.
 */
export function CheckGlyph({ done, className }: { done: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-slot="check-glyph"
      data-done={done ? "true" : "false"}
      className={cn(
        "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
        done ? "bg-primary text-primary-foreground border-primary" : "border-input text-transparent",
        className,
      )}
    >
      <Icon as={CheckIcon} size="sm" className="size-3" />
    </span>
  );
}

/**
 * **Status block** — the stamp and the progress bar.
 *
 * Progress is always the brand bar (§5.5): one accent, one meaning. It
 * animates with `transform` (§2.5 bans animating layout properties).
 */
export function StatusBlock({
  status,
  progress,
  label = "Session progress",
  className,
}: {
  status?: StatusStamp | null;
  progress?: number | null;
  /** Accessible name of the progress bar. */
  label?: string;
  className?: string;
}) {
  const tone: PanelTone = status?.tone ?? "neutral";
  const hasProgress = typeof progress === "number";
  const pct = hasProgress ? Math.max(0, Math.min(100, Math.round(progress))) : 0;

  return (
    <div data-slot="panel-status" className={cn("px-4 pt-4 pb-1", className)}>
      <div className="flex items-center justify-between gap-3">
        {/* StatusChip emits `data-tone` itself, from the same vocabulary. */}
        <StatusChip tone={CHIP_TONE[tone]} dot>
          {status?.label ?? "Not started"}
        </StatusChip>
        {hasProgress && <span className="text-muted-foreground text-xs tabular-nums">{pct}%</span>}
      </div>
      {hasProgress && (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          className="bg-muted mt-3 h-1 w-full overflow-hidden rounded-full"
        >
          <div
            data-slot="panel-progress-fill"
            className="bg-brand h-full origin-left rounded-full transition-transform duration-(--dur-3) ease-out"
            style={{ transform: `scaleX(${pct / 100})` }}
          />
        </div>
      )}
    </div>
  );
}

/** One note line: tone dot, text, `kind · time` caption. */
export function NoteRow({ note }: { note: Note }) {
  const tone: PanelTone = note.tone ?? "neutral";
  return (
    <li data-slot="panel-note" data-tone={tone} className="flex gap-2.5">
      <span aria-hidden="true" className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", TONE_DOT[tone])} />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug break-words">{note.text}</p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {note.kind && note.kind !== "note" ? `${note.kind} · ` : ""}
          {formatTime(note.ts)}
        </p>
      </div>
    </li>
  );
}

/** **Notes block** — what the agent has written down, in envelope order. */
export function NotesBlock({
  notes,
  empty = "Nothing noted yet.",
}: {
  notes: Note[];
  empty?: React.ReactNode;
}) {
  if (notes.length === 0) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <ul data-slot="panel-notes" className="space-y-2.5">
      {notes.map((note) => (
        <NoteRow key={note.key ?? note.id} note={note} />
      ))}
    </ul>
  );
}

/** One checklist row: glyph, label, "Required" chip for a blocking item, hint. */
export function ChecklistRow({ item }: { item: ChecklistItem }) {
  const done = item.done ?? false;
  return (
    <li
      data-slot="panel-checklist-item"
      data-done={done ? "true" : "false"}
      className="flex items-start gap-2.5"
    >
      <CheckGlyph done={done} />
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm leading-snug", done && "text-muted-foreground line-through")}>
          {item.label}
          {item.blocking && !done && (
            <StatusChip tone="warning" size="sm" className="ml-1.5 align-middle">
              Required
            </StatusChip>
          )}
        </p>
        {item.hint && <p className="text-muted-foreground mt-0.5 text-xs">{item.hint}</p>}
      </div>
    </li>
  );
}

/** **Checklist block** — what the agent still needs. */
export function ChecklistBlock({
  items,
  empty = "No open items.",
}: {
  items: ChecklistItem[];
  empty?: React.ReactNode;
}) {
  if (items.length === 0) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <ul data-slot="panel-checklist" className="space-y-2">
      {items.map((item) => (
        <ChecklistRow key={item.id} item={item} />
      ))}
    </ul>
  );
}

/** One attachment tile; `url` is the object URL once the bytes have arrived. */
export function AssetTile({ asset, url }: { asset: AssetRef; url?: string }) {
  const isImage = asset.mime.startsWith("image/");
  return (
    <figure
      data-slot="panel-asset"
      className="border-border bg-muted/30 overflow-hidden rounded-md border"
    >
      <div className="bg-muted/50 flex aspect-4/3 items-center justify-center">
        {url && isImage ? (
          /* eslint-disable-next-line @next/next/no-img-element -- blob: object URL from the lkap.ui.asset byte stream */
          <img
            src={url}
            alt={asset.caption ?? `${asset.kind} asset`}
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="text-muted-foreground px-2 text-center text-xs">
            {url ? asset.mime : "Receiving…"}
          </span>
        )}
      </div>
      {asset.caption && (
        <figcaption className="text-muted-foreground px-2 py-1.5 text-xs leading-snug">
          {asset.caption}
        </figcaption>
      )}
    </figure>
  );
}

/** **Assets block** — a 2-column grid of what the agent has captured. */
export function AssetsBlock({
  assets,
  urls,
  empty = "No images or files yet.",
}: {
  assets: AssetRef[];
  urls: Map<string, string>;
  empty?: React.ReactNode;
}) {
  if (assets.length === 0) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <div data-slot="panel-assets" className="grid grid-cols-2 gap-2.5">
      {assets.map((asset) => (
        <AssetTile key={asset.asset_id} asset={asset} url={urls.get(asset.asset_id)} />
      ))}
    </div>
  );
}

/** One activity row: a running item gets the meter, everything else a dot. */
export function ActivityRow({ event }: { event: ActivityEvent }) {
  return (
    <li data-slot="panel-activity-item" data-phase={event.phase} className="flex gap-2.5">
      {event.phase === "running" ? (
        // The row text already says "Working"; the meter is decoration.
        <span aria-hidden="true" className="mt-1 inline-flex shrink-0">
          <StateMeter state="thinking" size="xs" />
        </span>
      ) : (
        <span
          aria-hidden="true"
          className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", PHASE_DOT[event.phase])}
        />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug break-words">
          {event.headline}
          {event.urgent && (
            <StatusChip tone="danger" size="sm" className="ml-1.5 align-middle">
              Urgent
            </StatusChip>
          )}
        </p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {event.label} · {PHASE_LABEL[event.phase]}
          {typeof event.duration_ms === "number" && ` · ${Math.round(event.duration_ms)} ms`}
        </p>
      </div>
    </li>
  );
}

/** Newest first; the agent keeps `activity` in arrival order (CONTRACTS §10). */
export function sortActivity(activity: ActivityEvent[]): ActivityEvent[] {
  return [...activity].sort((a, b) => b.ts - a.ts);
}

/** **Activity block** — what the agent's tools are doing, newest first. */
export function ActivityBlock({
  events,
  limit,
  empty = "The agent has not run any tools yet.",
}: {
  events: ActivityEvent[];
  /** Keep only the newest `limit` rows. */
  limit?: number;
  empty?: React.ReactNode;
}) {
  const rows = React.useMemo(() => {
    const sorted = sortActivity(events);
    return limit === undefined ? sorted : sorted.slice(0, limit);
  }, [events, limit]);

  if (rows.length === 0) return <PanelEmpty>{empty}</PanelEmpty>;
  return (
    <ul data-slot="panel-activity" className="space-y-2.5">
      {rows.map((event) => (
        <ActivityRow key={event.id} event={event} />
      ))}
    </ul>
  );
}
