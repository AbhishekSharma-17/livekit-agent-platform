"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  AgentOut,
  ConfigVersionOut,
  ConfigVersionPage,
  FlowSpec,
  NodeSpecsResponse,
  ValidationResult,
} from "@/contracts/lkap-contracts";
import { api, apiRequest } from "@/lib/api";

/**
 * V2-16's hooks: node specs, draft-flow validation and the agent config
 * versions (`GET/POST /v1/agents/{id}/versions…`). Kept out of the shared
 * `api-hooks.ts`; the agent query key matches it (`["agents", id]`) so a
 * restore refreshes the editor.
 */

export const flowKeys = {
  nodeSpecs: ["flows", "node-specs"] as const,
  versions: (agentId: string) => ["agents", agentId, "versions"] as const,
  version: (agentId: string, n: number) => ["agents", agentId, "versions", n] as const,
  validate: (agentId: string, draft: string) => ["agents", agentId, "flow-validate", draft] as const,
};

export function useNodeSpecs() {
  return useQuery({
    queryKey: flowKeys.nodeSpecs,
    queryFn: () => api.get<NodeSpecsResponse>("flows/node-specs"),
    staleTime: Infinity,
  });
}

export function useAgentVersions(agentId: string, enabled = true) {
  return useQuery({
    queryKey: flowKeys.versions(agentId),
    queryFn: () => api.get<ConfigVersionPage>(`agents/${agentId}/versions`, { limit: 100 }),
    enabled,
  });
}

export function useAgentVersion(agentId: string, configVersion: number | null) {
  return useQuery({
    queryKey: flowKeys.version(agentId, configVersion ?? -1),
    queryFn: () => api.get<ConfigVersionOut>(`agents/${agentId}/versions/${configVersion}`),
    enabled: configVersion !== null,
    staleTime: Infinity,
  });
}

export function useRestoreVersion(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (configVersion: number) =>
      api.post<AgentOut>(`agents/${agentId}/versions/${configVersion}/restore`),
    onSuccess: (agent) => {
      queryClient.setQueryData(["agents", agentId], agent);
      void queryClient.invalidateQueries({ queryKey: flowKeys.versions(agentId) });
      void queryClient.invalidateQueries({ queryKey: ["agents"], exact: true });
    },
  });
}

/**
 * Validates an unsaved flow on the server (`POST /v1/agents/{id}/flow/validate`):
 * tool/knowledge references, provider overrides and variables the client can't
 * check. `draftJson` is the debounced, serialised draft; `null` skips the call.
 */
export function useFlowValidation(agentId: string, draftJson: string | null) {
  return useQuery({
    queryKey: flowKeys.validate(agentId, draftJson ?? ""),
    queryFn: ({ signal }) =>
      apiRequest<ValidationResult>(`agents/${agentId}/flow/validate`, {
        method: "POST",
        body: { flow: JSON.parse(draftJson ?? "{}") as FlowSpec },
        signal,
      }),
    enabled: draftJson !== null,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
}
