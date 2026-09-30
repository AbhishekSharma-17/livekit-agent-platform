"use client"

import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Tabs as TabsPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * Tabs (docs/ui/DESIGN-SYSTEM.md section 6.7) are for **sections of one
 * object or page**: 40 px text tabs over a bottom hairline, the selected tab
 * underlined 2 px in the accent, optional count pills (`TabsCount`). Vertical
 * tabs mark the selection with a 2 px accent bar on the left. For peer
 * filters or modes use `SegmentedControl` instead. `variant="line"` is the
 * old name for the same look.
 */
function Tabs({ className, orientation = "horizontal", ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      data-orientation={orientation}
      orientation={orientation}
      className={cn("group/tabs flex gap-4 data-horizontal:flex-col", className)}
      {...props}
    />
  )
}

const tabsListVariants = cva(
  "group/tabs-list flex text-text-secondary group-data-horizontal/tabs:h-10 group-data-horizontal/tabs:w-full group-data-horizontal/tabs:items-stretch group-data-horizontal/tabs:gap-5 group-data-horizontal/tabs:overflow-x-auto group-data-horizontal/tabs:border-b group-data-horizontal/tabs:border-border group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col group-data-vertical/tabs:gap-0.5",
  {
    variants: {
      variant: {
        default: "",
        line: "",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function TabsList({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> & VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  )
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "relative inline-flex shrink-0 items-center gap-1.5 text-control font-medium whitespace-nowrap text-text-secondary transition-colors duration-(--duration-fast) hover:text-foreground disabled:pointer-events-none disabled:opacity-50 data-active:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        // horizontal: the 2 px accent underline sits on the list's hairline
        "group-data-horizontal/tabs:h-10 group-data-horizontal/tabs:px-0.5 group-data-horizontal/tabs:after:absolute group-data-horizontal/tabs:after:inset-x-0 group-data-horizontal/tabs:after:-bottom-px group-data-horizontal/tabs:after:h-0.5 group-data-horizontal/tabs:after:rounded-pill group-data-horizontal/tabs:after:bg-brand group-data-horizontal/tabs:after:opacity-0 group-data-horizontal/tabs:data-active:after:opacity-100",
        // vertical: a left accent bar on a muted row
        "group-data-vertical/tabs:h-[34px] group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start group-data-vertical/tabs:rounded-sm group-data-vertical/tabs:px-3 group-data-vertical/tabs:hover:bg-muted group-data-vertical/tabs:data-active:bg-muted group-data-vertical/tabs:after:absolute group-data-vertical/tabs:after:inset-y-1.5 group-data-vertical/tabs:after:left-0 group-data-vertical/tabs:after:w-0.5 group-data-vertical/tabs:after:rounded-pill group-data-vertical/tabs:after:bg-brand group-data-vertical/tabs:after:opacity-0 group-data-vertical/tabs:data-active:after:opacity-100",
        className
      )}
      {...props}
    />
  )
}

/** A count pill beside a tab label. */
function TabsCount({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="tabs-count"
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-pill bg-muted px-1.5 text-caption leading-none font-medium text-text-secondary tabular-nums",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn("flex-1 text-body outline-none", className)} {...props} />
}

export { Tabs, TabsList, TabsTrigger, TabsContent, TabsCount, tabsListVariants }
