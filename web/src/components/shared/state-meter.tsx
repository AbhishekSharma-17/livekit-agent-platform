import * as React from "react";

import { cn } from "@/lib/utils";

import { STATE_LABEL, type MeterState } from "./agent-state";

export type StateMeterSize = "xs" | "sm" | "md" | "lg";

export interface StateMeterProps {
  state: MeterState;
  /** `xs` = 2-bar dot for chips; `sm`/`md` console; `lg` = session stage. Default `sm`. */
  size?: StateMeterSize;
  /** Bar count for `sm`–`lg` (default 4; 5 for `lg`). `xs` always draws 2. */
  bars?: 4 | 5;
  /** Render the state's word next to the bars. */
  label?: boolean;
  /**
   * Optional 0–1 input level (mic check, speaking on the stage). When set the
   * bars follow the level instead of the state's keyframes.
   */
  level?: number;
  className?: string;
}

/** Resting heights (scaleY) per bar count: a gentle arch, taller in the middle. */
const REST: Record<number, number[]> = {
  2: [0.55, 0.8],
  4: [0.45, 0.75, 0.75, 0.45],
  5: [0.4, 0.65, 0.85, 0.65, 0.4],
};

/** Per-bar multiplier for level-driven bars so the shape keeps its arch. */
const LEVEL_SHAPE: Record<number, number[]> = {
  2: [0.8, 1],
  4: [0.7, 1, 0.9, 0.65],
  5: [0.6, 0.85, 1, 0.8, 0.55],
};

/**
 * The signature primitive (docs/UI_UX_SPEC.md §2.1): a CSS-only bar meter
 * that renders agent state identically on every surface. Animation lives in
 * `globals.css` (`[data-slot=state-meter]`) and is removed under
 * `prefers-reduced-motion`.
 */
export function StateMeter({ state, size = "sm", bars, label = false, level, className }: StateMeterProps) {
  const count = size === "xs" ? 2 : (bars ?? (size === "lg" ? 5 : 4));
  const text = STATE_LABEL[state];
  const clamped = level === undefined ? undefined : Math.min(1, Math.max(0, level));

  const meter = (
    <span
      data-slot="state-meter"
      data-state={state}
      data-size={size}
      data-level={clamped === undefined ? undefined : ""}
      role="img"
      aria-label={text}
      className={label ? undefined : className}
    >
      {Array.from({ length: count }, (_, i) => {
        const style: React.CSSProperties & Record<string, string | number> = {
          "--i": i,
          "--rest": REST[count][i],
        };
        if (clamped !== undefined) {
          style["--level-scale"] = Math.max(0.2, clamped * LEVEL_SHAPE[count][i]);
        }
        return (
          <span
            key={i}
            data-bar=""
            data-edge={i === 0 || i === count - 1 ? "" : undefined}
            style={style}
          />
        );
      })}
    </span>
  );

  if (!label) return meter;

  return (
    <span data-slot="state-meter-labelled" className={cn("inline-flex items-center gap-2", className)}>
      {meter}
      <span
        aria-hidden="true"
        className={cn(
          "font-medium",
          size === "lg" ? "text-sm" : "text-xs",
          state === "failed" ? "text-danger-text" : "text-muted-foreground",
        )}
      >
        {text}
      </span>
    </span>
  );
}
