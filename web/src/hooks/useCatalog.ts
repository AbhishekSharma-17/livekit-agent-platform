"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { CatalogResponse } from "@/contracts/lkap-contracts";

export type CatalogKind = "models" | "voices" | "avatars" | "personas";

/**
 * Search and paging over a catalog (docs/v4/CUSTOM-MODELS.md D-V4-25).
 *
 * The api answers **at most 200 items by default** (`limit` ≤ 1000); a caller
 * that wants "the whole list" to filter locally passes `limit: 1000`
 * (`CATALOG_FULL_LIMIT`). `q` searches the api's cached list, never the
 * vendor; `search_vendor: true` forwards `q` to the vendor, and only
 * OpenRouter entries honour it. `model` keeps one model's voices.
 */
export interface CatalogParams {
  q?: string;
  limit?: number;
  offset?: number;
  model?: string;
  search_vendor?: boolean;
}

/** The api's `MAX_PAGE_LIMIT`: the most one request can return. */
export const CATALOG_FULL_LIMIT = 1000;
/** The api's default page size, and the Catalog dialog's page. */
export const CATALOG_PAGE_SIZE = 200;

function cleanParams(params: CatalogParams | undefined): CatalogParams {
  const out: CatalogParams = {};
  if (!params) return out;
  if (params.q) out.q = params.q;
  if (params.limit !== undefined) out.limit = params.limit;
  if (params.offset) out.offset = params.offset;
  if (params.model) out.model = params.model;
  if (params.search_vendor) out.search_vendor = true;
  return out;
}

/**
 * `GET /v1/providers/{id}/catalog?kind=&credential_id=&refresh=&q=&limit=&offset=&model=&search_vendor=`
 * (V2-06, V4-07) — used by the Providers page's Catalog dialog, the model
 * combobox's *Catalog* group, and the catalog pickers in `registry-form.tsx`.
 *
 * `error` on the response is informational only (V2-06: "a failed vendor
 * call must never break the page") — `items`/`source` still carry a cached
 * or static fallback list, so callers render `items` regardless and surface
 * `error` as a dismissible note, never as a query error.
 */
export function catalogKey(
  providerId: string | undefined,
  kind: CatalogKind,
  credentialId?: string | null,
  params?: CatalogParams,
) {
  return ["catalog", providerId, kind, credentialId ?? null, cleanParams(params)] as const;
}

function catalogQuery(kind: CatalogKind, credentialId: string | null | undefined, params: CatalogParams | undefined) {
  return {
    kind,
    credential_id: credentialId ?? undefined,
    ...cleanParams(params),
  };
}

export interface UseCatalogOptions extends Partial<UseQueryOptions<CatalogResponse>> {
  params?: CatalogParams;
}

export function useCatalog(
  providerId: string | undefined,
  kind: CatalogKind,
  credentialId?: string | null,
  options?: UseCatalogOptions,
) {
  const { params, ...queryOptions } = options ?? {};
  return useQuery({
    queryKey: catalogKey(providerId, kind, credentialId, params),
    queryFn: () => api.get<CatalogResponse>(`providers/${providerId}/catalog`, catalogQuery(kind, credentialId, params)),
    enabled: Boolean(providerId),
    staleTime: 5 * 60_000,
    // Paging and searching keep the previous page on screen while the next loads.
    placeholderData: params?.offset !== undefined || params?.q !== undefined ? keepPreviousData : undefined,
    ...queryOptions,
  });
}

/**
 * Forces a live vendor refetch, bypassing the cache (`refresh=true`), and
 * updates `useCatalog`'s cache entry for the same `params` in place (other
 * pages of the same catalog are invalidated so they refetch from the new cache).
 */
export function useRefreshCatalog(
  providerId: string,
  kind: CatalogKind,
  credentialId?: string | null,
  params?: CatalogParams,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.get<CatalogResponse>(`providers/${providerId}/catalog`, {
        ...catalogQuery(kind, credentialId, params),
        refresh: true,
      }),
    onSuccess: (result) => {
      queryClient.setQueryData(catalogKey(providerId, kind, credentialId, params), result);
      void queryClient.invalidateQueries({
        queryKey: ["catalog", providerId, kind, credentialId ?? null],
        predicate: (query) => query.queryKey[4] !== undefined && JSON.stringify(query.queryKey[4]) !== JSON.stringify(cleanParams(params)),
      });
    },
  });
}
