import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Textarea (docs/ui/DESIGN-SYSTEM.md section 6.2): the input's border, fill
 * and focus treatment; resizes vertically only, line-height 1.55.
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-18 w-full resize-y rounded border border-input bg-card px-[11px] py-[7px] text-control leading-[1.55] text-foreground transition-[color,border-color,box-shadow] duration-(--duration-fast) outline-none placeholder:text-text-tertiary hover:border-border-strong focus-visible:border-brand focus-visible:shadow-focus focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-muted disabled:text-text-secondary disabled:hover:border-input aria-invalid:border-destructive-solid",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
