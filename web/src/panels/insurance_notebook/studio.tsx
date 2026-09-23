"use client";

/**
 * The desk beside the notebook: the "still needed" list with its % ready ring
 * and the claim team's activity feed — ports of the demo's `renderNeeded` and
 * the tool feed, restyled as cards that sit on the dark session surface rather
 * than on the paper.
 */
import * as React from "react";
import { useMemo } from "react";

import type { ActivityEvent, ChecklistItem } from "@/contracts/lkap-contracts";
import { StateMeter } from "@/components/shared/state-meter";
import { StatusChip } from "@/components/shared/status-chip";
import { cn } from "@/lib/utils";
import { CheckGlyph } from "@/panels/generic/blocks";

import type { NotebookDocument } from "./state";

/** Blockers before documents, anything answered last — the demo's ordering. */
function rank(item: ChecklistItem): number {
  if (item.done) return 2;
  return item.id.startsWith("blocker:") ? 0 : 1;
}

/**
 * The ring reads the semantic tone tokens (§2.2), never a palette colour.
 *
 * It references `--success`/`--warning`/`--danger` and not Tailwind's
 * `--color-*` aliases: `@theme inline` emits those on :root with the light
 * values only, so they would not follow the dark session surface.
 */
function ringTone(progress: number): string {
  if (progress >= 80) return "var(--success)";
  if (progress >= 40) return "var(--warning)";
  return "var(--danger)";
}

export function Card({
  title,
  aside,
  children,
  className,
}: {
  title: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn("border-border bg-card rounded-lg border px-4 py-3.5", className)}
    >
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/**
 * The "still needed" card.
 *
 * `checklist` carries `blocker:*` (missing facts) and `doc:*` (documents) ids;
 * `documents` is joined back in by label to recover the pack's
 * required/recommended priority, which the platform envelope does not carry.
 */
export function StillNeeded({
  checklist,
  progress,
  documents,
}: {
  checklist: ChecklistItem[];
  progress: number | null | undefined;
  documents: NotebookDocument[];
}) {
  const value = Math.max(0, Math.min(100, Math.round(progress ?? 0)));
  const items = useMemo(
    () => [...checklist].sort((a, b) => rank(a) - rank(b)),
    [checklist],
  );
  const priorities = useMemo(() => {
    const map = new Map<string, string>();
    for (const doc of documents) map.set(doc.item, doc.priority);
    return map;
  }, [documents]);
  const open = items.filter((item) => !item.done).length;

  return (
    <Card
      title="Still needed"
      aside={
        <div
          className="ring text-foreground"
          role="progressbar"
          aria-label="Claim readiness"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={100}
          style={
            {
              "--value": value,
              "--ring-tone": ringTone(value),
            } as React.CSSProperties
          }
        >
          <span>{value}%</span>
        </div>
      }
    >
      <p className="text-muted-foreground mb-2 text-xs">
        {value}% ready · {open} open
      </p>
      {items.length === 0 ? (
        <p className="text-muted-foreground text-sm">Nothing outstanding yet.</p>
      ) : (
        <ul className="space-y-1.5" data-testid="notebook-still-needed">
          {items.map((item) => {
            const blocker = item.id.startsWith("blocker:");
            const priority = priorities.get(item.label);
            return (
              <li
                key={item.id}
                data-done={item.done ? "true" : "false"}
                className="flex items-start gap-2.5"
              >
                <CheckGlyph
                  done={item.done ?? false}
                  className={cn(!item.done && blocker && "border-warning")}
                />
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "block text-sm leading-snug",
                      item.done && "text-muted-foreground line-through",
                    )}
                  >
                    {item.label}
                    {!item.done && (blocker || priority) && (
                      // The pack writes these words ("required", "recommended");
                      // the chip sentence-cases them without rewriting the data.
                      <StatusChip
                        tone={blocker || priority === "required" ? "warning" : "neutral"}
                        size="sm"
                        className="ml-1.5 align-middle capitalize"
                      >
                        {blocker ? "blocker" : priority}
                      </StatusChip>
                    )}
                  </span>
                  {item.hint && (
                    <span className="text-muted-foreground mt-0.5 block text-xs">
                      {item.hint}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** Phase → dot token; a running row gets the state meter instead (§5.5). */
const PHASE_DOT: Record<Exclude<ActivityEvent["phase"], "running">, string> = {
  done: "bg-success",
  error: "bg-danger",
  cancelled: "bg-muted-foreground/50",
};

/** The demo's right-hand meta column: working / ms / interrupted the agent. */
function meta(event: ActivityEvent): string {
  if (event.phase === "running") return "working";
  if (event.urgent) return "interrupted the agent";
  if (event.phase === "error") return "failed";
  if (event.phase === "cancelled") return "cancelled";
  if (typeof event.duration_ms === "number") {
    return `${Math.round(event.duration_ms)} ms`;
  }
  return "";
}

/** "Claim team" activity: one row per tool run, newest first. */
export function TeamFeed({
  activity,
  limit = 8,
}: {
  activity: ActivityEvent[];
  limit?: number;
}) {
  const rows = useMemo(
    () => [...activity].sort((a, b) => b.ts - a.ts).slice(0, limit),
    [activity, limit],
  );

  return (
    <Card title="Claim team">
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          The team is waiting for the first detail.
        </p>
      ) : (
        <ul className="space-y-2" data-testid="notebook-team-feed">
          {rows.map((event) => (
            <li key={event.id} data-phase={event.phase} className="flex items-start gap-2.5">
              {event.phase === "running" ? (
                // The meta line already says "working"; the meter is decoration.
                <span aria-hidden className="mt-1 inline-flex shrink-0">
                  <StateMeter state="thinking" size="xs" />
                </span>
              ) : (
                <span
                  aria-hidden
                  className={cn(
                    "mt-1.5 size-1.5 shrink-0 rounded-full",
                    PHASE_DOT[event.phase],
                  )}
                />
              )}
              <span className="min-w-0 flex-1">
                <span className="block text-sm leading-snug break-words">
                  <span className="font-medium">{event.label}</span>
                  {event.headline ? ` · ${event.headline}` : ""}
                </span>
                <span
                  className={cn(
                    "mt-0.5 block text-xs",
                    event.urgent ? "text-danger-text" : "text-muted-foreground",
                  )}
                >
                  {meta(event)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
