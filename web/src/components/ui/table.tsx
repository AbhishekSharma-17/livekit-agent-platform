"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * The container scrolls sideways when a table is wider than its column; only
 * then does it join the tab order, so keyboard users can scroll it (axe
 * `scrollable-region-focusable`) without every fitting table adding a stop.
 */
function useHorizontallyScrollable(ref: React.RefObject<HTMLDivElement | null>) {
  const [scrollable, setScrollable] = React.useState(false)
  React.useEffect(() => {
    const node = ref.current
    if (!node || typeof ResizeObserver === "undefined") return
    const measure = () => setScrollable(node.scrollWidth > node.clientWidth + 1)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    if (node.firstElementChild) observer.observe(node.firstElementChild)
    return () => observer.disconnect()
  }, [ref])
  return scrollable
}

/**
 * Table (docs/ui/DESIGN-SYSTEM.md section 6.7): 36 px headers on `--muted`
 * in 12 px / 500 secondary, cells padded `10px 14px`, hairlines between rows
 * and a `--muted` row hover. Number columns: add `numeric` to the head and
 * cell (right-aligned, tabular figures). `framed` puts the table in the
 * spec's bordered, rounded wrapper that scrolls horizontally; leave it off
 * when a card already frames the table.
 */
function Table({ className, framed = false, ...props }: React.ComponentProps<"table"> & { framed?: boolean }) {
  const containerRef = React.useRef<HTMLDivElement>(null)
  const scrollable = useHorizontallyScrollable(containerRef)
  return (
    <div
      ref={containerRef}
      data-slot="table-container"
      data-framed={framed ? "" : undefined}
      tabIndex={scrollable ? 0 : undefined}
      className={cn(
        "relative w-full overflow-x-auto rounded-sm",
        framed && "rounded-lg border border-border bg-card"
      )}
    >
      <table data-slot="table" className={cn("w-full caption-bottom text-label", className)} {...props} />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t border-border bg-muted font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-border transition-colors duration-(--duration-fast) hover:bg-muted has-aria-expanded:bg-muted data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, numeric = false, ...props }: React.ComponentProps<"th"> & { numeric?: boolean }) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-9 bg-muted px-3.5 text-left align-middle text-caption font-medium whitespace-nowrap text-text-secondary [&:has([role=checkbox])]:pr-0",
        numeric && "text-right",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, numeric = false, ...props }: React.ComponentProps<"td"> & { numeric?: boolean }) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-3.5 py-2.5 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        numeric && "text-right tabular-nums",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-label text-text-secondary", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
