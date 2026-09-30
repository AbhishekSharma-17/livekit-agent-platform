"use client"

import * as React from "react"
import { Switch as SwitchPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * Switch (docs/ui/DESIGN-SYSTEM.md section 6.2): a 34 × 20 pill track
 * (`--muted-strong`, `--brand` when on) with a 16 px thumb that slides 14 px
 * over 150 ms. The off track keeps an `--input` edge so the control stays
 * visible at 3:1. `sm` is 26 × 16 for dense rows.
 */
function Switch({
  className,
  size = "default",
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & {
  size?: "sm" | "default"
}) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      data-size={size}
      className={cn(
        "peer group/switch relative inline-flex shrink-0 items-center rounded-pill border transition-colors duration-(--duration-base) after:absolute after:-inset-x-3 after:-inset-y-2 aria-invalid:border-destructive-solid data-[size=default]:h-5 data-[size=default]:w-[34px] data-[size=sm]:h-4 data-[size=sm]:w-[26px] data-checked:border-brand data-checked:bg-brand data-unchecked:border-input data-unchecked:bg-muted-strong data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block translate-x-px rounded-pill bg-card shadow-raised transition-transform duration-(--duration-base) ease-entrance group-data-[size=default]/switch:size-4 group-data-[size=sm]/switch:size-3 group-data-[size=default]/switch:data-checked:translate-x-[15px] group-data-[size=sm]/switch:data-checked:translate-x-[11px] dark:data-checked:bg-brand-foreground dark:data-unchecked:bg-foreground"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
