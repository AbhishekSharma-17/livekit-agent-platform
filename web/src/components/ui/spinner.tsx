import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Spinner (docs/ui/DESIGN-SYSTEM.md section 5): 14 px, a 2 px ring with an
 * accent top edge, 0.8 s linear rotation (keyframes in `globals.css`,
 * `[data-slot=spinner]`; stopped under reduced motion). Decorative: pair it
 * with visible text or a `role="status"` label. Never the only loading state
 * for a page, and never inside a text button (busy buttons change their label).
 */
function Spinner({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="spinner" aria-hidden="true" className={cn(className)} {...props} />
}

export { Spinner }
