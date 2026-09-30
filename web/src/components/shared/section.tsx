import * as React from "react";

import { cn } from "@/lib/utils";

export interface SectionProps {
  /** Anchor id; also used to label the region. */
  id: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right side of the title row (an action, a count, a switch). */
  aside?: React.ReactNode;
  /** Rows. Direct children are separated by `divide-y` hairlines — never nest cards. */
  children: React.ReactNode;
  className?: string;
}

/**
 * The editor's card replacement (docs/UI_UX_SPEC.md §2.4, §2.7): one bordered
 * card, a title row, and a `divide-y` body. Wrap each row in `SectionRow` for
 * the standard padding.
 */
export function Section({ id, title, description, aside, children, className }: SectionProps) {
  const titleId = `${id}-title`;
  return (
    <section
      id={id}
      aria-labelledby={titleId}
      data-slot="section"
      className={cn("scroll-mt-20 rounded-lg border border-border bg-card text-foreground", className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5">
        {/* A 10rem basis: a short aside (a pill, one button) sits beside the title; a
            row of buttons drops below it on phones instead of squeezing it. */}
        <div className="min-w-0 flex-[1_1_10rem]">
          <h2 id={titleId} className="text-title font-semibold tracking-[-0.008em]">
            {title}
          </h2>
          {description ? (
            <p className="mt-0.5 max-w-[72ch] text-label text-pretty text-text-secondary">{description}</p>
          ) : null}
        </div>
        {aside ? (
          <div data-slot="section-aside" className="flex max-w-full min-w-0 flex-wrap items-center justify-end gap-2">
            {aside}
          </div>
        ) : null}
      </div>
      <div data-slot="section-body" className="divide-y divide-border">
        {children}
      </div>
    </section>
  );
}

/** A padded row inside a `Section` body (20 px default, 16 px `compact`). */
export function SectionRow({
  compact = false,
  className,
  ...props
}: React.ComponentProps<"div"> & { compact?: boolean }) {
  return (
    <div
      data-slot="section-row"
      className={cn(compact ? "px-4 py-3" : "px-5 py-4", className)}
      {...props}
    />
  );
}
