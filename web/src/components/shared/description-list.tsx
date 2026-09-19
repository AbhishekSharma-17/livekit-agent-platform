import * as React from "react";

import { cn } from "@/lib/utils";

export interface DescriptionItem {
  term: React.ReactNode;
  detail: React.ReactNode;
  /** Render the detail in Geist Mono with tabular numbers (ids, models, counts). */
  mono?: boolean;
}

export interface DescriptionListProps {
  items: DescriptionItem[];
  /** Columns at ≥ 768 px (always 1 below). Default 1. */
  columns?: 1 | 2 | 3;
  className?: string;
}

const COLUMN_CLASSES: Record<1 | 2 | 3, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 md:grid-cols-2",
  3: "grid-cols-1 md:grid-cols-3",
};

/** Term/detail pairs (docs/UI_UX_SPEC.md §2.7); replaces the old `Stat` boxes. */
export function DescriptionList({ items, columns = 1, className }: DescriptionListProps) {
  return (
    <dl data-slot="description-list" className={cn("grid gap-x-6 gap-y-4", COLUMN_CLASSES[columns], className)}>
      {items.map((item, index) => (
        <div key={index} className="flex min-w-0 flex-col gap-1">
          <dt className="text-xs font-medium text-muted-foreground">{item.term}</dt>
          <dd
            className={cn(
              "min-w-0 text-sm break-words text-foreground",
              item.mono && "font-mono text-[0.8125rem] tabular-nums",
            )}
          >
            {item.detail}
          </dd>
        </div>
      ))}
    </dl>
  );
}
