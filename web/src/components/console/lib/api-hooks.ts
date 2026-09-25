"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { isSendableModelId, modelIdPath } from "@/lib/model-ids";
import { uploadKbDocument } from "@/components/console/lib/upload";
import type {
  AgentCreate,
  AgentOut,
  AgentPage,
  AgentUpdate,
  CredentialCreate,
  CredentialOut,
  CredentialPage,
  CredentialTestResult,
  CredentialUpdate,
  HealthResponse,
  KbCreate,
  KbDocumentOut,
  KbDocumentPage,
  KbOut,
  KbPage,
  KbSearchRequest,
  KbSearchResponse,
  ModelCapabilities,
  ModelTestRequest,
  ModelTestResult,
  PacksResponse,
  ProviderModelOut,
  ProviderModelPage,
  ProvidersResponse,
  SessionDetailOut,
  SessionEventPage,
  SessionPage,
  ToolCreate,
  ToolDryRunRequest,
  ToolDryRunResult,
  ToolOut,
  ToolPage,
  ValidationResult,
} from "@/contracts/lkap-contracts";

/**
 * Typed react-query hooks over `@/lib/api`'s generic `apiRequest`. All calls
 * go through the server-side proxy (`src/app/api/console/[...path]/route.ts`)
 * which attaches `X-Admin-Token`; nothing here ever sees that token. Route
 * shapes are `lkap_api` per docs/CONTRACTS.md §7.
 */

const keys = {
  health: ["health"] as const,
  providers: ["providers"] as const,
  packs: ["packs"] as const,
  agents: ["agents"] as const,
  agent: (id: string) => ["agents", id] as const,
  credentials: (providerId?: string) => ["credentials", providerId ?? "all"] as const,
  tools: (agentId?: string) => ["tools", agentId ?? "all"] as const,
  kbs: ["knowledge-bases"] as const,
  kb: (id: string) => ["knowledge-bases", id] as const,
  kbDocuments: (id: string) => ["knowledge-bases", id, "documents"] as const,
  sessions: (agentId?: string, status?: string) => ["sessions", agentId ?? "", status ?? ""] as const,
  session: (id: string) => ["sessions", id] as const,
  sessionEvents: (id: string) => ["sessions", id, "events"] as const,
  /** Every model-record query of one provider (`["provider-models", providerId, …]`), for invalidation. */
  providerModelsAll: (providerId: string) => ["provider-models", providerId] as const,
  providerModels: (providerId: string, params: { custom: boolean; limit?: number }) =>
    ["provider-models", providerId, "list", params.custom, params.limit ?? null] as const,
  providerModel: (providerId: string, modelId: string) => ["provider-models", providerId, "one", modelId] as const,
};


// ---- health ----

/** `GET /v1/health` (public route; goes through the proxy like everything else). */
export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => api.get<HealthResponse>("health"),
    staleTime: 30_000,
  });
}

// ---- providers / packs ----

export function useProviders(options?: Partial<UseQueryOptions<ProvidersResponse>>) {
  return useQuery({
    queryKey: keys.providers,
    queryFn: () => api.get<ProvidersResponse>("providers"),
    staleTime: 5 * 60_000,
    ...options,
  });
}

export function usePacks() {
  return useQuery({
    queryKey: keys.packs,
    queryFn: () => api.get<PacksResponse>("packs"),
    staleTime: 5 * 60_000,
  });
}

// ---- provider model records, Test model (V4-09; docs/v4/CUSTOM-MODELS.md D-V4-24, D-V4-26) ----

/**
 * `GET /v1/providers/{id}/models?custom=&limit=` — the workspace's records for
 * this provider's credential home and kind, most recently tested first
 * ("Your custom models" in the model combobox; the Catalog dialog's chips).
 */
export function useProviderModels(
  providerId: string | undefined,
  params: { custom?: boolean; limit?: number } = {},
  options?: { enabled?: boolean },
) {
  const custom = params.custom ?? false;
  return useQuery({
    queryKey: keys.providerModels(providerId ?? "", { custom, limit: params.limit }),
    queryFn: () =>
      api.get<ProviderModelPage>(`providers/${providerId}/models`, {
        custom: custom || undefined,
        limit: params.limit,
      }),
    enabled: Boolean(providerId) && (options?.enabled ?? true),
    staleTime: 60_000,
  });
}

/**
 * `GET /v1/providers/{id}/models/{model_id}` — one record, or `null` when the
 * workspace has none (404: never tested, declared or seen).
 *
 * The id travels in the URL path, so the query is **never** enabled for a
 * value that fails the model-id rule (R-V4-32), whatever the caller passes.
 */
export function useProviderModel(
  providerId: string | undefined,
  modelId: string | null | undefined,
  options?: { enabled?: boolean },
) {
  const sendable = isSendableModelId(modelId);
  return useQuery<ProviderModelOut | null, ApiError>({
    queryKey: keys.providerModel(providerId ?? "", sendable ? modelId : ""),
    queryFn: async () => {
      try {
        return await api.get<ProviderModelOut>(`providers/${providerId}/models/${modelIdPath(modelId as string)}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(providerId) && sendable && (options?.enabled ?? true),
    retry: false,
    throwOnError: false,
    staleTime: 60_000,
  });
}

/**
 * `POST /v1/providers/{id}/test-model` — one capped, real vendor call. Refuses
 * locally (no request) for an id that fails the model-id rule. On success the
 * provider's records refetch, so the tested chip and "Your custom models" update.
 */
export function useTestModel(providerId: string) {
  const queryClient = useQueryClient();
  return useMutation<ModelTestResult, Error, ModelTestRequest>({
    mutationFn: async (body) => {
      if (!isSendableModelId(body.model)) {
        throw new ApiError(422, "invalid_model_id", "The model id can't be tested: fix it first.");
      }
      return api.post<ModelTestResult>(`providers/${providerId}/test-model`, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.providerModelsAll(providerId) });
    },
  });
}

/** `PUT /v1/providers/{id}/models/{model_id}` `{declared}` — admin only; refuses locally for a failing id. */
export function useDeclareModel(providerId: string) {
  const queryClient = useQueryClient();
  return useMutation<ProviderModelOut, Error, { modelId: string; declared: ModelCapabilities }>({
    mutationFn: async ({ modelId, declared }) => {
      if (!isSendableModelId(modelId)) {
        throw new ApiError(422, "invalid_model_id", "The model id can't be saved: fix it first.");
      }
      return api.put<ProviderModelOut>(`providers/${providerId}/models/${modelIdPath(modelId)}`, { declared });
    },
    onSuccess: (record, { modelId }) => {
      queryClient.setQueryData(keys.providerModel(providerId, modelId), record);
      void queryClient.invalidateQueries({
        queryKey: keys.providerModelsAll(providerId),
        predicate: (query) => query.queryKey[2] === "list",
      });
    },
  });
}

// ---- agents ----

export function useAgents() {
  return useQuery({
    queryKey: keys.agents,
    queryFn: () => api.get<AgentPage>("agents"),
  });
}

export function useAgent(id: string) {
  return useQuery({
    queryKey: keys.agent(id),
    queryFn: () => api.get<AgentOut>(`agents/${id}`),
    enabled: id.length > 0,
  });
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentCreate) => api.post<AgentOut>("agents", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useUpdateAgent(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentUpdate) => api.put<AgentOut>(`agents/${id}`, body),
    onSuccess: (agent) => {
      queryClient.setQueryData(keys.agent(id), agent);
      void queryClient.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`agents/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useValidateAgent(id: string) {
  return useMutation({
    mutationFn: () => api.post<ValidationResult>(`agents/${id}/validate`),
  });
}

// ---- credentials ----

export function useCredentials(providerId?: string) {
  return useQuery({
    queryKey: keys.credentials(providerId),
    queryFn: () => api.get<CredentialPage>("credentials", providerId ? { provider_id: providerId } : undefined),
  });
}

export function useCreateCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CredentialCreate) => api.post<CredentialOut>("credentials", body),
    onSuccess: (credential) => {
      void queryClient.invalidateQueries({ queryKey: keys.credentials(credential.provider_id) });
      void queryClient.invalidateQueries({ queryKey: keys.credentials(undefined) });
    },
  });
}

/** `PUT /v1/credentials/{id}` — omit `secrets` to keep the stored values (rename / rotate). */
export function useUpdateCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CredentialUpdate }) =>
      api.put<CredentialOut>(`credentials/${id}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    },
  });
}

/** `POST /v1/credentials/{id}/test` — a cheap vendor call; `ok=false` carries the reason in `message`. */
export function useTestCredential() {
  return useMutation({
    mutationFn: (id: string) => api.post<CredentialTestResult>(`credentials/${id}/test`),
  });
}

export function useDeleteCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`credentials/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    },
  });
}

// ---- tools ----

export function useTools(agentId?: string) {
  return useQuery({
    queryKey: keys.tools(agentId),
    queryFn: () => api.get<ToolPage>("tools", agentId ? { agent_id: agentId } : undefined),
  });
}

export function useCreateTool() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ToolCreate) => api.post<ToolOut>("tools", body),
    onSuccess: (tool) => {
      void queryClient.invalidateQueries({ queryKey: keys.tools(tool.agent_id ?? undefined) });
      void queryClient.invalidateQueries({ queryKey: keys.tools(undefined) });
    },
  });
}

export function useUpdateTool() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ToolCreate }) => api.put<ToolOut>(`tools/${id}`, body),
    onSuccess: (tool) => {
      void queryClient.invalidateQueries({ queryKey: keys.tools(tool.agent_id ?? undefined) });
      void queryClient.invalidateQueries({ queryKey: keys.tools(undefined) });
    },
  });
}

export function useDeleteTool() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`tools/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

export function useDryRunTool() {
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ToolDryRunRequest }) =>
      api.post<ToolDryRunResult>(`tools/${id}/dry-run`, body),
  });
}

// ---- knowledge bases ----

export function useKbs() {
  return useQuery({
    queryKey: keys.kbs,
    queryFn: () => api.get<KbPage>("knowledge-bases"),
  });
}

export function useKb(id: string) {
  return useQuery({
    queryKey: keys.kb(id),
    queryFn: () => api.get<KbOut>(`knowledge-bases/${id}`),
    enabled: id.length > 0,
  });
}

export function useCreateKb() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: KbCreate) => api.post<KbOut>("knowledge-bases", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.kbs });
    },
  });
}

export function useDeleteKb() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`knowledge-bases/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.kbs });
    },
  });
}

export function useKbDocuments(kbId: string, options?: { pollWhilePending?: boolean }) {
  return useQuery({
    queryKey: keys.kbDocuments(kbId),
    queryFn: () => api.get<KbDocumentPage>(`knowledge-bases/${kbId}/documents`),
    enabled: kbId.length > 0,
    refetchInterval: (query) => {
      if (!options?.pollWhilePending) return false;
      const data = query.state.data;
      const hasPending = data?.items.some((doc: KbDocumentOut) => doc.status === "pending") ?? false;
      return hasPending ? 2000 : false;
    },
  });
}

export function useUploadKbDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadKbDocument(kbId, file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.kbDocuments(kbId) });
      void queryClient.invalidateQueries({ queryKey: keys.kb(kbId) });
    },
  });
}

export function useDeleteKbDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) => api.delete<void>(`knowledge-bases/${kbId}/documents/${docId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.kbDocuments(kbId) });
      void queryClient.invalidateQueries({ queryKey: keys.kb(kbId) });
    },
  });
}

export function useKbSearch(kbId: string) {
  return useMutation({
    mutationFn: (body: KbSearchRequest) => api.post<KbSearchResponse>(`knowledge-bases/${kbId}/search`, body),
  });
}

// ---- sessions ----

/**
 * Sessions list. `limit` is applied client-side (`select` slices `items`;
 * `total` is unchanged) so the overview's "8 most recent" shares the cache
 * with the full list.
 */
export function useSessions(agentId?: string, status?: string, limit?: number) {
  return useQuery({
    queryKey: keys.sessions(agentId, status),
    queryFn: () =>
      api.get<SessionPage>("sessions", {
        agent_id: agentId || undefined,
        status: status || undefined,
      }),
    select: limit === undefined ? undefined : (page: SessionPage) => ({ ...page, items: page.items.slice(0, limit) }),
  });
}

export function useSessionDetail(id: string) {
  return useQuery({
    queryKey: keys.session(id),
    queryFn: () => api.get<SessionDetailOut>(`sessions/${id}`),
    enabled: id.length > 0,
  });
}

export function useSessionEvents(id: string) {
  return useQuery({
    queryKey: keys.sessionEvents(id),
    queryFn: () => api.get<SessionEventPage>(`sessions/${id}/events`),
    enabled: id.length > 0,
  });
}
