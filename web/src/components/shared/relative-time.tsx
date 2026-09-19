"use client";

import * as React from "react";

import { formatDateTime, formatRelative, toMillis } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface RelativeTimeProps {
  /** ISO string (epoch seconds/ms also accepted, see `toMillis`). */
  iso: string | number;
  /** Show "19 Sep, 03:04 (4 min ago)" instead of "4 min ago". */
  withExact?: boolean;
  className?: string;
}

const TICK_MS = 30_000;

/**
 * "4 min ago" with the exact time in `title` (docs/UI_UX_SPEC.md §2.7). The
 * first render (server + hydration) shows the exact time; the relative phrase
 * swaps in after mount so server and client markup always agree.
 */
export function RelativeTime({ iso, withExact = false, className }: RelativeTimeProps) {
  const [now, setNow] = React.useState<number | null>(null);

  React.useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const ms = toMillis(iso);
  if (Number.isNaN(ms)) {
    return <span className={className}>—</span>;
  }

  const exact = formatDateTime(ms);
  const relative = now === null ? null : formatRelative(ms, now);
  let text: string;
  if (relative === null) text = exact;
  else if (withExact && relative !== exact) text = `${exact} (${relative})`;
  else text = relative;

  return (
    <time
      dateTime={new Date(ms).toISOString()}
      title={exact}
      data-slot="relative-time"
      suppressHydrationWarning
      className={cn("tabular-nums", className)}
    >
      {text}
    </time>
  );
}
