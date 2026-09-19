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
      className={cn("scroll-mt-20 rounded-lg border border-border bg-card text-card-foreground", className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em]">
            {title}
          </h2>
          {description ? (
            <p className="mt-0.5 max-w-[65ch] text-sm text-pretty text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {aside ? <div className="flex shrink-0 items-center gap-2">{aside}</div> : null}
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
