"use client"

import * as React from "react"
import { Progress as ProgressPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * Progress bar (docs/ui/DESIGN-SYSTEM.md section 6.5): `role="progressbar"`
 * with `aria-valuemin`, `aria-valuemax` and `aria-valuenow` (from Radix).
 * Always give it an accessible name (`aria-label` or `aria-labelledby`).
 */
function Progress({ className, value, ...props }: React.ComponentProps<typeof ProgressPrimitive.Root>) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={value}
      className={cn("relative flex h-1.5 w-full items-center overflow-hidden rounded-pill bg-muted-strong", className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className="size-full flex-1 rounded-pill bg-brand transition-transform duration-(--duration-slow) ease-entrance"
        style={{ transform: `translateX(-${100 - (value || 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
