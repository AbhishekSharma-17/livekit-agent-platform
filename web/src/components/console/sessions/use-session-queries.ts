"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { AgentOut, ConnectionPage, SessionEventOut, SessionEventPage, SessionPage } from "@/contracts/lkap-contracts";

/**
 * WP-7's own reads. They live here (not in `lib/api-hooks.ts`, which is
 * WP-0's) because the list and the timeline need more rows than the shared
 * hooks ask for: `GET /v1/sessions` defaults to 50 rows and
 * `GET /v1/sessions/{id}/events` to 200, which silently truncated both views.
 * Keys stay under `["sessions", …]` so any `invalidateQueries(["sessions"])`
 * refreshes these too.
 */

/**
 * The window this hook fetches for client-side filtering — **not** the same
 * number as one request's `limit` (see below).
 */
export const SESSION_LIST_FETCH_LIMIT = 500;

/**
 * The api's maximum page size for `GET /v1/sessions` is `le=200` (V2-12
 * tightened it from `500`, ask #67 closing the CONTRACTS-V2 §3 filter gap,
 * the same day this constant was written against the old cap — a single
 * `limit=500` request now 422s the whole list, verified live). This hook
 * pages through `SESSION_REQUEST_PAGE`-sized requests instead of lowering
 * `SESSION_LIST_FETCH_LIMIT`, so "the newest 500" stays true rather than
 * silently shrinking to 200.
 */
const SESSION_REQUEST_PAGE = 200;

/**
 * The newest `SESSION_LIST_FETCH_LIMIT` sessions. Filtering and the 25-row
 * pagination happen client-side (docs/UI_UX_SPEC.md §7.14 item 2): the api
 * takes `limit`/`offset`, but not the channel / connection / date filters the
 * list offers, so paging on the server would filter one page at a time
 * (docs/v2/_asks.md V2-14-5 records why this wasn't switched to the server
 * filters CONTRACTS-V2 §3 / ask #67 added).
 */
export function useSessionList() {
  return useQuery({
    queryKey: ["sessions", "list", { limit: SESSION_LIST_FETCH_LIMIT }] as const,
    queryFn: async () => {
      const items: SessionPage["items"] = [];
      let offset = 0;
      let total = 0;
      while (items.length < SESSION_LIST_FETCH_LIMIT) {
        const page = await api.get<SessionPage>("sessions", {
          limit: Math.min(SESSION_REQUEST_PAGE, SESSION_LIST_FETCH_LIMIT - items.length),
          offset,
        });
        items.push(...page.items);
        total = page.total;
        offset += page.items.length;
        if (page.items.length < SESSION_REQUEST_PAGE || offset >= total) break;
      }
      return { items, total } satisfies SessionPage;
    },
  });
}

const EVENTS_PAGE = 1000;
/** Safety cap: stop paging after this many events (a very long call). */
export const EVENTS_MAX = 10_000;

/**
 * Every event of a session, paged with `after_id` (the api caps one page at
 * 1000). The Timeline and Raw events tabs share this query.
 */
export function useAllSessionEvents(sessionId: string) {
  return useQuery({
    queryKey: ["sessions", sessionId, "events", "all"] as const,
    enabled: sessionId.length > 0,
    queryFn: async () => {
      const items: SessionEventOut[] = [];
      let afterId: number | undefined;
      for (;;) {
        const page = await api.get<SessionEventPage>(`sessions/${sessionId}/events`, {
          limit: EVENTS_PAGE,
          after_id: afterId,
        });
        items.push(...page.items);
        if (page.items.length < EVENTS_PAGE || items.length >= EVENTS_MAX) break;
        afterId = page.items[page.items.length - 1].id;
      }
      return { items, truncated: items.length >= EVENTS_MAX };
    },
  });
}

/**
 * Connection names for the list filter and the detail header. V2-13 owns the
 * connection hooks; this is a plain read of the same `GET /v1/connections`.
 * Callers fall back to showing the id when it fails.
 */
export function useConnectionNames() {
  return useQuery({
    queryKey: ["connections", "list"] as const,
    queryFn: () => api.get<ConnectionPage>("connections"),
    retry: false,
    staleTime: 60_000,
    select: (page) => new Map(page.items.map((connection) => [connection.id, connection.name])),
  });
}

/**
 * The session's agent, for the panel tab. Same key as `useAgent` (shared
 * cache) but no retries: a deleted agent answers 404 and the tab should fall
 * back to the generic panel at once, not after react-query's ~7 s of retries.
 */
export function useSessionAgent(agentId: string) {
  return useQuery({
    queryKey: ["agents", agentId] as const,
    queryFn: () => api.get<AgentOut>(`agents/${agentId}`),
    enabled: agentId.length > 0,
    retry: false,
  });
}
