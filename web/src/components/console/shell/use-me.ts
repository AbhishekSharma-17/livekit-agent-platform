"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import type { Me } from "@/contracts/lkap-contracts";

/**
 * `GET /v1/auth/me` (docs/v2/CONTRACTS-V2.md §3.1) — built by V2-02, which is
 * landing in parallel with this package. Until it ships the route stub
 * (V2-01) returns 404, which this hook treats as "auth isn't wired up yet"
 * rather than an error: the console keeps working in the v1 admin-token
 * mode, the workspace switcher stays hidden, and the shell's "can't reach
 * the API" banner (query-provider.tsx) does not count it.
 *
 * A 401 means the opposite — a real session exists and expired/was never
 * created — and is handled separately by the query cache's global `onError`
 * (redirects to `/login`), not here.
 */
export interface MeState {
  me: Me | undefined;
  /** The endpoint isn't implemented yet (404) — not the same as "signed out". */
  unavailable: boolean;
  isLoading: boolean;
}

export function useMe(): MeState {
  const query = useQuery<Me, ApiError>({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<Me>("auth/me"),
    retry: false,
    staleTime: 5 * 60_000,
    // 401 is handled globally (redirect); 404 means "not built yet" and
    // should not surface as a thrown query error other hooks have to guard.
    throwOnError: false,
  });

  const unavailable = query.error instanceof ApiError && query.error.status === 404;

  return {
    me: query.data,
    unavailable,
    isLoading: query.isLoading,
  };
}
