"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import type { ConnectionPage, WebhookEndpointPage } from "@/contracts/lkap-contracts";

/**
 * Setup-checklist rows for destinations owned by packages landing in
 * parallel (V2-03 connections, V2-08 webhooks): both routers exist today
 * only as the empty stubs V2-01 wired up, so `GET /v1/connections` and
 * `GET /v1/webhooks` 404. That is "not done yet", not an error — these
 * probes report `{total: 0, available: false}` on a 404 instead of
 * throwing, so the checklist row renders its normal "not done" state and
 * the shell's API-health banner (which only counts network/5xx failures)
 * never sees it.
 */
interface ProbeResult {
  total: number;
  /** False when the endpoint isn't implemented yet. */
  available: boolean;
  isLoading: boolean;
}

interface ProbeQueryData {
  total: number;
  available: boolean;
}

function useCountProbe(key: string, path: string): ProbeResult {
  const query = useQuery<ProbeQueryData, ApiError>({
    queryKey: ["overview-probe", key],
    queryFn: async () => {
      try {
        if (path === "connections") {
          const page = await api.get<ConnectionPage>(path);
          return { total: page.total, available: true };
        }
        const page = await api.get<WebhookEndpointPage>(path);
        return { total: page.total, available: true };
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          return { total: 0, available: false };
        }
        throw error;
      }
    },
    retry: false,
    staleTime: 30_000,
  });

  return {
    total: query.data?.total ?? 0,
    available: query.data?.available ?? false,
    isLoading: query.isLoading,
  };
}

export function useConnectionsProbe(): ProbeResult {
  return useCountProbe("connections", "connections");
}

export function useWebhooksProbe(): ProbeResult {
  return useCountProbe("webhooks", "webhooks");
}
