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
import { cn } from "@/lib/utils";

import type { NotebookDocument } from "./state";

/** Blockers before documents, anything answered last — the demo's ordering. */
function rank(item: ChecklistItem): number {
  if (item.done) return 2;
  return item.id.startsWith("blocker:") ? 0 : 1;
}

function ringTone(progress: number): string {
  if (progress >= 80) return "var(--color-emerald-400, #34d399)";
  if (progress >= 40) return "var(--color-amber-400, #fbbf24)";
  return "var(--color-red-400, #f87171)";
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
      className={cn(
        "border-border/60 bg-card/50 rounded-xl border px-3.5 py-3",
        className,
      )}
    >
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <h3 className="text-muted-foreground text-[0.7rem] font-semibold tracking-[0.12em] uppercase">
          {title}
        </h3>
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
      <p className="text-muted-foreground/80 mb-2 text-xs">
        {value}% ready · {open} open
      </p>
      {items.length === 0 ? (
        <p className="text-muted-foreground/70 text-sm italic">
          Nothing outstanding yet.
        </p>
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
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-[5px] border text-[0.6rem] font-bold",
                    item.done
                      ? "border-emerald-400/60 bg-emerald-400/20 text-emerald-300"
                      : blocker
                        ? "border-amber-400/70 text-transparent shadow-[0_0_0_3px_rgba(251,191,36,0.12)]"
                        : "border-border text-transparent",
                  )}
                >
                  ✓
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "block text-sm leading-snug",
                      item.done && "text-muted-foreground line-through",
                    )}
                  >
                    {item.label}
                    {!item.done && (blocker || priority) && (
                      <span
                        className={cn(
                          "ml-1.5 align-middle text-[0.62rem] font-semibold tracking-wide uppercase",
                          blocker || priority === "required"
                            ? "text-amber-300"
                            : "text-muted-foreground",
                        )}
                      >
                        {blocker ? "blocker" : priority}
                      </span>
                    )}
                  </span>
                  {item.hint && (
                    <span className="text-muted-foreground/70 mt-0.5 block text-[0.7rem]">
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

const PHASE_DOT: Record<ActivityEvent["phase"], string> = {
  running: "bg-sky-400 animate-pulse",
  done: "bg-emerald-400",
  error: "bg-red-400",
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
        <p className="text-muted-foreground/70 text-sm italic">
          The team is waiting for the first detail.
        </p>
      ) : (
        <ul className="space-y-2" data-testid="notebook-team-feed">
          {rows.map((event) => (
            <li key={event.id} className="flex items-start gap-2.5">
              <span
                aria-hidden
                className={cn(
                  "mt-1.5 size-1.5 shrink-0 rounded-full",
                  PHASE_DOT[event.phase],
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm leading-snug break-words">
                  <span className="font-medium">{event.label}</span>
                  {event.headline ? ` · ${event.headline}` : ""}
                </span>
                <span
                  className={cn(
                    "mt-0.5 block text-[0.7rem]",
                    event.urgent ? "text-red-300" : "text-muted-foreground/70",
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
