"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { CatalogResponse } from "@/contracts/lkap-contracts";

export type CatalogKind = "models" | "voices" | "avatars" | "personas";

/**
 * `GET /v1/providers/{id}/catalog?kind=&credential_id=&refresh=` (V2-06) —
 * used by the Providers catalog page's "Catalog" preview button and by the
 * avatar/voice/model catalog pickers this package adds to `registry-form.tsx`.
 *
 * `error` on the response is informational only (V2-06: "a failed vendor
 * call must never break the page") — `items`/`source` still carry a cached
 * or static fallback list, so callers render `items` regardless and surface
 * `error` as a dismissible note, never as a query error.
 */
function catalogKey(providerId: string | undefined, kind: CatalogKind, credentialId?: string | null) {
  return ["catalog", providerId, kind, credentialId ?? null] as const;
}

export function useCatalog(
  providerId: string | undefined,
  kind: CatalogKind,
  credentialId?: string | null,
  options?: Partial<UseQueryOptions<CatalogResponse>>,
) {
  return useQuery({
    queryKey: catalogKey(providerId, kind, credentialId),
    queryFn: () =>
      api.get<CatalogResponse>(`providers/${providerId}/catalog`, {
        kind,
        credential_id: credentialId ?? undefined,
      }),
    enabled: Boolean(providerId),
    staleTime: 5 * 60_000,
    ...options,
  });
}

/** Forces a live vendor refetch, bypassing the cache (`refresh=true`), and updates `useCatalog`'s cache entry in place. */
export function useRefreshCatalog(providerId: string, kind: CatalogKind, credentialId?: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.get<CatalogResponse>(`providers/${providerId}/catalog`, {
        kind,
        credential_id: credentialId ?? undefined,
        refresh: true,
      }),
    onSuccess: (result) => {
      queryClient.setQueryData(catalogKey(providerId, kind, credentialId), result);
    },
  });
}
