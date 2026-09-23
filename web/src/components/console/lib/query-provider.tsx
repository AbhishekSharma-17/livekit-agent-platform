"use client";

import * as React from "react";
import { useState } from "react";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";

/**
 * Tiny pub-sub so `ApiHealthBanner` (components/console/shell) can subscribe
 * to the query cache's error stream without every hook importing react
 * context — the cache itself is a singleton per `ConsoleQueryProvider`
 * instance, which is what we actually want to observe.
 */
function createHealthEvents() {
  const listeners = new Set<(down: boolean) => void>();
  let down = false;
  return {
    subscribe(listener: (down: boolean) => void) {
      listener(down);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set(next: boolean) {
      if (next === down) return;
      down = next;
      listeners.forEach((listener) => listener(down));
    },
  };
}

export const apiHealthEvents = createHealthEvents();

/** Network failure or 5xx counts toward the banner; a 4xx with an error envelope does not (docs/UI_UX_SPEC.md §6). */
function isInfraFailure(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500;
  return true; // fetch/network throw, not an ApiError
}

const REDIRECT_EXEMPT_PATH_PREFIXES = ["/api/console/auth"];

function redirectToLogin() {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  if (pathname === "/login" || REDIRECT_EXEMPT_PATH_PREFIXES.some((p) => pathname.startsWith(p))) return;
  const next = encodeURIComponent(`${pathname}${search}`);
  window.location.assign(`/login?next=${next}`);
}

export function ConsoleQueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    let consecutiveFailures = 0;

    const queryCache = new QueryCache({
      onError: (error) => {
        // A real, authenticated-session-required 401 (V2-02): no login page
        // or middleware exists yet (both are V2-02/V2-14), so this is the
        // shell's own redirect handling — see the WP-1 hand-off note.
        if (error instanceof ApiError && error.status === 401) {
          redirectToLogin();
          return;
        }
        if (isInfraFailure(error)) {
          consecutiveFailures += 1;
          if (consecutiveFailures >= 3) apiHealthEvents.set(true);
        }
      },
      onSuccess: () => {
        consecutiveFailures = 0;
        apiHealthEvents.set(false);
      },
    });

    return new QueryClient({
      queryCache,
      defaultOptions: {
        queries: {
          retry: 1,
          refetchOnWindowFocus: false,
        },
      },
    });
  });

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
