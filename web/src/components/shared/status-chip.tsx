import * as React from "react";

import { cn } from "@/lib/utils";

import { StateMeter } from "./state-meter";

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger" | "live";

export interface StatusChipProps {
  tone: StatusTone;
  /** Leading dot in the tone's solid colour. `live` always shows its meter. */
  dot?: boolean;
  /** `sm` = micro type for dense rows; `md` (default) = caption type. */
  size?: "sm" | "md";
  children: React.ReactNode;
  className?: string;
}

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-info-soft text-info-text",
  success: "bg-success-soft text-success-text",
  warning: "bg-warning-soft text-warning-text",
  danger: "bg-danger-soft text-danger-text",
  live: "bg-brand-soft text-brand-text",
};

const DOT_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  live: "bg-brand",
};

/**
 * Status chip (docs/UI_UX_SPEC.md §2.7): square-ish, soft surface + tone
 * text. Replaces ad-hoc `Badge` for status. `tone="live"` renders a
 * `StateMeter xs` in the `listening` state (the "tally lamp on" look).
 */
export function StatusChip({ tone, dot = false, size = "md", children, className }: StatusChipProps) {
  return (
    <span
      data-slot="status-chip"
      data-tone={tone}
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1.5 rounded-xs whitespace-nowrap",
        size === "sm"
          ? "h-5 px-1.5 text-[0.6875rem] leading-[0.875rem] font-medium tracking-[0.02em]"
          : "h-6 px-2 text-xs font-medium",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {tone === "live" ? (
        <span aria-hidden="true" className="inline-flex">
          <StateMeter state="listening" size="xs" />
        </span>
      ) : dot ? (
        <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", DOT_CLASSES[tone])} />
      ) : null}
      {children}
    </span>
  );
}
