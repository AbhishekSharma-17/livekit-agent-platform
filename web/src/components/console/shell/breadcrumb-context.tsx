"use client";

import * as React from "react";

import type { BreadcrumbEntry } from "@/components/shared/page-header";

/**
 * Lets a deep-linked page (an agent editor, a session detail — owned by
 * WP-2/WP-3/WP-7) hand the shell's top bar a richer trail than the static
 * one-level label `navLabelForPath` can produce ("Agents / Stage9
 * Insurance" per docs/UI_UX_SPEC.md §3.2). Depth-1 list pages don't need
 * this: their own `PageHeader` already renders no breadcrumb trail, and the
 * top bar falls back to the nav label.
 *
 * Usage from an owned page: `useSetBreadcrumbs([{ label: "Agents", href:
 * "/console/agents" }, { label: agent.name }])` in a `useEffect`, cleared on
 * unmount by passing `undefined`.
 */
const BreadcrumbContext = React.createContext<{
  trail: BreadcrumbEntry[] | undefined;
  setTrail: (trail: BreadcrumbEntry[] | undefined) => void;
} | null>(null);

export function BreadcrumbProvider({ children }: { children: React.ReactNode }) {
  const [trail, setTrail] = React.useState<BreadcrumbEntry[] | undefined>(undefined);
  const value = React.useMemo(() => ({ trail, setTrail }), [trail]);
  return <BreadcrumbContext.Provider value={value}>{children}</BreadcrumbContext.Provider>;
}

export function useBreadcrumbTrail(): BreadcrumbEntry[] | undefined {
  const ctx = React.useContext(BreadcrumbContext);
  return ctx?.trail;
}

/** Call from a page that owns a deeper route to set the top bar's trail. */
export function useSetBreadcrumbs(trail: BreadcrumbEntry[] | undefined) {
  const ctx = React.useContext(BreadcrumbContext);
  const setTrail = ctx?.setTrail;
  const key = trail ? JSON.stringify(trail) : "";
  React.useEffect(() => {
    setTrail?.(trail);
    return () => setTrail?.(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setTrail, key]);
}

/**
 * Declarative form of `useSetBreadcrumbs` for server pages (V2-19C): renders
 * nothing, sets the top bar's trail. Every depth ≥ 2 console page uses the
 * top bar for its trail — one breadcrumb `nav` per page (axe
 * `landmark-unique`), never a second copy inside the `PageHeader`.
 */
export function ConsoleBreadcrumbs({ trail }: { trail: BreadcrumbEntry[] }) {
  useSetBreadcrumbs(trail);
  return null;
}
