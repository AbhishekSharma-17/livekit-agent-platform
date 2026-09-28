"use client";

/**
 * Ready-made panels a builder can start from (V6-08 → V6-10, ask #56):
 * `GET /v1/panels/presets` — the "Notebook" preset today. Used by the panel
 * composer's "Start from a preset" control and the New agent dialog's
 * "Starting panel" choice.
 *
 * Same shape as `agents/create/use-templates.ts`'s fallback: an api older
 * than V6-08 has no `panels/presets` route, so a 404 is "no presets" rather
 * than an error — the caller just hides the control.
 */
import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import type { PanelPresetsResponse } from "@/contracts/lkap-contracts";

export const panelPresetsKey = ["panels", "presets"] as const;

async function fetchPanelPresets(): Promise<PanelPresetsResponse["items"]> {
  try {
    const response = await api.get<PanelPresetsResponse>("panels/presets");
    return response.items;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return [];
    throw error;
  }
}

export function usePanelPresets() {
  return useQuery({
    queryKey: panelPresetsKey,
    queryFn: fetchPanelPresets,
    staleTime: 5 * 60_000,
  });
}
