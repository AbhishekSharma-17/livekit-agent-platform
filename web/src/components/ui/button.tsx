import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"

/**
 * Buttons (docs/ui/DESIGN-SYSTEM.md section 6.1).
 *
 * Variants: `primary` (the one main action: accent fill), `secondary` (card,
 * hairline, raised shadow), `ghost`, `danger` (only inside a confirmation
 * step), `danger-outline` (the entry point to a destructive flow), and the
 * text buttons `link` (accent), `link-destructive` and `link-neutral`.
 *
 * **The default variant is secondary-looking, not the accent.** About 240
 * console buttons are variant-less today; mapping them to the accent would put
 * several accent buttons in one view (docs/ui/AUDIT.md P3). So a variant-less
 * `<Button>` renders as `secondary`, and each view marks its one main action
 * with an explicit `variant="primary"`, placed last. Legacy names are kept as
 * aliases so screens keep compiling: `default` and `outline` render as
 * `secondary`, `destructive` as `danger-outline`, `brand` as `primary`.
 * `data-variant` always reports the name the caller passed.
 *
 * Sizes: `sm` 28 px, `default` 34 px, `lg` 40 px, `icon` 34 px square,
 * `icon-md` 32 px, `icon-sm` 28 px, `block` (full width). Pressed buttons
 * scale to .98; disabled ones sit at opacity .5 with a not-allowed cursor.
 *
 * Busy state: pass `busy` (and a gerund `busyLabel`, "Saving…"). The button
 * disables itself and swaps its label; icon buttons swap their icon for the
 * spinner. Text buttons never show a spinner.
 */
const SECONDARY =
  "border-border bg-card text-foreground shadow-raised not-disabled:hover:bg-muted aria-expanded:bg-muted"
const PRIMARY =
  "bg-brand text-brand-foreground not-disabled:hover:bg-brand-hover not-disabled:active:bg-brand-active aria-expanded:bg-brand-hover"
const DANGER_OUTLINE =
  "border-destructive-border bg-card text-destructive-text not-disabled:hover:bg-destructive-subtle aria-expanded:bg-destructive-subtle"
const TEXT_BUTTON =
  "h-auto rounded-sm border-0 px-0 text-label font-medium underline-offset-3 not-disabled:hover:underline not-disabled:active:scale-100"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center gap-1.5 rounded border border-transparent text-control leading-none font-medium whitespace-nowrap transition-[color,background-color,border-color,box-shadow,opacity,transform] duration-(--duration-fast) ease-out select-none not-disabled:active:not-aria-[haspopup]:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-invalid:border-destructive-border [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        primary: PRIMARY,
        secondary: SECONDARY,
        ghost: "text-text-secondary not-disabled:hover:bg-muted not-disabled:hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground",
        danger: "bg-destructive-solid text-destructive-foreground not-disabled:hover:bg-destructive-hover aria-expanded:bg-destructive-hover",
        "danger-outline": DANGER_OUTLINE,
        link: cn(TEXT_BUTTON, "text-brand not-disabled:hover:text-brand-hover"),
        "link-destructive": cn(TEXT_BUTTON, "text-destructive-text"),
        "link-neutral": cn(TEXT_BUTTON, "text-text-secondary not-disabled:hover:text-foreground"),
        /** @deprecated Renders as `secondary`; mark the one main action `primary`. */
        default: SECONDARY,
        /** @deprecated Use `secondary`. */
        outline: SECONDARY,
        /** @deprecated Use `danger-outline` (entry point) or `danger` (inside a confirmation). */
        destructive: DANGER_OUTLINE,
        /** @deprecated Use `primary`. */
        brand: PRIMARY,
      },
      size: {
        default: "h-8.5 px-[13px] has-data-[icon=inline-end]:pr-2.5 has-data-[icon=inline-start]:pl-2.5",
        sm: "h-7 gap-1 rounded-sm px-2.5 text-label has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-10 px-[18px] text-body",
        icon: "size-8.5",
        "icon-md": "size-8",
        "icon-sm": "size-7 rounded-sm",
        block: "h-8.5 w-full px-[13px]",
        /** @deprecated 24 px is below the 28 px control floor; use `sm`. */
        xs: "h-6 gap-1 rounded-sm px-2 text-caption [&_svg:not([class*='size-'])]:size-3",
        /** @deprecated Use `icon-sm`. */
        "icon-xs": "size-6 rounded-sm [&_svg:not([class*='size-'])]:size-3",
        /** @deprecated Use `icon` (34 px). */
        "icon-lg": "size-10",
        /** The caller-facing session's Start call (decision D3). */
        xl: "h-11 gap-2 px-5 text-body has-data-[icon=inline-end]:pr-4 has-data-[icon=inline-start]:pl-4 [&_svg:not([class*='size-'])]:size-5",
      },
    },
    compoundVariants: [
      { variant: ["link", "link-destructive", "link-neutral"], className: "h-auto px-0" },
    ],
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>
type ButtonSize = NonNullable<VariantProps<typeof buttonVariants>["size"]>

const ICON_SIZES = new Set<ButtonSize>(["icon", "icon-md", "icon-sm", "icon-xs", "icon-lg"])

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
    /** Disable and show the busy label (text buttons) or the spinner (icon buttons). */
    busy?: boolean
    /** Gerund plus ellipsis, e.g. "Saving…". Defaults to the normal label. */
    busyLabel?: React.ReactNode
  }

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  busy = false,
  busyLabel,
  disabled,
  children,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot.Root : "button"
  const resolvedSize: ButtonSize = size ?? "default"
  let content = children
  if (busy && !asChild) {
    content = ICON_SIZES.has(resolvedSize) ? <Spinner /> : (busyLabel ?? children)
  }

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      data-busy={busy ? "" : undefined}
      aria-busy={busy || undefined}
      disabled={asChild ? disabled : disabled || busy}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    >
      {content}
    </Comp>
  )
}

/**
 * Icon button (section 6.1): 32 px (28 px `sm`), borderless, secondary ink,
 * `--muted` on hover. For row menus, dismiss and the hamburger. The
 * accessible name is required.
 */
function IconButton({
  label,
  size = "default",
  className,
  children,
  ...props
}: Omit<ButtonProps, "size" | "variant" | "aria-label"> & { label: string; size?: "default" | "sm" }) {
  return (
    <Button
      variant="ghost"
      size={size === "sm" ? "icon-sm" : "icon-md"}
      aria-label={label}
      className={className}
      {...props}
    >
      {children}
    </Button>
  )
}

export { Button, IconButton, buttonVariants, type ButtonProps, type ButtonVariant, type ButtonSize }
