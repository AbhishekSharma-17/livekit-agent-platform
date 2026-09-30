import * as React from "react"
import { cva } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { CircleAlertIcon, CircleCheckIcon, InfoIcon, TriangleAlertIcon, type LucideIcon } from "lucide-react"

/**
 * Alert (docs/ui/DESIGN-SYSTEM.md section 6.5): padding 10 px 12 px, radius
 * 8 px, 13 px text, the tone's subtle fill, border and text colour.
 *
 * Two ways in:
 * - `tone` (preferred): the tone's icon is drawn for you, with an optional
 *   bold `title` and right-aligned `actions` (Retry …).
 * - `variant` (legacy shadcn API): the children bring their own icon.
 *
 * The role is `alert` for danger and `status` for every other tone.
 */
export type AlertTone = "info" | "success" | "warning" | "danger" | "brand" | "neutral"

const TONE_CLASSES: Record<AlertTone, string> = {
  info: "border-info-border bg-info-subtle text-info-text",
  success: "border-success-border bg-success-subtle text-success-text",
  warning: "border-warning-border bg-warning-subtle text-warning-text",
  danger: "border-destructive-border bg-destructive-subtle text-destructive-text",
  brand: "border-brand-border bg-brand-subtle text-brand",
  neutral: "border-border bg-muted text-foreground",
}

export const ALERT_TONE_ICON: Record<AlertTone, LucideIcon> = {
  info: InfoIcon,
  success: CircleCheckIcon,
  warning: TriangleAlertIcon,
  danger: CircleAlertIcon,
  brand: InfoIcon,
  neutral: InfoIcon,
}

type LegacyVariant = "default" | "destructive" | AlertTone

const LEGACY_TONE: Record<LegacyVariant, AlertTone> = {
  default: "neutral",
  destructive: "danger",
  info: "info",
  success: "success",
  warning: "warning",
  danger: "danger",
  brand: "brand",
  neutral: "neutral",
}

const alertVariants = cva(
  "group/alert relative grid w-full gap-0.5 rounded border px-3 py-2.5 text-left text-label leading-[1.45] has-[>svg]:grid-cols-[auto_1fr] has-[>svg]:gap-x-2 *:[svg]:row-span-2 *:[svg]:mt-px *:[svg]:text-current *:[svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: TONE_CLASSES.neutral,
        destructive: TONE_CLASSES.danger,
        info: TONE_CLASSES.info,
        success: TONE_CLASSES.success,
        warning: TONE_CLASSES.warning,
        danger: TONE_CLASSES.danger,
        brand: TONE_CLASSES.brand,
        neutral: TONE_CLASSES.neutral,
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

type AlertProps = Omit<React.ComponentProps<"div">, "title"> & {
  /** Legacy shadcn API: the children bring their own icon. */
  variant?: LegacyVariant | null
  /** Preferred API: draws the tone's icon. */
  tone?: AlertTone
  /** Bold first line (tone API). */
  title?: React.ReactNode
  /** Right-aligned actions such as Retry (tone API). */
  actions?: React.ReactNode
  /** Replace or hide (`false`) the tone's icon. */
  icon?: LucideIcon | false
}

function Alert({ className, variant, tone, title, actions, icon, children, role, ...props }: AlertProps) {
  const resolved: AlertTone = tone ?? LEGACY_TONE[variant ?? "default"]
  const ariaRole = role ?? (resolved === "danger" ? "alert" : "status")
  if (tone === undefined) {
    return (
      <div
        data-slot="alert"
        data-tone={resolved}
        role={ariaRole}
        className={cn(alertVariants({ variant: variant ?? "default" }), className)}
        {...props}
      >
        {children}
      </div>
    )
  }
  const Glyph = icon === false ? null : (icon ?? ALERT_TONE_ICON[tone])
  return (
    <div
      data-slot="alert"
      data-tone={tone}
      role={ariaRole}
      className={cn(
        "flex w-full items-start gap-2 rounded border px-3 py-2.5 text-left text-label leading-[1.45]",
        TONE_CLASSES[tone],
        className
      )}
      {...props}
    >
      {Glyph ? <Glyph aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> : null}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {title ? (
          <div data-slot="alert-title" className="font-semibold">
            {title}
          </div>
        ) : null}
        <div data-slot="alert-description" className="text-pretty [&_a]:underline [&_a]:underline-offset-3">
          {children}
        </div>
      </div>
      {actions ? (
        <div data-slot="alert-action" className="-my-1 flex shrink-0 flex-wrap items-center gap-2 self-center">
          {actions}
        </div>
      ) : null}
    </div>
  )
}

function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-title"
      className={cn(
        "font-semibold group-has-[>svg]/alert:col-start-2 [&_a]:underline [&_a]:underline-offset-3",
        className
      )}
      {...props}
    />
  )
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn(
        "text-label text-pretty text-current group-has-[>svg]/alert:col-start-2 [&_a]:underline [&_a]:underline-offset-3 [&_p:not(:last-child)]:mb-2",
        className
      )}
      {...props}
    />
  )
}

function AlertAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-action"
      className={cn("absolute top-2 right-2", className)}
      {...props}
    />
  )
}

export { Alert, AlertTitle, AlertDescription, AlertAction }
