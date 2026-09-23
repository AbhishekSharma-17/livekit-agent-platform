"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * Read-only lookup of the agent's bound connection for the header chip and
 * the summary rail. V2-13 owns the connection hooks (`hooks/useConnections.ts`)
 * and the change flow; this stays a single GET with the same query key shape
 * so their cache and ours agree. The api may not serve `/v1/connections/{id}`
 * yet — callers render a neutral fallback on error.
 */
export function useAgentConnection(connectionId: string | null | undefined) {
  return useQuery({
    queryKey: ["connections", connectionId],
    queryFn: () => api.get<ConnectionOut>(`connections/${connectionId}`),
    enabled: Boolean(connectionId),
    retry: false,
    staleTime: 60_000,
  });
}

export function connectionTypeLabel(connection: Pick<ConnectionOut, "deployment_type">): string {
  return connection.deployment_type === "self_hosted" ? "Self-hosted" : "Cloud";
}
