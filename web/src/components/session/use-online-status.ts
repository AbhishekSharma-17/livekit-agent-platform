"use client";

/**
 * `navigator.onLine`, kept current by the `online` / `offline` events, for the
 * pre-call card's "you're offline" state (docs/ui/DESIGN-SYSTEM.md section
 * 8.8). The server snapshot, and so the first client render, is "online": the
 * hydrated markup matches the server's, and an offline browser switches over
 * right after hydration.
 */
import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => navigator.onLine !== false,
    () => true,
  );
}
