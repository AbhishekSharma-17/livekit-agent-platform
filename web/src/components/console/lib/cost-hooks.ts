"use client";

import * as React from "react";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  CostAssumptionsOut,
  CostDriver,
  CostEstimate,
  CostEstimateRequest,
  PriceQuoteItemIn,
  PriceQuotesResponse,
  WorkspacePrice,
  WorkspacePricesOut,
} from "@/contracts/lkap-contracts";

/**
 * V4-16's cost hooks (docs/v4/COSTS.md §5), kept out of `lib/api-hooks.ts` on
 * the coordinator's instruction (V5-22 runs in parallel on that file):
 * `POST /v1/cost-estimates`, `POST /v1/pricing/quotes`, `GET
 * /v1/cost-estimates/assumptions`, `GET`/`PUT /v1/workspace/prices`.
 *
 * Every figure these return is an estimate at list prices, never a bill
 * (R-V4-50) — callers must keep the "estimate" word and the source/date next
 * to any number they render from here.
 */

/** Stable across key order, so two logically-equal requests share one cache entry / one network call. */
function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

const keys = {
  costEstimate: (request: CostEstimateRequest) => ["cost-estimate", stableStringify(request)] as const,
  assumptions: ["cost-assumptions"] as const,
  priceQuotes: (items: PriceQuoteItemIn[]) => ["price-quotes", stableStringify(items)] as const,
  workspacePrices: ["workspace-prices"] as const,
};

/**
 * `POST /v1/cost-estimates`. `request === null` disables the query (no
 * source to estimate yet — e.g. the editor hasn't resolved a draft config).
 * `retry: false` because a draft with a validation error answers 422, which
 * is a shape to show ("can't estimate yet"), not something to retry;
 * `placeholderData` keeps the last good figure on screen while a debounced
 * edit is in flight, so the rail never flickers to a loading state.
 */
export function useCostEstimate(request: CostEstimateRequest | null) {
  return useQuery<CostEstimate>({
    queryKey: keys.costEstimate(request ?? {}),
    queryFn: () => api.post<CostEstimate>("cost-estimates", request),
    enabled: request !== null,
    retry: false,
    placeholderData: keepPreviousData,
  });
}

/** `GET /v1/cost-estimates/assumptions` — the workspace's effective assumptions and how many sessions they came from. */
export function useCostAssumptions() {
  return useQuery<CostAssumptionsOut>({
    queryKey: keys.assumptions,
    queryFn: () => api.get<CostAssumptionsOut>("cost-estimates/assumptions"),
    staleTime: 60_000,
  });
}

/**
 * `POST /v1/pricing/quotes` — up to 100 provider/model pairs, each with its
 * own per-minute share. Items are sorted before hashing into the query key so
 * two callers asking for the same set in a different order share one request
 * (the model combobox's "one POST per open").
 */
export function usePriceQuotes(items: PriceQuoteItemIn[], options?: { enabled?: boolean }) {
  const sorted = React.useMemo(
    () => [...items].sort((a, b) => `${a.provider_id}:${a.model ?? ""}`.localeCompare(`${b.provider_id}:${b.model ?? ""}`)),
    [items],
  );
  return useQuery<PriceQuotesResponse>({
    queryKey: keys.priceQuotes(sorted),
    queryFn: () => api.post<PriceQuotesResponse>("pricing/quotes", { items: sorted }),
    enabled: (options?.enabled ?? true) && sorted.length > 0,
    staleTime: 5 * 60_000,
  });
}

/** `GET /v1/workspace/prices` — every admin has read; only an admin can write (`useUpdateWorkspacePrices`). */
export function useWorkspacePrices() {
  return useQuery<WorkspacePricesOut>({
    queryKey: keys.workspacePrices,
    queryFn: () => api.get<WorkspacePricesOut>("workspace/prices"),
  });
}

/**
 * `PUT /v1/workspace/prices` — replaces the **full** stored list (the route's
 * own contract), so callers must pass the complete list back (stored rows
 * plus edits/additions), never a subset. A successful save invalidates every
 * cost estimate and price quote in the cache, since a new price changes them
 * all.
 */
export function useUpdateWorkspacePrices() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (prices: WorkspacePrice[]) => api.put<WorkspacePricesOut>("workspace/prices", { prices }),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.workspacePrices, data);
      void queryClient.invalidateQueries({ queryKey: ["cost-estimate"] });
      void queryClient.invalidateQueries({ queryKey: ["price-quotes"] });
    },
  });
}

// ---- per-viewer estimate settings (R-V4-46: a localStorage convenience, never an AgentConfig field) ----

export interface EstimateSettings {
  /** Overrides by assumption key (`GET .../assumptions`'s keys); empty = the defaults/workspace averages. */
  assumptions: Record<string, number | string>;
  /** "Use my workspace's averages" (`CostEstimateRequest.workspace_averages`). */
  workspaceAverages: boolean;
  channel: "web" | "phone" | "text";
}

export const DEFAULT_ESTIMATE_SETTINGS: EstimateSettings = {
  assumptions: {},
  workspaceAverages: false,
  channel: "web",
};

function settingsStorageKey(scope: string): string {
  return `lkap:cost-estimate:${scope}`;
}

const settingsCache = new Map<string, EstimateSettings>();
const settingsListeners = new Map<string, Set<() => void>>();

function readSettings(scope: string): EstimateSettings {
  const cached = settingsCache.get(scope);
  if (cached) return cached;
  let value = DEFAULT_ESTIMATE_SETTINGS;
  try {
    const raw = typeof window === "undefined" ? null : window.localStorage.getItem(settingsStorageKey(scope));
    if (raw) value = { ...DEFAULT_ESTIMATE_SETTINGS, ...(JSON.parse(raw) as Partial<EstimateSettings>) };
  } catch {
    // Private browsing, disabled storage, or malformed JSON: fall back to defaults.
  }
  settingsCache.set(scope, value);
  return value;
}

function writeSettings(scope: string, next: EstimateSettings): void {
  settingsCache.set(scope, next);
  try {
    window.localStorage.setItem(settingsStorageKey(scope), JSON.stringify(next));
  } catch {
    // Best-effort: the in-memory cache still updates every open tab this session.
  }
  for (const listener of settingsListeners.get(scope) ?? []) listener();
}

/**
 * Clears one scope's cached/stored estimate settings, or every scope when
 * omitted. Exported for tests: the cache is module-level (by design, so
 * every surface reading the same agent shares one object), which means
 * settings otherwise leak between `it()` blocks in the same test file that
 * reuse an agent id — call this in `beforeEach`.
 */
export function resetEstimateSettings(scope?: string): void {
  if (scope) {
    settingsCache.delete(scope);
    try {
      window.localStorage.removeItem(settingsStorageKey(scope));
    } catch {
      // ignore
    }
    return;
  }
  const scopes = [...settingsCache.keys()];
  settingsCache.clear();
  try {
    for (const key of scopes) window.localStorage.removeItem(settingsStorageKey(key));
  } catch {
    // ignore
  }
}

/**
 * The estimate dialog's assumption overrides, "Use my workspace's averages"
 * and channel, shared by every surface that estimates the same agent (or
 * draft, under `"draft"`) so "the header figure equals the rail's" holds by
 * construction — same settings, same request, same react-query cache entry.
 * Per-viewer only (`localStorage`), exactly as R-V4-46 specifies; never sent
 * anywhere except folded into a `CostEstimateRequest`.
 */
export function useEstimateSettings(scope: string | undefined): readonly [EstimateSettings, (next: Partial<EstimateSettings>) => void] {
  const key = scope ?? "draft";
  const subscribe = React.useCallback(
    (onStoreChange: () => void) => {
      let set = settingsListeners.get(key);
      if (!set) {
        set = new Set();
        settingsListeners.set(key, set);
      }
      set.add(onStoreChange);
      return () => set?.delete(onStoreChange);
    },
    [key],
  );
  const getSnapshot = React.useCallback(() => readSettings(key), [key]);
  const getServerSnapshot = React.useCallback(() => DEFAULT_ESTIMATE_SETTINGS, []);
  const value = React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const setValue = React.useCallback(
    (next: Partial<EstimateSettings>) => writeSettings(key, { ...readSettings(key), ...next }),
    [key],
  );
  return [value, setValue] as const;
}

// ---- formatting ----

/** `$0.0048`, 4 decimals under $1 (so a fractional-cent line never rounds to "$0.00"), else 2. */
export function formatUsd(value: number | string | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return null;
  const digits = n !== 0 && Math.abs(n) < 1 ? 4 : 2;
  return `$${n.toFixed(digits)}`;
}

/** "≈ $0.0400/min" — null when there is nothing priced to show. Callers add their own "· estimate". */
export function formatUsdPerMin(value: number | string | null | undefined): string | null {
  const usd = formatUsd(value);
  return usd === null ? null : `≈ ${usd}/min`;
}

/**
 * Plain-language names for `EstimateSlot`/`CostDriver.slot` (D-V4-47), shared
 * by the Cost tab's "Why it differs" list and analytics' driver table so the
 * same part of the agent is always named the same way.
 */
export const SLOT_LABELS: Record<CostDriver["slot"], string> = {
  stt: "Caller's speech → text",
  llm: "Agent's thinking",
  tts: "Agent's voice",
  realtime: "Agent's thinking and voice",
  avatar: "Video avatar",
  workflow_llm: "Flow step",
  image_gen: "Image generation",
  embedding: "Knowledge lookups",
  turn_detection: "Turn detection",
  vad: "Voice detection",
  livekit_agent: "Call minutes",
  livekit_participant: "Participant minutes",
  livekit_sip: "Phone minutes",
  livekit_egress: "Recording",
  qa_judge: "Quality review",
};

/** One sentence per driver, naming the quantity and the delta (COSTS.md §4.3's "Why it differs"). */
export function driverSentence(driver: CostDriver): string {
  const label = SLOT_LABELS[driver.slot] ?? driver.slot;
  const actual = driver.actual_quantity != null ? Number(driver.actual_quantity).toLocaleString() : null;
  const estimated = driver.estimated_quantity != null ? Number(driver.estimated_quantity).toLocaleString() : null;
  const delta = driver.delta_usd != null ? Number(driver.delta_usd) : null;
  const deltaText = delta != null ? `${delta >= 0 ? "+" : "−"}${formatUsd(Math.abs(delta)) ?? "$0"}` : null;
  const quantities = actual != null && estimated != null ? ` (${actual} vs ${estimated} expected)` : "";
  const tail = deltaText ? ` → ${deltaText}` : "";
  return `${label}: ${driver.reason}${quantities}${tail}`;
}
