import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Skeleton block (docs/ui/DESIGN-SYSTEM.md sections 5 and 6.5): a `--muted`
 * block with a `--muted-strong` sweep every 1.4 s (`globals.css`,
 * `[data-slot=skeleton]`; still under reduced motion). Skeletons mirror the
 * real layout; wrap them in `LoadingRegion` so screen readers hear a label.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="skeleton" aria-hidden="true" className={cn("rounded-sm", className)} {...props} />
}

/** Lines of 14 px text with a ragged right edge: the last line stops at 60 %. */
function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div data-slot="skeleton-text" className={cn("flex flex-col gap-2", className)}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className={cn("h-3.5", index === lines - 1 && lines > 1 ? "w-3/5" : index % 2 === 1 ? "w-11/12" : "w-full")}
        />
      ))}
    </div>
  )
}

export { Skeleton, SkeletonText }
