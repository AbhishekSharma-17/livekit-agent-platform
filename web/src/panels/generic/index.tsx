"use client";

/**
 * Generic panel — the reference `PanelDefinition` (docs/CONTRACTS.md §11).
 *
 * Renders every slot of the platform envelope (`status`, `progress`, `notes`,
 * `checklist`, `assets`, `activity`) plus a collapsible JSON view of the
 * pack-defined `custom` object, so any pack has a usable panel before it ships
 * a bespoke one. It is a pure renderer: the only way out is `perform`.
 */
import * as React from "react";
import { useMemo } from "react";

import type {
  ActivityEvent,
  AssetRef,
  ChecklistItem,
  Note,
  StatusStamp,
} from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { PanelDefinition, PanelProps } from "@/panels/registry";

type Tone = NonNullable<StatusStamp["tone"]>;

const TONE_BADGE: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-sky-500/15 text-sky-300",
  success: "bg-emerald-500/15 text-emerald-300",
  warning: "bg-amber-500/15 text-amber-300",
  danger: "bg-red-500/15 text-red-300",
};

const TONE_RAIL: Record<Tone, string> = {
  neutral: "bg-muted-foreground/40",
  info: "bg-sky-400",
  success: "bg-emerald-400",
  warning: "bg-amber-400",
  danger: "bg-red-400",
};

const PHASE_LABEL: Record<ActivityEvent["phase"], string> = {
  running: "Working",
  done: "Done",
  error: "Failed",
  cancelled: "Cancelled",
};

const PHASE_DOT: Record<ActivityEvent["phase"], string> = {
  running: "bg-sky-400 animate-pulse",
  done: "bg-emerald-400",
  error: "bg-red-400",
  cancelled: "bg-muted-foreground/50",
};

function formatTime(ts: number): string {
  // Agent timestamps are epoch seconds (CONTRACTS §10).
  const date = new Date(ts > 1e11 ? ts : ts * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="border-border/60 border-t px-4 py-4 first:border-t-0">
      <h3 className="text-muted-foreground mb-2.5 flex items-center gap-2 text-[0.7rem] font-semibold tracking-[0.12em] uppercase">
        {title}
        {count !== undefined && count > 0 && (
          <span className="bg-muted text-muted-foreground rounded-full px-1.5 py-px text-[0.65rem] font-medium tracking-normal tabular-nums">
            {count}
          </span>
        )}
      </h3>
      {children}
    </section>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground/70 text-sm">{children}</p>;
}

function StatusHeader({
  status,
  progress,
}: {
  status: StatusStamp | null | undefined;
  progress: number | null | undefined;
}) {
  const tone: Tone = status?.tone ?? "neutral";
  const hasProgress = typeof progress === "number";
  const pct = hasProgress ? Math.max(0, Math.min(100, progress)) : 0;

  return (
    <div className="px-4 pt-4 pb-1">
      <div className="flex items-center justify-between gap-3">
        <Badge
          variant="secondary"
          data-tone={tone}
          className={cn("text-xs font-semibold", TONE_BADGE[tone])}
        >
          {status?.label ?? "Not started"}
        </Badge>
        {hasProgress && (
          <span className="text-muted-foreground text-xs tabular-nums">
            {pct}%
          </span>
        )}
      </div>
      {hasProgress && (
        <div
          role="progressbar"
          aria-label="Session progress"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          className="bg-muted mt-3 h-1.5 w-full overflow-hidden rounded-full"
        >
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500 ease-out",
              TONE_RAIL[tone],
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

function NoteRow({ note }: { note: Note }) {
  const tone: Tone = note.tone ?? "neutral";
  return (
    <li className="flex gap-2.5">
      <span
        aria-hidden
        className={cn("mt-1.5 h-2 w-0.5 shrink-0 rounded-full", TONE_RAIL[tone])}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug break-words">{note.text}</p>
        <p className="text-muted-foreground/70 mt-0.5 text-[0.7rem]">
          {note.kind && note.kind !== "note" ? `${note.kind} · ` : ""}
          {formatTime(note.ts)}
        </p>
      </div>
    </li>
  );
}

function ChecklistRow({ item }: { item: ChecklistItem }) {
  const done = item.done ?? false;
  return (
    <li className="flex items-start gap-2.5">
      <span
        aria-hidden
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-[5px] border text-[0.6rem] font-bold",
          done
            ? "border-emerald-400/50 bg-emerald-400/20 text-emerald-300"
            : item.blocking
              ? "border-amber-400/50 text-amber-300"
              : "border-border text-transparent",
        )}
      >
        ✓
      </span>
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-sm leading-snug",
            done && "text-muted-foreground line-through",
          )}
        >
          {item.label}
          {item.blocking && !done && (
            <span className="ml-1.5 align-middle text-[0.65rem] font-semibold tracking-wide text-amber-300 uppercase">
              required
            </span>
          )}
        </p>
        {item.hint && (
          <p className="text-muted-foreground/70 mt-0.5 text-[0.7rem]">
            {item.hint}
          </p>
        )}
      </div>
    </li>
  );
}

function AssetTile({ asset, url }: { asset: AssetRef; url: string | undefined }) {
  const isImage = asset.mime.startsWith("image/");
  return (
    <figure className="border-border/70 bg-muted/30 overflow-hidden rounded-lg border">
      <div className="bg-muted/50 flex aspect-4/3 items-center justify-center">
        {url && isImage ? (
          /* eslint-disable-next-line @next/next/no-img-element -- blob: object URL from the lkap.ui.asset byte stream */
          <img
            src={url}
            alt={asset.caption ?? `${asset.kind} asset`}
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="text-muted-foreground/70 px-2 text-center text-[0.7rem]">
            {url ? asset.mime : "Receiving…"}
          </span>
        )}
      </div>
      {asset.caption && (
        <figcaption className="text-muted-foreground px-2 py-1.5 text-[0.7rem] leading-snug">
          {asset.caption}
        </figcaption>
      )}
    </figure>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  return (
    <li className="flex gap-2.5">
      <span
        aria-hidden
        className={cn(
          "mt-1.5 size-1.5 shrink-0 rounded-full",
          PHASE_DOT[event.phase],
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug break-words">
          {event.headline}
          {event.urgent && (
            <span className="ml-1.5 align-middle text-[0.65rem] font-semibold tracking-wide text-red-300 uppercase">
              urgent
            </span>
          )}
        </p>
        <p className="text-muted-foreground/70 mt-0.5 text-[0.7rem]">
          {event.label} · {PHASE_LABEL[event.phase]}
          {typeof event.duration_ms === "number" &&
            ` · ${Math.round(event.duration_ms)}ms`}
        </p>
      </div>
    </li>
  );
}

export function GenericPanel({ state, assets }: PanelProps) {
  const notes = state.notes ?? [];
  const checklist = state.checklist ?? [];
  const assetRefs = state.assets ?? [];
  const custom = useMemo(() => state.custom ?? {}, [state.custom]);

  const customJson = useMemo(() => {
    try {
      return JSON.stringify(custom, null, 2);
    } catch {
      return "{}";
    }
  }, [custom]);

  // Newest first: the agent keeps `activity` in arrival order (CONTRACTS §10).
  const recentActivity = useMemo(
    () => [...(state.activity ?? [])].sort((a, b) => b.ts - a.ts),
    [state.activity],
  );

  return (
    <div
      data-testid="generic-panel"
      className="flex h-full flex-col overflow-y-auto"
    >
      <StatusHeader status={state.status} progress={state.progress} />

      <Section title="Notes" count={notes.length}>
        {notes.length === 0 ? (
          <EmptyHint>Nothing noted yet.</EmptyHint>
        ) : (
          <ul className="space-y-2.5">
            {notes.map((note) => (
              <NoteRow key={note.key ?? note.id} note={note} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Still needed" count={checklist.length}>
        {checklist.length === 0 ? (
          <EmptyHint>No open items.</EmptyHint>
        ) : (
          <ul className="space-y-2">
            {checklist.map((item) => (
              <ChecklistRow key={item.id} item={item} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Attachments" count={assetRefs.length}>
        {assetRefs.length === 0 ? (
          <EmptyHint>No images or files yet.</EmptyHint>
        ) : (
          <div className="grid grid-cols-2 gap-2.5">
            {assetRefs.map((asset) => (
              <AssetTile
                key={asset.asset_id}
                asset={asset}
                url={assets.get(asset.asset_id)}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title="Activity" count={recentActivity.length}>
        {recentActivity.length === 0 ? (
          <EmptyHint>The agent has not run any tools yet.</EmptyHint>
        ) : (
          <ul className="space-y-2.5">
            {recentActivity.map((event) => (
              <ActivityRow key={event.id} event={event} />
            ))}
          </ul>
        )}
      </Section>

      {Object.keys(custom).length > 0 && (
        <Section title="Pack data">
          <details className="group">
            <summary className="text-muted-foreground hover:text-foreground focus-visible:ring-ring cursor-pointer rounded-sm text-sm focus-visible:ring-2 focus-visible:outline-none">
              Show raw state
            </summary>
            <pre className="bg-muted/40 text-muted-foreground mt-2 max-h-72 overflow-auto rounded-md p-3 font-mono text-[0.7rem] leading-relaxed">
              {customJson}
            </pre>
          </details>
        </Section>
      )}
    </div>
  );
}

export const GENERIC_PANEL: PanelDefinition = {
  id: "generic",
  title: "Session",
  Component: GenericPanel,
  layout: "side",
};
