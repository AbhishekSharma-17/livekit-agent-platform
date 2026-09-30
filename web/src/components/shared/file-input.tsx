import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * File input (docs/ui/DESIGN-SYSTEM.md section 6.2): the native control with
 * its selector button styled as a 32 px bordered secondary button, so it stays
 * keyboard- and screen-reader-correct everywhere.
 */
export function FileInput({ className, ...props }: Omit<React.ComponentProps<"input">, "type">) {
  return (
    <input
      type="file"
      data-slot="file-input"
      className={cn(
        "block w-full min-w-0 cursor-pointer text-label text-text-secondary file:mr-3 file:h-8 file:cursor-pointer file:rounded file:border file:border-border file:bg-card file:px-3 file:text-control file:font-medium file:text-foreground file:shadow-raised file:transition-colors file:duration-(--duration-fast) hover:file:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
