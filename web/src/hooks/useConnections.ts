"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type {
  ConnectionCreate,
  ConnectionOut,
  ConnectionPage,
  ConnectionRotateIn,
  ConnectionTestResult,
  ConnectionUpdate,
  FleetActionIn,
  FleetStatus,
} from "@/contracts/lkap-contracts";

/**
 * Connections (V2-13): `GET/POST /v1/connections`, `/{id}`, `/test`
 * (pre-save), `/{id}/test`, `/{id}/rotate`, `/{id}/default`, `/{id}/fleet`
 * (V2-04's routes, docs/v2/_asks.md #48), `/{id}/worker-env` and
 * `/{id}/deploy-bundle` (the latter two are not JSON — see
 * `fetchWorkerEnv`/`downloadDeployBundle` below, which bypass `@/lib/api`'s
 * `apiRequest` on purpose).
 *
 * Everything here goes through the same `/api/console/*` proxy as
 * `components/console/lib/api-hooks.ts`; kept in a separate module per the
 * V2-13 package card (`web/src/hooks/useConnections.ts`) rather than
 * appended to that file, which V2-13 does not own.
 */

const keys = {
  all: ["connections"] as const,
  detail: (id: string) => ["connections", id] as const,
  fleet: (id: string) => ["connections", id, "fleet"] as const,
};

export function useConnections() {
  return useQuery({
    queryKey: keys.all,
    queryFn: () => api.get<ConnectionPage>("connections"),
    staleTime: 30_000,
  });
}

export function useConnection(id: string) {
  return useQuery({
    queryKey: keys.detail(id),
    queryFn: () => api.get<ConnectionOut>(`connections/${id}`),
    enabled: id.length > 0,
  });
}

/** The workspace's default connection, or the first one — used where a slot editor needs *a* connection to gate against before one is explicitly chosen. */
export function useDefaultConnection() {
  const query = useConnections();
  const items = query.data?.items ?? [];
  const connection = items.find((c) => c.is_default) ?? items[0];
  return { ...query, connection };
}

export function useCreateConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConnectionCreate) => api.post<ConnectionOut>("connections", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

export function useUpdateConnection(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConnectionUpdate) => api.put<ConnectionOut>(`connections/${id}`, body),
    onSuccess: (connection) => {
      queryClient.setQueryData(keys.detail(id), connection);
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

export function useDeleteConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`connections/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

/** `POST /v1/connections/test` — probes unsaved create-form details; nothing is stored. */
export function useTestUnsavedConnection() {
  return useMutation({
    mutationFn: (body: ConnectionCreate) => api.post<ConnectionTestResult>("connections/test", body),
  });
}

/** `POST /v1/connections/{id}/test` — probes and persists the result. */
export function useTestConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<ConnectionTestResult>(`connections/${id}/test`),
    onSuccess: (_result, id) => {
      void queryClient.invalidateQueries({ queryKey: keys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

export function useRotateConnection(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConnectionRotateIn) => api.post<ConnectionOut>(`connections/${id}/rotate`, body),
    onSuccess: (connection) => {
      queryClient.setQueryData(keys.detail(id), connection);
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

export function useSetDefaultConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<ConnectionOut>(`connections/${id}/default`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

/** Fleet status (docs/v2/_asks.md #48); the detail page polls while a restart/rolling replace is likely in flight. */
export function useConnectionFleet(id: string, options?: { poll?: boolean }) {
  return useQuery({
    queryKey: keys.fleet(id),
    queryFn: () => api.get<FleetStatus>(`connections/${id}/fleet`),
    enabled: id.length > 0,
    refetchInterval: options?.poll ? 5_000 : false,
  });
}

export function useFleetAction(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: FleetActionIn) => api.post<FleetStatus>(`connections/${id}/fleet`, body),
    onSuccess: (status) => {
      queryClient.setQueryData(keys.fleet(id), status);
    },
  });
}

export type WorkerEnvFormat = "env" | "compose" | "lk";

/**
 * `GET /v1/connections/{id}/worker-env?format=` returns `text/plain`, not
 * JSON — `@/lib/api`'s `apiRequest` always calls `.json()`, so this bypasses
 * it and talks to the same `/api/console/*` proxy directly (the proxy is a
 * byte-for-byte pass-through, see `app/api/console/[...path]/route.ts`).
 */
export async function fetchWorkerEnv(id: string, format: WorkerEnvFormat): Promise<string> {
  const response = await fetch(`/api/console/connections/${id}/worker-env?format=${format}`);
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new ApiError(response.status, "worker_env_failed", text || `Request failed (${response.status})`);
  }
  return response.text();
}

/**
 * `POST /v1/connections/{id}/deploy-bundle` returns a zip. Fetches it as a
 * blob and triggers a browser download — never routed through `apiRequest`.
 */
export async function downloadDeployBundle(id: string, filenameHint: string): Promise<void> {
  const response = await fetch(`/api/console/connections/${id}/deploy-bundle`, { method: "POST" });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "error" in body) {
        const err = (body as { error?: { message?: string } }).error;
        if (err?.message) message = err.message;
      }
    } catch {
      // ignore — keep the generic message
    }
    throw new ApiError(response.status, "deploy_bundle_failed", message);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `${filenameHint}.zip`;
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
