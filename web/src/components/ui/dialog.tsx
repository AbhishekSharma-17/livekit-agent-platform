"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"

function Dialog({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="dialog-overlay"
      className={cn(
        "fixed inset-0 isolate z-50 bg-overlay duration-(--duration-slow) data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * Dialog (docs/ui/DESIGN-SYSTEM.md section 6.4): an `--overlay` backdrop and
 * a popup `min(100vw − 32px, 560px)` wide (`lg` 760, `xl` 980; `sm` 440 for
 * short confirmations), at most `min(88dvh, 860px)` tall with internal
 * scroll, 24 px padding, 14 px radius and the modal shadow. It rises 8 px
 * and scales from .98 over 220 ms. A 30 px close `X` sits 16 px from the top
 * right. On phones it is `100vw − 16px` wide with 20 px padding.
 *
 * The footer is sticky and bleeds to the dialog's edges on a `--muted` fill
 * with a top hairline, so the actions stay visible while the body scrolls.
 * Put the one primary action last, Cancel to its left.
 *
 * Destructive confirmations pass `role="alertdialog"` (see
 * `console/shared/confirm-dialog.tsx`). Focus is trapped while open and
 * returns to the opener on close.
 *
 * Panels replace the side sheets the console used to have: side drawers are
 * not allowed (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md section 5, decision D7).
 */
const DIALOG_PANEL_WIDTH = {
  sm: "sm:w-[min(calc(100vw-32px),440px)]",
  md: "sm:w-[min(calc(100vw-32px),560px)]",
  lg: "sm:w-[min(calc(100vw-32px),760px)]",
  xl: "sm:w-[min(calc(100vw-32px),980px)]",
} as const

type DialogSize = keyof typeof DIALOG_PANEL_WIDTH

/**
 * Without `size` this is the stock compact dialog (confirmations, short
 * forms): 24 rem wide, one shrinkable grid column, and it scrolls as a whole
 * if it is ever taller than the viewport. With `size` it becomes a panel: a
 * flex column capped at 85 % of the viewport with a sticky
 * `DialogHeader`/`DialogFooter` and a scrolling `DialogBody` in between, and
 * full-screen below `sm` (phones). Anything with code, a long list or more
 * than a few fields belongs in a panel (`md` for forms).
 */
/**
 * Records what had focus when the dialog opened, so closing can hand focus
 * back to it. Radix only returns focus to a `DialogTrigger`; most console
 * dialogs open from state (a picker's "Add key", a row or split-button menu
 * item, the sidebar toggle), which would otherwise drop focus on `<body>`. A
 * dropdown menu item is resolved to its menu's trigger, because the item is
 * gone by the time the dialog closes. Runs as a layout effect of the content's
 * first child, i.e. before Radix's focus scope moves focus inside.
 */
function OpenerCapture({ openerRef }: { openerRef: React.RefObject<HTMLElement | null> }) {
  React.useLayoutEffect(() => {
    let opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const menu = opener?.closest('[role="menu"]')
    const menuTriggerId = menu?.getAttribute("aria-labelledby")
    const menuTrigger = menuTriggerId ? document.getElementById(menuTriggerId) : null
    if (menuTrigger) opener = menuTrigger
    openerRef.current = opener && opener !== document.body ? opener : null
  }, [openerRef])
  return null
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  size,
  onCloseAutoFocus,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  showCloseButton?: boolean
  size?: DialogSize
}) {
  const panel = size !== undefined
  const openerRef = React.useRef<HTMLElement | null>(null)
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        data-layout={panel ? "panel" : undefined}
        className={cn(
          "group/dialog fixed top-1/2 left-1/2 z-50 w-[calc(100vw-16px)] max-h-[min(88dvh,860px)] -translate-x-1/2 -translate-y-1/2 rounded-dialog border border-border bg-popover text-body text-foreground shadow-modal duration-(--duration-slow) ease-entrance outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-98 data-open:slide-in-from-bottom-2 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-98",
          // Compact layout guards: one `minmax(0,1fr)` column so a long `<pre>`,
          // URL or code line can't widen the grid past the box (grid items
          // default to `min-width: auto`), and a capped height that scrolls
          // instead of pushing the header or footer off a short screen.
          !panel &&
            "grid grid-cols-[minmax(0,1fr)] gap-4 overflow-y-auto overscroll-contain p-5 sm:w-[min(calc(100vw-32px),560px)] sm:p-6",
          panel && "flex flex-col gap-0 overflow-hidden p-0",
          panel && DIALOG_PANEL_WIDTH[size],
          className
        )}
        onCloseAutoFocus={(event) => {
          onCloseAutoFocus?.(event)
          if (event.defaultPrevented) return
          const opener = openerRef.current
          if (opener?.isConnected) {
            event.preventDefault()
            opener.focus({ preventScroll: true })
          }
        }}
        {...props}
      >
        <OpenerCapture openerRef={openerRef} />
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close data-slot="dialog-close" asChild>
            <Button variant="ghost" className="absolute top-4 right-4 size-[30px] rounded-sm" size="icon-sm">
              <XIcon />
              <span className="sr-only">Close</span>
            </Button>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn(
        "flex flex-col gap-1 pr-8 group-data-[layout=panel]/dialog:shrink-0 group-data-[layout=panel]/dialog:border-b group-data-[layout=panel]/dialog:border-border group-data-[layout=panel]/dialog:px-5 group-data-[layout=panel]/dialog:py-4 group-data-[layout=panel]/dialog:pr-14 sm:group-data-[layout=panel]/dialog:px-6 sm:group-data-[layout=panel]/dialog:pt-5",
        className
      )}
      {...props}
    />
  )
}

function DialogFooter({
  className,
  showCloseButton = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean
}) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "sticky bottom-0 z-10 -mx-5 -mb-5 mt-2 flex flex-col-reverse gap-2 rounded-b-dialog border-t border-border bg-muted px-5 py-3 sm:-mx-6 sm:-mb-6 sm:flex-row sm:flex-wrap sm:justify-end sm:px-6 group-data-[layout=panel]/dialog:static group-data-[layout=panel]/dialog:mx-0 group-data-[layout=panel]/dialog:mt-0 group-data-[layout=panel]/dialog:mb-0 group-data-[layout=panel]/dialog:shrink-0 group-data-[layout=panel]/dialog:pb-[max(0.75rem,env(safe-area-inset-bottom))]",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close asChild>
          <Button variant="secondary">Close</Button>
        </DialogPrimitive.Close>
      )}
    </div>
  )
}

/** The scrolling region of a panel dialog (`DialogContent size=…`), between the sticky header and footer. */
function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-body"
      className={cn("relative flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-5 sm:px-6", className)}
      {...props}
    />
  )
}

function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        "font-heading text-dialog font-semibold tracking-[-0.012em] text-foreground",
        className
      )}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "text-control text-text-secondary *:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-foreground",
        className
      )}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
  type DialogSize,
}
