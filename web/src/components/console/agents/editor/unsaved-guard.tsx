"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * The internal URL an anchor click would navigate to, or null when the click
 * should be left alone (new tab, modifier keys, download, external origin,
 * same page, already handled).
 */
export function guardedHref(event: MouseEvent, currentUrl: URL): string | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const target = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!(target instanceof HTMLAnchorElement)) return null;
  if (target.target && target.target !== "_self") return null;
  if (target.hasAttribute("download") || target.dataset.unsavedGuard === "skip") return null;
  const next = new URL(target.href, currentUrl.href);
  if (next.origin !== currentUrl.origin) return null;
  if (next.pathname === currentUrl.pathname && next.search === currentUrl.search) return null;
  return `${next.pathname}${next.search}${next.hash}`;
}

/**
 * Unsaved-changes guard (docs/UI_UX_SPEC.md §4.3): `beforeunload` for reloads
 * and tab closes, and a confirmation for in-app link clicks (captured on the
 * document before Next's `<Link>` handler runs). Programmatic navigation
 * (`router.push`) and the browser back button are not intercepted — the App
 * Router exposes no navigation-blocking API.
 */
export function UnsavedGuard({ dirty, name }: { dirty: boolean; name: string }) {
  const router = useRouter();
  const [pendingHref, setPendingHref] = React.useState<string | null>(null);
  const dirtyRef = React.useRef(dirty);
  dirtyRef.current = dirty;

  React.useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent) {
      if (!dirtyRef.current) return;
      event.preventDefault();
      // Legacy browsers need a returnValue to show the prompt.
      event.returnValue = "";
    }
    function onClick(event: MouseEvent) {
      if (!dirtyRef.current) return;
      const href = guardedHref(event, new URL(window.location.href));
      if (!href) return;
      event.preventDefault();
      event.stopPropagation();
      setPendingHref(href);
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, []);

  return (
    <Dialog open={pendingHref !== null} onOpenChange={(open) => !open && setPendingHref(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Leave without saving?</DialogTitle>
          <DialogDescription>Your unsaved changes to {name} will be lost.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setPendingHref(null)}>
            Stay
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            onClick={() => {
              const href = pendingHref;
              setPendingHref(null);
              if (href) router.push(href);
            }}
          >
            Leave without saving
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
