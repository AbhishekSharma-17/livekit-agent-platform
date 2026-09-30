import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Card (docs/ui/DESIGN-SYSTEM.md section 6.7): a hairline border, 12 px
 * radius and **no shadow**. Header `16px 20px` over a bottom hairline (title,
 * 13 px description, right-side actions); body `16px 20px 20px`; footer on
 * `--muted` over a top hairline with right-aligned actions; horizontal
 * padding drops to 16 px on phones. `CardInset` is a `--muted` box inside a
 * card for secondary content. `size="sm"` tightens every region.
 */
function Card({ className, size = "default", ...props }: React.ComponentProps<"div"> & { size?: "default" | "sm" }) {
  return (
    <div
      data-slot="card"
      data-size={size}
      className={cn(
        "group/card flex flex-col overflow-hidden rounded-lg border border-border bg-card text-body text-foreground has-[>img:first-child]:pt-0 *:[img:first-child]:rounded-t-lg *:[img:last-child]:rounded-b-lg",
        className
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "group/card-header @container/card-header grid auto-rows-min items-start gap-1 px-4 py-4 not-last:border-b not-last:border-border has-data-[slot=card-action]:grid-cols-[minmax(0,1fr)_auto] has-data-[slot=card-description]:grid-rows-[auto_auto] sm:px-5 group-data-[size=sm]/card:px-4 group-data-[size=sm]/card:py-3",
        className
      )}
      {...props}
    />
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn(
        "font-heading text-title font-semibold tracking-[-0.008em] text-foreground group-data-[size=sm]/card:text-body",
        className
      )}
      {...props}
    />
  )
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("max-w-[72ch] text-label text-text-secondary", className)}
      {...props}
    />
  )
}

function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn("col-start-2 row-span-2 row-start-1 flex items-center gap-2 self-start justify-self-end", className)}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn(
        "px-4 pt-4 pb-5 sm:px-5 group-data-[size=sm]/card:px-4 group-data-[size=sm]/card:pt-3 group-data-[size=sm]/card:pb-4",
        className
      )}
      {...props}
    />
  )
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn(
        "flex flex-wrap items-center justify-end gap-2 border-t border-border bg-muted px-4 py-3 sm:px-5 group-data-[size=sm]/card:px-4",
        className
      )}
      {...props}
    />
  )
}

function CardInset({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-inset" className={cn("rounded bg-muted p-3 text-label", className)} {...props} />
}

export { Card, CardHeader, CardFooter, CardTitle, CardAction, CardDescription, CardContent, CardInset }
