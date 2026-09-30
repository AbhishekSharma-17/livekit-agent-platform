import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export interface LoadingRegionProps {
  /** What is loading, announced to screen readers ("Loading sessions"). */
  label: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * The accessible wrapper for every skeleton (docs/ui/DESIGN-SYSTEM.md
 * section 8.1): a `status` live region whose name is real text, so the
 * skeleton blocks themselves stay decorative. An `aria-label` on a roleless
 * `div` is not announced and is flagged by axe (`aria-prohibited-attr`). No
 * `aria-busy`: several screen readers hold a busy region's announcement until
 * it clears, and this one unmounts when the data arrives.
 */
export function LoadingRegion({ label, children, className }: LoadingRegionProps) {
  return (
    <div role="status" aria-live="polite" data-slot="loading-region" className={className}>
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="contents">
        {children}
      </div>
    </div>
  );
}

export interface SkeletonRowsProps {
  label: string;
  /** Number of placeholder rows (default 3). */
  rows?: number;
  /** Row height class; fixed so data replaces rows without layout shift. */
  rowClassName?: string;
  className?: string;
}

/** A list's loading state: fixed-height rows matching the rows they replace. */
export function SkeletonRows({ label, rows = 3, rowClassName = "h-10", className }: SkeletonRowsProps) {
  return (
    <LoadingRegion label={label} className={cn("flex flex-col gap-2", className)}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={cn("w-full", rowClassName)} />
      ))}
    </LoadingRegion>
  );
}

/**
 * Loading row (section 6.5) for small in-card loads: the spinner plus a
 * 13 px secondary label, in a `role="status"` region. Never the only loading
 * state for a page; pages use skeletons that mirror their layout.
 */
export function LoadingRow({ label, className }: { label: string; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-slot="loading-row"
      className={cn("flex items-center gap-2 py-2 text-label text-text-secondary", className)}
    >
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
