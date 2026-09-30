import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Tag (docs/ui/DESIGN-SYSTEM.md section 6.6): 22 px and squarer than a
 * badge (6 px radius), card fill, secondary text. For freeform labels such as
 * topics and dates; space them 6 px apart (`TagList`).
 */
export function Tag({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="tag"
      className={cn(
        "inline-flex h-[22px] w-fit shrink-0 items-center gap-1 rounded-sm border border-border bg-card px-2 text-caption leading-none whitespace-nowrap text-text-secondary [&>svg]:size-3",
        className,
      )}
      {...props}
    />
  );
}

/** Tags in a wrapping row with 6 px gaps. */
export function TagList({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="tag-list" className={cn("flex flex-wrap items-center gap-1.5", className)} {...props} />;
}
