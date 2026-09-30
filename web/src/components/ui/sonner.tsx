"use client"

import * as React from "react"
import { useTheme } from "next-themes"
import { toast as sonnerToast, Toaster as Sonner, type ExternalToast, type ToasterProps } from "sonner"
import { CircleAlertIcon, CircleCheckIcon, InfoIcon, TriangleAlertIcon } from "lucide-react"

import { Spinner } from "@/components/ui/spinner"

/**
 * The one toast primitive for the whole app (docs/ui/DESIGN-SYSTEM.md
 * section 6.4), on sonner:
 * - bottom-right, 24 px from the edges (16 px on phones), lifted above the
 *   phone tab bar (`--toast-offset-mobile-bottom` in `globals.css`);
 * - success and info toasts auto-dismiss after 6 s; errors and warnings stay
 *   until dismissed and carry a close button;
 * - errors are `role="alert"` (assertive), everything else `role="status"`;
 * - overlay shadow, popover surface, the intent-map icons.
 *
 * Call sites keep using `toast` from `sonner` (or the re-export below): the
 * mounted `Toaster` applies the error/warning policy to sonner's `toast`.
 */
export const TOAST_DURATION_MS = 6000

const PERSISTENT: ExternalToast = { duration: Number.POSITIVE_INFINITY, closeButton: true }
const POLICY = Symbol.for("lkap.toast-policy")

type PolicyTarget = typeof sonnerToast & { [POLICY]?: true }

/** Make errors and warnings persistent (idempotent). Callers may still pass their own `duration`. */
export function applyToastPolicy(target: typeof sonnerToast = sonnerToast): void {
  const toastApi = target as PolicyTarget
  if (toastApi[POLICY]) return
  const error = toastApi.error
  const warning = toastApi.warning
  toastApi.error = (message, data) => error(message, { ...PERSISTENT, ...data })
  toastApi.warning = (message, data) => warning(message, { ...PERSISTENT, ...data })
  toastApi[POLICY] = true
}

/** Give each toast its live-region role: `alert` for errors, `status` otherwise. */
function assignRoles(root: ParentNode | null) {
  if (!root) return
  root.querySelectorAll<HTMLElement>("[data-sonner-toast]").forEach((node) => {
    const role = node.getAttribute("data-type") === "error" ? "alert" : "status"
    if (node.getAttribute("role") !== role) node.setAttribute("role", role)
  })
}

const Toaster = ({ ...props }: ToasterProps) => {
  const { resolvedTheme } = useTheme()
  const sectionRef = React.useRef<HTMLElement>(null)

  React.useEffect(() => {
    applyToastPolicy()
    const section = sectionRef.current
    if (!section) return
    assignRoles(section)
    // Watch only the toaster's own region, not the whole page.
    const observer = new MutationObserver(() => assignRoles(section))
    observer.observe(section, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-type"] })
    return () => observer.disconnect()
  }, [])

  return (
    <Sonner
      ref={sectionRef}
      theme={(resolvedTheme as ToasterProps["theme"]) ?? "system"}
      position="bottom-right"
      duration={TOAST_DURATION_MS}
      offset={{
        top: "var(--toast-offset)",
        right: "var(--toast-offset)",
        bottom: "var(--toast-offset)",
        left: "var(--toast-offset)",
      }}
      mobileOffset={{
        top: "var(--toast-offset-mobile)",
        right: "var(--toast-offset-mobile)",
        bottom: "var(--toast-offset-mobile-bottom)",
        left: "var(--toast-offset-mobile)",
      }}
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4 text-success-text" />,
        info: <InfoIcon className="size-4 text-info-text" />,
        warning: <TriangleAlertIcon className="size-4 text-warning-text" />,
        error: <CircleAlertIcon className="size-4 text-destructive-text" />,
        loading: <Spinner />,
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius-lg)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "cn-toast shadow-overlay! text-label!",
          description: "text-text-secondary!",
        },
      }}
      {...props}
    />
  )
}

export { Toaster, sonnerToast as toast }
