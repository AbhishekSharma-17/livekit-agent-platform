"use client"

import * as React from "react"
import { cn } from "cn"
import { Dialog as DialogPrimitive } from "radix-ui"

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
        "fixed inset-0 isolate z-50 bg-[color-mix(in_oklch,var(--shadow-color)_24%,transparent)] duration-100 supports-backdrop-filter:backdrop-blur-xs data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * Widths for the scrolling "panel" layout (`size`). Panels replace the side
 * sheets the console used to have — side drawers are not allowed (user UI
 * rule, docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §5): forms use `md`, rich content
 * (test chat, version history, deliveries) `lg`/`xl`.
 */
const DIALOG_PANEL_WIDTH = {
  sm: "sm:max-w-md",
  md: "sm:max-w-lg",
  lg: "sm:max-w-3xl",
  xl: "sm:max-w-5xl",
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
          "group/dialog fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 duration-100 outline-none sm:max-w-sm data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          // Compact layout guards: one `minmax(0,1fr)` column so a long `<pre>`,
          // URL or code line can't widen the grid past the box (grid items
          // default to `min-width: auto`), and a viewport-capped height that
          // scrolls instead of pushing the header/footer off a short screen.
          !panel && "grid-cols-[minmax(0,1fr)] max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain",
          panel &&
            "flex max-h-[85dvh] flex-col gap-0 overflow-hidden p-0 max-sm:top-0 max-sm:left-0 max-sm:h-dvh max-sm:max-h-dvh max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none max-sm:ring-0",
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
            <Button
              variant="ghost"
              className={cn("absolute top-2 right-2", panel && "top-3.5 right-3.5")}
              size="icon-sm"
            >
              <XIcon
              />
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
        "flex flex-col gap-2 group-data-[layout=panel]/dialog:shrink-0 group-data-[layout=panel]/dialog:gap-1 group-data-[layout=panel]/dialog:border-b group-data-[layout=panel]/dialog:border-border group-data-[layout=panel]/dialog:px-5 group-data-[layout=panel]/dialog:py-4 group-data-[layout=panel]/dialog:pr-14",
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
        "-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-xl border-t bg-muted/50 p-4 sm:flex-row sm:justify-end group-data-[layout=panel]/dialog:mx-0 group-data-[layout=panel]/dialog:mb-0 group-data-[layout=panel]/dialog:shrink-0 group-data-[layout=panel]/dialog:px-5 group-data-[layout=panel]/dialog:py-3.5 max-sm:group-data-[layout=panel]/dialog:rounded-none max-sm:group-data-[layout=panel]/dialog:pb-[max(0.875rem,env(safe-area-inset-bottom))]",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close asChild>
          <Button variant="outline">Close</Button>
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
      className={cn("relative flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto overscroll-contain px-5 py-5", className)}
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
        "font-heading text-base leading-none font-medium group-data-[layout=panel]/dialog:text-[1.0625rem] group-data-[layout=panel]/dialog:leading-6 group-data-[layout=panel]/dialog:font-semibold group-data-[layout=panel]/dialog:tracking-[-0.01em]",
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
        "text-sm text-muted-foreground *:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-foreground",
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
