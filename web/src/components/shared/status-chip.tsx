import * as React from "react";

import { cn } from "@/lib/utils";

import { lifecycleStatus, type StatusTone } from "./status-map";

export type { StatusTone } from "./status-map";

export interface StatusPillProps {
  tone: StatusTone;
  /** `sm` 20 px for dense rows; `md` (default) 22 px. */
  size?: "sm" | "md";
  children: React.ReactNode;
  className?: string;
}

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "border-border bg-muted text-text-secondary",
  info: "border-info-border bg-info-subtle text-info-text",
  success: "border-success-border bg-success-subtle text-success-text",
  warning: "border-warning-border bg-warning-subtle text-warning-text",
  danger: "border-destructive-border bg-destructive-subtle text-destructive-text",
  live: "border-brand-border bg-brand-subtle text-brand",
};

/**
 * Status pill (docs/ui/DESIGN-SYSTEM.md section 6.6): 22 px, padding 0 8 px,
 * 12 px / 500, a hairline border, a pill radius and always a 6 px
 * `currentColor` dot, so status is a word plus a tone, never colour alone.
 * `live` is the brand tone with a pulsing dot (still under reduced motion).
 * Map api states with `lifecycleStatus()` or use `LifecycleBadge`.
 */
export function StatusPill({ tone, size = "md", children, className }: StatusPillProps) {
  return (
    <span
      data-slot="status-chip"
      data-tone={tone}
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1.5 rounded-pill border px-2 text-caption leading-none font-medium whitespace-nowrap",
        size === "sm" ? "h-5" : "h-[22px]",
        TONE_CLASSES[tone],
        className,
      )}
    >
      <span
        aria-hidden="true"
        data-slot="status-dot"
        data-pulse={tone === "live" ? "" : undefined}
        className="size-1.5 shrink-0 rounded-pill bg-current"
      />
      {children}
    </span>
  );
}

export interface StatusChipProps extends StatusPillProps {
  /** @deprecated Status pills always show their dot (spec 6.6). */
  dot?: boolean;
}

/** @deprecated Use `StatusPill`; kept so existing screens keep compiling. */
export function StatusChip(props: StatusChipProps) {
  // `dot` is ignored: StatusPill always draws it.
  return <StatusPill {...props} />;
}

/**
 * A status pill straight from an api state ("indexing", "needs_reauth"):
 * the tone and human label come from the one shared lifecycle map.
 */
export function LifecycleBadge({
  state,
  label,
  size,
  className,
}: {
  state: string | null | undefined;
  /** Override the mapped label (the tone still comes from the map). */
  label?: React.ReactNode;
  size?: "sm" | "md";
  className?: string;
}) {
  const status = lifecycleStatus(state);
  return (
    <StatusPill tone={status.tone} size={size} className={className}>
      {label ?? status.label}
    </StatusPill>
  );
}
