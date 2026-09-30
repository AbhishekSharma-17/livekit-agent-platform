"use client"

import * as React from "react"
import { Slot } from "radix-ui"
import { MenuIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { useIsMobile } from "@/hooks/use-mobile"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"

/**
 * Console sidebar primitives (docs/ui/DESIGN-SYSTEM.md sections 7.1 and 7.2).
 *
 * - Wider than 820 px: a 248 px sidebar on the app background, sticky and
 *   full height, scrolling on its own. There is no collapsed rail.
 * - At 820 px and below the sidebar is removed and the same content opens in
 *   the full-screen **Menu dialog** from the top bar's hamburger (or the
 *   phone tab bar's "More"). Side sheets are not allowed (decision D7,
 *   docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md section 5).
 *
 * Closing the menu hands focus back to the control that opened it, including
 * after a mouse click (Safari and Firefox don't focus buttons on click, so the
 * dialog's own opener capture would see `<body>`).
 */
export const SIDEBAR_WIDTH = "248px"

type SidebarContextProps = {
  /** At most 820 px wide: the sidebar is the Menu dialog. */
  isMobile: boolean
  menuOpen: boolean
  setMenuOpen: (open: boolean) => void
  /** Open the Menu dialog and remember `opener` for focus return. */
  openMenu: (opener?: HTMLElement | null) => void
  openerRef: React.RefObject<HTMLElement | null>
}

const SidebarContext = React.createContext<SidebarContextProps | null>(null)

function useSidebar() {
  const context = React.useContext(SidebarContext)
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.")
  }
  return context
}

function SidebarProvider({ children }: { children: React.ReactNode }) {
  const isMobile = useIsMobile()
  const [menuOpen, setMenuOpen] = React.useState(false)
  const openerRef = React.useRef<HTMLElement | null>(null)

  const openMenu = React.useCallback((opener?: HTMLElement | null) => {
    openerRef.current = opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    setMenuOpen(true)
  }, [])

  // Widening past 820 px while the menu is open: the sidebar is back, so the
  // dialog has nothing left to do.
  React.useEffect(() => {
    if (!isMobile) setMenuOpen(false)
  }, [isMobile])

  const value = React.useMemo<SidebarContextProps>(
    () => ({ isMobile, menuOpen, setMenuOpen, openMenu, openerRef }),
    [isMobile, menuOpen, openMenu]
  )

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>
}

/** The desktop sidebar: hidden at 820 px and below, where `SidebarMenuDialog` takes over. */
function Sidebar({ className, children, ...props }: React.ComponentProps<"aside">) {
  return (
    <aside
      data-slot="sidebar"
      className={cn(
        "sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col bg-sidebar text-foreground min-[821px]:flex",
        className
      )}
      {...props}
    >
      <div
        data-slot="sidebar-inner"
        className="flex min-h-0 flex-1 flex-col px-2.5 pt-3 pb-2.5"
      >
        {children}
      </div>
    </aside>
  )
}

/**
 * The full-screen Menu dialog (820 px and below): the sidebar's own content
 * on the sidebar colour, in a 248 px column (full width on phones).
 */
function SidebarMenuDialog({ children }: { children: React.ReactNode }) {
  const { menuOpen, setMenuOpen, openerRef } = useSidebar()
  return (
    <Dialog open={menuOpen} onOpenChange={setMenuOpen}>
      <DialogContent
        data-slot="sidebar-menu-dialog"
        data-sidebar="sidebar"
        data-mobile="true"
        className="inset-0 top-0 left-0 flex h-dvh gap-0 max-h-none w-full translate-x-0 translate-y-0 flex-col overflow-y-auto rounded-none border-0 bg-sidebar p-0 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-foreground shadow-none sm:w-full sm:p-0 data-open:zoom-in-100 data-closed:zoom-out-100"
        onCloseAutoFocus={(event) => {
          const opener = openerRef.current
          if (opener?.isConnected) {
            event.preventDefault()
            opener.focus({ preventScroll: true })
          }
        }}
      >
        <DialogTitle className="sr-only">Menu</DialogTitle>
        <DialogDescription className="sr-only">Console navigation.</DialogDescription>
        <div
          data-slot="sidebar-inner"
          className="flex min-h-full w-full flex-1 flex-col px-4 pt-3 pb-4 sm:max-w-[248px] sm:px-2.5"
        >
          {children}
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** The hamburger in the top bar (820 px and below). */
function SidebarTrigger({
  className,
  onClick,
  ...props
}: React.ComponentProps<typeof Button>) {
  const { openMenu, menuOpen } = useSidebar()
  return (
    <Button
      data-slot="sidebar-trigger"
      variant="ghost"
      size="icon-md"
      aria-label="Open menu"
      aria-haspopup="dialog"
      aria-expanded={menuOpen}
      className={cn("min-[821px]:hidden", className)}
      onClick={(event) => {
        onClick?.(event)
        openMenu(event.currentTarget)
      }}
      {...props}
    >
      <MenuIcon aria-hidden="true" />
    </Button>
  )
}

function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-header"
      className={cn("flex shrink-0 items-center gap-2 pb-3", className)}
      {...props}
    />
  )
}

/** The scrolling middle: the grouped nav. */
function SidebarContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-content"
      className={cn("no-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto", className)}
      {...props}
    />
  )
}

/** The foot: the account menu, above a hairline. */
function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-footer"
      className={cn("mt-2 flex shrink-0 flex-col gap-1 border-t border-border pt-2", className)}
      {...props}
    />
  )
}

function SidebarGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-group"
      className={cn("flex w-full min-w-0 flex-col gap-0.5", className)}
      {...props}
    />
  )
}

/** A 12 px tertiary label that names the job of the group below it. */
function SidebarGroupLabel({
  className,
  asChild = false,
  ...props
}: React.ComponentProps<"div"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "div"
  return (
    <Comp
      data-slot="sidebar-group-label"
      className={cn("flex h-7 items-center px-2.5 text-caption font-medium text-text-tertiary", className)}
      {...props}
    />
  )
}

function SidebarMenu({ className, ...props }: React.ComponentProps<"ul">) {
  return (
    <ul
      data-slot="sidebar-menu"
      className={cn("flex w-full min-w-0 flex-col gap-0.5", className)}
      {...props}
    />
  )
}

function SidebarMenuItem({ className, ...props }: React.ComponentProps<"li">) {
  return <li data-slot="sidebar-menu-item" className={cn("relative", className)} {...props} />
}

/**
 * A nav link: 34 px, 13.5 px / 500, secondary text, 17 px icon, `--sidebar-hover`
 * on hover. Active: the `--sidebar-active` pill with a hairline border, the
 * raised shadow, foreground text, the accent icon and `aria-current="page"`.
 * `size="touch"` is the 48 px row the Menu dialog uses on phones.
 */
function SidebarMenuButton({
  asChild = false,
  isActive = false,
  size = "default",
  className,
  ...props
}: React.ComponentProps<"button"> & {
  asChild?: boolean
  isActive?: boolean
  size?: "default" | "touch"
}) {
  const Comp = asChild ? Slot.Root : "button"
  return (
    <Comp
      data-slot="sidebar-menu-button"
      data-sidebar="menu-button"
      data-active={isActive}
      data-size={size}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "group/menu-button flex w-full min-w-0 items-center gap-2.5 rounded border border-transparent px-2.5 text-left text-control font-medium text-text-secondary outline-none transition-colors duration-(--duration-fast)",
        "hover:bg-sidebar-hover hover:text-foreground focus-visible:shadow-focus",
        "data-[active=true]:border-border data-[active=true]:bg-sidebar-active data-[active=true]:text-foreground data-[active=true]:shadow-raised",
        "[&>svg]:shrink-0 [&>svg]:text-text-secondary data-[active=true]:[&>svg]:text-brand-text [&>span]:truncate",
        size === "touch" ? "h-12" : "h-[34px]",
        className
      )}
      {...props}
    />
  )
}

/** A tabular count on the link's right edge. */
function SidebarMenuBadge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="sidebar-menu-badge"
      className={cn("ml-auto text-caption font-medium text-text-tertiary tabular-nums", className)}
      {...props}
    />
  )
}

export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuDialog,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
}
