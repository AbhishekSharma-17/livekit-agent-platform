"use client"

import * as React from "react"
import { Slot } from "radix-ui"
import { MenuIcon, PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { useIsMobile } from "@/hooks/use-mobile"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { readStoredSidebarState, writeSidebarState } from "@/components/ui/sidebar-state"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

/**
 * Console sidebar primitives (docs/ui/DESIGN-SYSTEM.md sections 7.1 and 7.2).
 *
 * - Wider than 820 px: a 248 px sidebar on the app background, sticky and
 *   full height, scrolling on its own. It collapses to a 58 px icon rail
 *   (`SidebarCollapseTrigger`, or Cmd or Ctrl plus B). In the rail, link
 *   labels fade out but stay in the accessibility tree, group labels become
 *   short hairlines and each link gets a tooltip with its name. The choice is
 *   remembered per browser (`sidebar-state.ts`), and the server renders the
 *   remembered width from the cookie mirror, so nothing jumps on load.
 * - At 820 px and below the sidebar is removed and the same content opens in
 *   the full-screen **Menu dialog** from the top bar's hamburger (or the
 *   phone tab bar's "More"). Side sheets are not allowed (decision D7,
 *   docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md section 5). There is no rail there.
 *
 * Closing the menu hands focus back to the control that opened it, including
 * after a mouse click (Safari and Firefox don't focus buttons on click, so the
 * dialog's own opener capture would see `<body>`).
 */
export const SIDEBAR_WIDTH = "248px"
/**
 * The icon rail. 58 px puts a nav icon (10 px inset, 1 px border, 10 px
 * padding) on the rail's centre line, so the icons stay put while the width
 * animates and only the labels fade.
 */
export const SIDEBAR_RAIL_WIDTH = "58px"
/** The desktop sidebar's id, for the collapse control's `aria-controls`. */
export const SIDEBAR_ID = "console-sidebar"

type SidebarContextProps = {
  /** At most 820 px wide: the sidebar is the Menu dialog. */
  isMobile: boolean
  menuOpen: boolean
  setMenuOpen: (open: boolean) => void
  /** Open the Menu dialog and remember `opener` for focus return. */
  openMenu: (opener?: HTMLElement | null) => void
  openerRef: React.RefObject<HTMLElement | null>
  /** The desktop sidebar is the icon rail (only ever drawn wider than 820 px). */
  collapsed: boolean
  setCollapsed: (collapsed: boolean) => void
  toggleCollapsed: () => void
}

const SidebarContext = React.createContext<SidebarContextProps | null>(null)

function useSidebar() {
  const context = React.useContext(SidebarContext)
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.")
  }
  return context
}

/** Typing in a field keeps Cmd or Ctrl plus B for itself (bold in rich text, for one). */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target instanceof HTMLElement && target.isContentEditable) return true
  return (
    target.closest(
      "input, textarea, select, [contenteditable=''], [contenteditable='true'], [contenteditable='plaintext-only'], [role='textbox']"
    ) !== null
  )
}

function SidebarProvider({
  children,
  defaultCollapsed = false,
}: {
  children: React.ReactNode
  /** The remembered state as the server read it from the cookie mirror, so the first paint is right. */
  defaultCollapsed?: boolean
}) {
  const isMobile = useIsMobile()
  const [menuOpen, setMenuOpen] = React.useState(false)
  const openerRef = React.useRef<HTMLElement | null>(null)
  const [collapsed, setCollapsedState] = React.useState(defaultCollapsed)

  const openMenu = React.useCallback((opener?: HTMLElement | null) => {
    openerRef.current = opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    setMenuOpen(true)
  }, [])

  const setCollapsed = React.useCallback((next: boolean) => {
    setCollapsedState(next)
    writeSidebarState(next ? "collapsed" : "expanded")
  }, [])

  const toggleCollapsed = React.useCallback(() => setCollapsed(!collapsed), [collapsed, setCollapsed])

  // localStorage is the record. If it disagrees with the cookie the server
  // read (the cookie was cleared or has expired), follow it and rewrite the
  // cookie. A remembered rail with nothing stored yet gets stored.
  const initialCollapsed = React.useRef(defaultCollapsed)
  React.useEffect(() => {
    const stored = readStoredSidebarState()
    const fromCookie = initialCollapsed.current ? "collapsed" : "expanded"
    if (stored !== null && stored !== fromCookie) {
      setCollapsedState(stored === "collapsed")
      writeSidebarState(stored)
    } else if (stored === null && fromCookie === "collapsed") {
      writeSidebarState("collapsed")
    }
  }, [])

  // Cmd or Ctrl plus B toggles the rail. Not at 820 px and below, where
  // there is no rail, and not while typing in a field.
  React.useEffect(() => {
    if (isMobile) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.shiftKey) return
      if (!(event.metaKey || event.ctrlKey)) return
      if (event.key?.toLowerCase() !== "b" && event.code !== "KeyB") return
      if (isEditableTarget(event.target)) return
      event.preventDefault()
      toggleCollapsed()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [isMobile, toggleCollapsed])

  // Widening past 820 px while the menu is open: the sidebar is back, so the
  // dialog has nothing left to do.
  React.useEffect(() => {
    if (!isMobile) setMenuOpen(false)
  }, [isMobile])

  const value = React.useMemo<SidebarContextProps>(
    () => ({ isMobile, menuOpen, setMenuOpen, openMenu, openerRef, collapsed, setCollapsed, toggleCollapsed }),
    [isMobile, menuOpen, openMenu, collapsed, setCollapsed, toggleCollapsed]
  )

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>
}

/**
 * The desktop sidebar: hidden at 820 px and below, where `SidebarMenuDialog`
 * takes over. `data-state` is `expanded` or `collapsed`, and children style
 * the rail with `group-data-[state=collapsed]/sidebar:` (nothing in the Menu
 * dialog sits inside this group, so it never sees the rail). Only this
 * container's width animates, over `--duration-base` with the entrance
 * easing. Under reduced motion it changes at once.
 */
function Sidebar({ className, children, ...props }: React.ComponentProps<"aside">) {
  const { collapsed } = useSidebar()
  return (
    <aside
      id={SIDEBAR_ID}
      data-slot="sidebar"
      data-state={collapsed ? "collapsed" : "expanded"}
      className={cn(
        "group/sidebar sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col overflow-x-hidden bg-sidebar text-foreground transition-[width] duration-(--duration-base) ease-entrance data-[state=collapsed]:w-[58px] motion-reduce:transition-none min-[821px]:flex",
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

/**
 * "⌘ B" on Apple platforms and "Ctrl B" elsewhere. Only rendered inside an
 * open tooltip, so it never runs on the server.
 */
function ShortcutHint() {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
  const apple = /mac|iphone|ipad/i.test(nav.userAgentData?.platform ?? nav.platform ?? "")
  return (
    <KbdGroup>
      <Kbd>{apple ? "⌘" : "Ctrl"}</Kbd>
      <Kbd>B</Kbd>
    </KbdGroup>
  )
}

/**
 * Collapses the desktop sidebar to the icon rail and back (also Cmd or Ctrl
 * plus B). It lives inside the desktop sidebar, so it never shows at 820 px
 * and below. The tooltip repeats the name and shows the shortcut.
 */
function SidebarCollapseTrigger({ className, ...props }: Omit<React.ComponentProps<typeof Button>, "children">) {
  const { collapsed, toggleCollapsed } = useSidebar()
  const label = collapsed ? "Expand sidebar" : "Collapse sidebar"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          data-slot="sidebar-collapse-trigger"
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          aria-controls={SIDEBAR_ID}
          aria-expanded={!collapsed}
          className={cn("shrink-0 text-text-secondary hover:bg-sidebar-hover hover:text-foreground", className)}
          onClick={toggleCollapsed}
          {...props}
        >
          {collapsed ? <PanelLeftOpenIcon aria-hidden="true" /> : <PanelLeftCloseIcon aria-hidden="true" />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={8}>
        {label}
        <ShortcutHint />
      </TooltipContent>
    </Tooltip>
  )
}

/**
 * The rail's name tooltip for a link. It is supplementary (the link keeps its
 * own accessible name), opens only while the desktop sidebar is collapsed,
 * and stays controlled throughout so it never switches modes.
 */
function SidebarRailTooltip({ label, children }: { label: string; children: React.ReactElement }) {
  const { collapsed, isMobile } = useSidebar()
  const [open, setOpen] = React.useState(false)
  return (
    <Tooltip open={collapsed && !isMobile && open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={8}>
        {label}
      </TooltipContent>
    </Tooltip>
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

/**
 * A 12 px tertiary label that names the job of the group below it. In the
 * rail the words fade out (the list keeps its name through
 * `aria-labelledby`) and a short hairline takes their place, so the groups
 * still read apart.
 */
function SidebarGroupLabel({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-group-label"
      className={cn(
        "relative flex h-7 items-center px-2.5 text-caption font-medium whitespace-nowrap text-text-tertiary",
        className
      )}
      {...props}
    >
      <span className="transition-opacity duration-(--duration-base) ease-entrance group-data-[state=collapsed]/sidebar:opacity-0">
        {children}
      </span>
      <span
        aria-hidden="true"
        data-slot="sidebar-group-divider"
        className="pointer-events-none absolute inset-x-2.5 top-1/2 h-px bg-border opacity-0 transition-opacity duration-(--duration-base) ease-entrance group-data-[state=collapsed]/sidebar:opacity-100"
      />
    </div>
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
 * `size="touch"` is the 48 px row the Menu dialog uses on phones. In the
 * rail the icon stays where it is and the label (`SidebarMenuLabel`) fades.
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
        "group/menu-button flex w-full min-w-0 items-center gap-2.5 rounded border border-transparent px-2.5 text-left text-control font-medium whitespace-nowrap text-text-secondary outline-none transition-colors duration-(--duration-fast)",
        "hover:bg-sidebar-hover hover:text-foreground focus-visible:shadow-focus",
        "data-[active=true]:border-border data-[active=true]:bg-sidebar-active data-[active=true]:text-foreground data-[active=true]:shadow-raised",
        "[&>svg]:shrink-0 [&>svg]:text-text-secondary data-[active=true]:[&>svg]:text-brand [&>span]:truncate",
        size === "touch" ? "h-12" : "h-[34px]",
        className
      )}
      {...props}
    />
  )
}

/**
 * A link's text. In the rail it fades out but stays in the accessibility
 * tree, so the link keeps its name.
 */
function SidebarMenuLabel({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="sidebar-menu-label"
      className={cn(
        "min-w-0 flex-1 truncate transition-opacity duration-(--duration-base) ease-entrance group-data-[state=collapsed]/sidebar:opacity-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * A tabular count on the link's right edge. In the rail it becomes a small
 * pill on the icon's top right corner.
 */
function SidebarMenuBadge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="sidebar-menu-badge"
      className={cn(
        "ml-auto text-caption font-medium text-text-tertiary tabular-nums",
        "group-data-[state=collapsed]/sidebar:absolute group-data-[state=collapsed]/sidebar:top-0.5 group-data-[state=collapsed]/sidebar:left-[22px] group-data-[state=collapsed]/sidebar:flex group-data-[state=collapsed]/sidebar:h-4 group-data-[state=collapsed]/sidebar:min-w-4 group-data-[state=collapsed]/sidebar:items-center group-data-[state=collapsed]/sidebar:justify-center group-data-[state=collapsed]/sidebar:rounded-pill group-data-[state=collapsed]/sidebar:border group-data-[state=collapsed]/sidebar:border-border group-data-[state=collapsed]/sidebar:bg-sidebar-active group-data-[state=collapsed]/sidebar:px-1 group-data-[state=collapsed]/sidebar:text-tab group-data-[state=collapsed]/sidebar:leading-none",
        className
      )}
      {...props}
    />
  )
}

export {
  Sidebar,
  SidebarCollapseTrigger,
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
  SidebarMenuLabel,
  SidebarProvider,
  SidebarRailTooltip,
  SidebarTrigger,
  useSidebar,
}
