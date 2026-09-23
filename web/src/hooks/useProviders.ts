"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { ProviderKind } from "@/components/console/registry/provider-meta";
import type { ProviderSettingsIn, ProviderOut, ProvidersResponse } from "@/contracts/lkap-contracts";

/**
 * The Providers catalog's own workspace-settings mutation (V2-13). The list
 * query itself (`GET /v1/providers`) already exists as `useProviders` in
 * `components/console/lib/api-hooks.ts` (WP-4, used by every slot editor) —
 * this module deliberately does not redeclare that name; `useProviderList`
 * below wraps it with the kind/availability/enabled filters the catalog page
 * needs, and shares its query key so a settings save invalidates both.
 */

const PROVIDERS_KEY = ["providers"] as const;

export interface ProviderListFilters {
  kind?: ProviderKind;
  availability?: "available" | "deferred" | "incompatible" | "removed";
  enabled?: boolean;
}

/** `GET /v1/providers?kind=&availability=&enabled=` — server-side filtering for the catalog page. */
export function useProviderList(filters: ProviderListFilters = {}) {
  return useQuery({
    queryKey: [...PROVIDERS_KEY, filters],
    queryFn: () =>
      api.get<ProvidersResponse>("providers", {
        kind: filters.kind,
        availability: filters.availability,
        enabled: filters.enabled,
      }),
    staleTime: 30_000,
  });
}

/** `PUT /v1/providers/{id}/settings` — enable/disable and set the default credential. */
export function useUpdateProviderSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProviderSettingsIn }) =>
      api.put<ProviderOut>(`providers/${id}/settings`, body),
    onSuccess: () => {
      // Shared with the bare `useProviders()` key (`["providers"]`) used by
      // every slot editor, so a settings change is reflected there too.
      void queryClient.invalidateQueries({ queryKey: PROVIDERS_KEY });
    },
  });
}
