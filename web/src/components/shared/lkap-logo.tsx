import * as React from "react";

import { cn } from "@/lib/utils";

import { LKAP_MARK } from "./lkap-logo-geometry";

export type LkapLogoSize = "sm" | "md" | "lg";

export interface LkapLogoProps {
  /** `mark` is the tile alone. `full` adds the "LKAP" wordmark. Default `full`. */
  variant?: "mark" | "full";
  /** `sm` 20 px mark with 14 px type, `md` 28 px with 15 px, `lg` 40 px with 22 px. Default `md`. */
  size?: LkapLogoSize;
  /**
   * A product name after the wordmark in secondary text ("Console"). It joins
   * the accessible name ("LKAP Console").
   */
  product?: string;
  /**
   * Classes for the wordmark and product text, for example
   * `group-data-[state=collapsed]/sidebar:sr-only` to keep the words for
   * assistive tech while the collapsed rail shows only the mark.
   */
  textClassName?: string;
  /**
   * For `mark`: give the mark the name "LKAP" (an image with a label). Leave
   * it off when the mark sits beside visible text that already says LKAP, or
   * inside a link that has its own name, so the name is heard once.
   */
  labelled?: boolean;
  className?: string;
}

const MARK_SIZE: Record<LkapLogoSize, string> = {
  sm: "size-5",
  md: "size-7",
  lg: "size-10",
};
const TEXT_SIZE: Record<LkapLogoSize, string> = {
  sm: "text-body tracking-[-0.006em]",
  md: "text-title tracking-[-0.01em]",
  lg: "text-page tracking-[-0.018em]",
};
const GAP: Record<LkapLogoSize, string> = {
  sm: "gap-2",
  md: "gap-2.5",
  lg: "gap-3",
};

/**
 * The tile: `--brand` behind a `--brand-foreground` live dot and two rising
 * bars. The pair is checked by `pnpm check:contrast` at 4.5:1 in both themes,
 * above the 3:1 a graphic needs. Always decorative, the parent names it.
 */
function Mark({ size, className }: { size: LkapLogoSize; className?: string }) {
  const { size: grid, radius, dot, bars } = LKAP_MARK;
  return (
    <svg
      data-slot="lkap-mark"
      viewBox={`0 0 ${grid} ${grid}`}
      aria-hidden="true"
      focusable="false"
      className={cn("shrink-0", MARK_SIZE[size], className)}
    >
      <rect width={grid} height={grid} rx={radius} className="fill-brand" />
      <circle cx={dot.cx} cy={dot.cy} r={dot.r} className="fill-brand-foreground" />
      {bars.map((bar) => (
        <rect
          key={bar.x}
          x={bar.x}
          y={bar.y}
          width={bar.width}
          height={bar.height}
          rx={bar.width / 2}
          className="fill-brand-foreground"
        />
      ))}
    </svg>
  );
}

/**
 * Our own logo (docs/ui/DESIGN-SYSTEM.md sections 5 and 7.1). Inline SVG built
 * from the tokens, so it follows the theme with no asset to load. Hook-free,
 * so server components (home, sign-in, not-found) render it too.
 *
 * - `full`: the mark, then "LKAP" in Inter 600 and an optional product name in
 *   secondary text. One image named "LKAP" (or "LKAP Console"), so a reader
 *   hears the name once whatever the layout hides.
 * - `mark`: the tile alone, decorative unless `labelled`.
 *
 * The geometry lives in `lkap-logo-geometry.ts`, which also draws the app
 * icons, so the favicon and the in-app mark never drift apart.
 */
export function LkapLogo({ variant = "full", size = "md", product, textClassName, labelled = false, className }: LkapLogoProps) {
  if (variant === "mark") {
    const a11y = labelled ? { role: "img", "aria-label": "LKAP" } : { "aria-hidden": true as const };
    return (
      <span {...a11y} data-slot="lkap-logo" data-variant="mark" className={cn("inline-flex shrink-0", className)}>
        <Mark size={size} />
      </span>
    );
  }
  return (
    <span
      role="img"
      aria-label={product ? `LKAP ${product}` : "LKAP"}
      data-slot="lkap-logo"
      data-variant="full"
      className={cn("inline-flex min-w-0 items-center", GAP[size], className)}
    >
      <Mark size={size} />
      <span className={cn("min-w-0 truncate leading-tight", TEXT_SIZE[size], textClassName)}>
        <span className="font-semibold text-foreground">LKAP</span>
        {product ? <span className="font-normal text-text-secondary"> {product}</span> : null}
      </span>
    </span>
  );
}
