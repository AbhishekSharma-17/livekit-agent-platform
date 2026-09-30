import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

/**
 * Badge (docs/ui/DESIGN-SYSTEM.md section 6.6): 22 px, padding 0 8 px,
 * 12 px / 500, a hairline border and a pill radius, in a tone's subtle fill,
 * border and text. For **status** use `StatusPill` / `LifecycleBadge`
 * (`components/shared/status-chip`), which add the mandatory dot; for
 * freeform labels use `Tag`. Legacy `variant` names map onto the tones.
 */
const TONES = {
  neutral: "border-border bg-muted text-text-secondary",
  brand: "border-brand-border bg-brand-subtle text-brand",
  info: "border-info-border bg-info-subtle text-info-text",
  success: "border-success-border bg-success-subtle text-success-text",
  warning: "border-warning-border bg-warning-subtle text-warning-text",
  danger: "border-destructive-border bg-destructive-subtle text-destructive-text",
} as const

const badgeVariants = cva(
  "group/badge inline-flex h-[22px] w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-pill border px-2 text-caption leading-none font-medium whitespace-nowrap transition-colors duration-(--duration-fast) has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: TONES.neutral,
        secondary: TONES.neutral,
        destructive: TONES.danger,
        outline: "border-border bg-card text-foreground",
        ghost: "border-transparent bg-transparent text-text-secondary [a]:hover:bg-muted",
        link: "border-transparent bg-transparent text-brand underline-offset-3 [a]:hover:underline",
      },
      /** Token tones; when set they win over `variant`. */
      tone: TONES,
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  tone,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      data-tone={tone ?? undefined}
      className={cn(badgeVariants({ variant, tone }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
