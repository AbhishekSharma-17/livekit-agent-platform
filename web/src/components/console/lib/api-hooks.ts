"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { isSendableModelId, modelIdPath } from "@/lib/model-ids";
import { uploadKbDocument } from "@/components/console/lib/upload";
import type {
  AgentCreate,
  AgentTestRun,
  AgentTestRunIn,
  AgentTestRunPage,
  AgentOut,
  AgentPage,
  AgentUpdate,
  AppActionPage,
  AppActionsPickIn,
  AppActionsPickOut,
  AppConnectIn,
  AppConnectOut,
  AppConnectionOut,
  AppConnectionPage,
  AppKeyTestOut,
  AppReconnectIn,
  AppsStatusOut,
  ConnectionRenameIn,
  CredentialCreate,
  CredentialOut,
  CredentialPage,
  CredentialTestResult,
  CredentialUpdate,
  DatasetLookupIn,
  DatasetLookupOut,
  DatasetOut,
  DatasetPage,
  DatasetPreviewOut,
  HealthResponse,
  KbCreate,
  KbDocumentOut,
  KbDocumentPage,
  KbEvalRunOut,
  KbEvalSetIn,
  KbEvalSetOut,
  KbEvaluateIn,
  KbOut,
  KbPage,
  KbSearchRequest,
  KbSearchResponse,
  KbSourceOut,
  KnowledgeConnectionCreate,
  KnowledgeConnectionOut,
  KnowledgeConnectionPage,
  KnowledgeConnectionTestOut,
  KnowledgeConnectionUpdate,
  McpOauthStartIn,
  McpOauthStartOut,
  McpOauthStatusOut,
  McpTestResult,
  ModelCapabilities,
  ModelTestRequest,
  ModelTestResult,
  PacksResponse,
  ProviderModelOut,
  ProviderModelPage,
  ProvidersResponse,
  SessionDetailOut,
  SessionEventPage,
  SessionListenTokenOut,
  SessionPage,
  SessionWhisperIn,
  SessionWhisperOut,
  ToolCreate,
  ToolDryRunRequest,
  ToolDryRunResult,
  ToolKitInstantiate,
  ToolKitInstantiated,
  ToolKitsResponse,
  ToolkitOut,
  ToolkitPage,
  ToolOut,
  ToolPage,
  ToolTemplateInstantiate,
  ToolTemplateInstantiated,
  ToolTemplatesResponse,
  ValidationResult,
} from "@/contracts/lkap-contracts";
import { uploadDataset } from "@/components/console/lib/upload";

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
  /** V5-25/V5-28: `GET /v1/tool-templates` (the Cal.com booking set). */
  toolTemplates: ["tool-templates"] as const,
  /** Nested under `["tools", ...]` so `invalidateQueries({queryKey: ["tools"]})` (delete, test, revoke) reaches it too. */
  mcpOauthStatus: (toolId: string) => ["tools", toolId, "oauth-status"] as const,
  kbs: ["knowledge-bases"] as const,
  kb: (id: string) => ["knowledge-bases", id] as const,
  kbDocuments: (id: string) => ["knowledge-bases", id, "documents"] as const,
  /** V5-45/ask #232: `GET /v1/knowledge-bases/{id}/source` — an external (managed search) knowledge base's partition and document count. */
  kbSource: (id: string) => ["knowledge-bases", id, "source"] as const,
  kbEvals: (id: string) => ["knowledge-bases", id, "evals"] as const,
  kbEvalRun: (kbId: string, jobId: string) => ["knowledge-bases", kbId, "evaluate", jobId] as const,
  kbEvalLatest: (id: string) => ["knowledge-bases", id, "evaluate", "latest"] as const,
  /** V5-24: BYO Qdrant/Pinecone/Weaviate + hosted re-rankers, `/v1/knowledge-connections`. */
  knowledgeConnections: ["knowledge-connections"] as const,
  /** V5-33: an agent's test runs, nested under `["agents", id, ...]` so the agent's own invalidation reaches them too. */
  agentTestRuns: (agentId: string, limit: number) => ["agents", agentId, "tests", "runs", limit] as const,
  agentTestRun: (agentId: string, runId: string) => ["agents", agentId, "tests", "runs", runId] as const,
  sessions: (agentId?: string, status?: string) => ["sessions", agentId ?? "", status ?? ""] as const,
  session: (id: string) => ["sessions", id] as const,
  sessionEvents: (id: string) => ["sessions", id, "events"] as const,
  /** V5-38: the Live tab's own polled feed (separate from `sessionEvents`'s one-shot fetch above). */
  sessionLiveEvents: (id: string) => ["sessions", id, "events", "live"] as const,
  /** Every model-record query of one provider (`["provider-models", providerId, …]`), for invalidation. */
  providerModelsAll: (providerId: string) => ["provider-models", providerId] as const,
  providerModels: (providerId: string, params: { custom: boolean; limit?: number }) =>
    ["provider-models", providerId, "list", params.custom, params.limit ?? null] as const,
  providerModel: (providerId: string, modelId: string) => ["provider-models", providerId, "one", modelId] as const,
  /** Tools -> Apps (Composio, docs/v5/COMPOSIO.md §6). */
  appsStatus: ["apps", "status"] as const,
  appsCategories: ["apps", "categories"] as const,
  // Normalized to the same shape the request itself sends (falsy ->
  // `undefined`, so an omitted field and an explicit falsy one hash
  // identically): `AppsTab`'s speculative prefetch and `AppGallery`'s own
  // default-view call must land on the very same cache entry, or the
  // "warm the gallery's query in parallel with status" fix silently does
  // nothing (a third, wasted request instead of a shared one).
  appsToolkits: (params: ToolkitListParams) =>
    [
      "apps",
      "toolkits",
      {
        query: params.query || undefined,
        category: params.category || undefined,
        connectedOnly: params.connectedOnly || undefined,
        limit: params.limit,
      },
    ] as const,
  appsToolkit: (slug: string) => ["apps", "toolkit", slug] as const,
  appsActions: (slug: string, params: AppActionListParams) => ["apps", "actions", slug, params] as const,
  appsConnections: ["apps", "connections"] as const,
  appsConnection: (id: string) => ["apps", "connection", id] as const,
  /** V6-19: lookup tables (`GET /v1/datasets…`, D-V6-27). */
  datasets: ["datasets"] as const,
  dataset: (id: string) => ["datasets", id] as const,
  datasetRows: (id: string, offset: number, limit: number) => ["datasets", id, "rows", offset, limit] as const,
  /** V6-19: tool kits (`GET /v1/tool-kits…`, D-V6-26). */
  toolKits: ["tool-kits"] as const,
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

/** `POST /agents/{id}/unarchive` (V6-30): clears `archived_at`, so the agent is active again. */
export function useRestoreAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<AgentOut>(`agents/${id}/unarchive`),
    onSuccess: (agent) => {
      queryClient.setQueryData(keys.agent(agent.id), agent);
      void queryClient.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useValidateAgent(id: string) {
  return useMutation({
    mutationFn: () => api.post<ValidationResult>(`agents/${id}/validate`),
  });
}

// ---- agent tests (V5-33) ----

/** `GET .../tests/runs`: newest first, no verdicts (the run table). */
export function useAgentTestRuns(agentId: string, limit = 20) {
  return useQuery({
    queryKey: keys.agentTestRuns(agentId, limit),
    queryFn: () => api.get<AgentTestRunPage>(`agents/${agentId}/tests/runs`, { limit }),
    enabled: agentId.length > 0,
  });
}

/** Polls one run (verdicts included) until it leaves `queued`/`running`. `runId: null` disables the query. */
export function useAgentTestRun(agentId: string, runId: string | null) {
  return useQuery({
    queryKey: keys.agentTestRun(agentId, runId ?? "none"),
    queryFn: () => api.get<AgentTestRun>(`agents/${agentId}/tests/runs/${runId}`),
    enabled: agentId.length > 0 && runId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });
}

/** `POST .../tests/run`: 202 with the queued run (no verdicts yet); the section polls it with `useAgentTestRun`. */
export function useRunAgentTests(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body?: AgentTestRunIn) => api.post<AgentTestRun>(`agents/${agentId}/tests/run`, body),
    onSuccess: (run) => {
      queryClient.setQueryData(keys.agentTestRun(agentId, run.id), run);
      void queryClient.invalidateQueries({ queryKey: keys.agentTestRuns(agentId, 20) });
    },
  });
}

// ---- credentials ----

export function useCredentials(providerId?: string) {
  return useQuery({
    queryKey: keys.credentials(providerId),
    queryFn: () => api.get<CredentialPage>("credentials", providerId ? { provider_id: providerId } : undefined),
  });
}

/**
 * `useCredentials` across several credential homes at once — `providerIds`
 * is a fixed-length, constant-order list (e.g. `OPENAI_KEY_HOME_IDS`,
 * `registry/credential-picker.tsx`), so `useQueries` (not a loop of
 * `useCredentials` calls, which the rules of hooks forbid for a dynamic
 * count) is the one query per id, cached the same way `useCredentials`
 * caches its own.
 */
export function useCredentialsAcrossHomes(providerIds: readonly string[]) {
  return useQueries({
    queries: providerIds.map((providerId) => ({
      queryKey: keys.credentials(providerId),
      queryFn: () => api.get<CredentialPage>("credentials", { provider_id: providerId }),
    })),
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
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<CredentialTestResult>(`credentials/${id}/test`),
    // V6-32: the api records the result on the key; refetch so the list shows it.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    },
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

// ---- tool templates (V5-25/V5-28: `GET /v1/tool-templates`, "Add a tool" → "From a template") ----

export function useToolTemplates() {
  return useQuery({
    queryKey: keys.toolTemplates,
    queryFn: () => api.get<ToolTemplatesResponse>("tool-templates"),
  });
}

export function useInstantiateToolTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ templateId, body }: { templateId: string; body: ToolTemplateInstantiate }) =>
      api.post<ToolTemplateInstantiated>(`tool-templates/${templateId}/instantiate`, body),
    onSuccess: (_result, { body }) => {
      void queryClient.invalidateQueries({ queryKey: keys.tools(body.agent_id ?? undefined) });
      void queryClient.invalidateQueries({ queryKey: keys.tools(undefined) });
    },
  });
}

// ---- lookup tables (V6-19: `GET/POST /v1/datasets…`, D-V6-27) ----

/** `GET /v1/datasets` — newest first; polls while any import is still `pending`. */
export function useDatasets(options?: { pollWhilePending?: boolean; enabled?: boolean }) {
  return useQuery({
    queryKey: keys.datasets,
    queryFn: () => api.get<DatasetPage>("datasets"),
    enabled: options?.enabled ?? true,
    refetchInterval: (query) => {
      if (!options?.pollWhilePending) return false;
      const hasPending = query.state.data?.items.some((item: DatasetOut) => item.status === "pending") ?? false;
      return hasPending ? 2000 : false;
    },
  });
}

/** `GET /v1/datasets/{id}` — polls while the import is `pending` (progress, then status/error). */
export function useDataset(id: string, options?: { poll?: boolean }) {
  return useQuery({
    queryKey: keys.dataset(id),
    queryFn: () => api.get<DatasetOut>(`datasets/${id}`),
    enabled: id.length > 0,
    refetchInterval: (query) => {
      if (!options?.poll) return false;
      return query.state.data?.status === "pending" ? 2000 : false;
    },
  });
}

/** `GET /v1/datasets/{id}/rows` — a page of imported rows in file order (empty until `ready`). */
export function useDatasetRows(id: string, offset: number, limit: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: keys.datasetRows(id, offset, limit),
    queryFn: () => api.get<DatasetPreviewOut>(`datasets/${id}/rows`, { offset, limit }),
    enabled: id.length > 0 && (options?.enabled ?? true),
  });
}

/** `POST /v1/datasets` — multipart (`upload.ts`'s `uploadDataset`, `apiRequest` always JSON-encodes). */
export function useUploadDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, keyColumns, file }: { name: string; keyColumns: Record<string, string>; file: File }) =>
      uploadDataset(name, keyColumns, file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.datasets });
    },
  });
}

/** `DELETE /v1/datasets/{id}` — 409 while a tool still uses the table (the error names them). */
export function useDeleteDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`datasets/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.datasets });
    },
  });
}

/** `POST /v1/datasets/{id}/lookup` — the console's test lookup (a builder write, like the knowledge test search). */
export function useDatasetLookup(id: string) {
  return useMutation({
    mutationFn: (body: DatasetLookupIn) => api.post<DatasetLookupOut>(`datasets/${id}/lookup`, body),
  });
}

// ---- tool kits (V6-19: `GET/POST /v1/tool-kits…`, D-V6-26) ----

/** `GET /v1/tool-kits` — the kits gallery, in catalogue order. */
export function useToolKits() {
  return useQuery({
    queryKey: keys.toolKits,
    queryFn: () => api.get<ToolKitsResponse>("tool-kits"),
  });
}

/**
 * `POST /v1/tool-kits/{id}/instantiate` — with `dry_run: true` this only
 * previews (`ToolKitInstantiated.changes`); without it, the kit is added to
 * the agent in one new configuration version, so the agent (its config,
 * panel, flow) and the shared tools list both need refreshing. The open
 * agent editor already resets its form whenever `agent.config_version`
 * changes (`agent-editor.tsx`), so invalidating `keys.agent` here is enough
 * to bring a kit's new tools, blocks and instructions into view.
 */
export function useInstantiateToolKit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kitId, body }: { kitId: string; body: ToolKitInstantiate }) =>
      api.post<ToolKitInstantiated>(`tool-kits/${kitId}/instantiate`, body),
    onSuccess: (_result, { body }) => {
      if (body.dry_run) return;
      void queryClient.invalidateQueries({ queryKey: keys.agent(body.agent_id) });
      void queryClient.invalidateQueries({ queryKey: keys.tools(body.agent_id) });
      void queryClient.invalidateQueries({ queryKey: keys.tools(undefined) });
      void queryClient.invalidateQueries({ queryKey: keys.datasets });
    },
  });
}

// ---- MCP test + sign-in (V5-21) ----

/** `POST /v1/tools/{id}/test` — connects once, stores `cached_tools` on the definition. */
export function useTestMcpTool() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<McpTestResult>(`tools/${id}/test`),
    onSuccess: () => {
      // The api stores `cached_tools`/`cached_at` on the row it just tested.
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

/** `GET /v1/tools/{id}/oauth/status` — `null` id means "not saved yet" (no request). */
export function useMcpOauthStatus(toolId: string | null, options?: { poll?: boolean }) {
  return useQuery({
    queryKey: keys.mcpOauthStatus(toolId ?? "unsaved"),
    queryFn: () => api.get<McpOauthStatusOut>(`tools/${toolId}/oauth/status`),
    enabled: toolId !== null,
    refetchInterval: options?.poll ? 2500 : false,
  });
}

/** `POST /v1/tools/{id}/oauth/start` — opens the vendor's page (R-V5-14: same browser, never a copyable link). */
export function useStartMcpOauth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: McpOauthStartIn }) =>
      api.post<McpOauthStartOut>(`tools/${id}/oauth/start`, body),
    onSuccess: (_result, { id }) => {
      void queryClient.invalidateQueries({ queryKey: keys.mcpOauthStatus(id) });
    },
  });
}

/** `POST /v1/tools/{id}/oauth/revoke` — best-effort at the provider, then deletes the sign-in. */
export function useRevokeMcpOauth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<McpOauthStatusOut>(`tools/${id}/oauth/revoke`),
    onSuccess: () => {
      // Prefix match: also covers `keys.mcpOauthStatus(id)` (nested under `["tools", ...]`).
      // The revoke clears `auth.credential_id` on the tool row too, so the list needs
      // refreshing, not just the status sub-key — otherwise a Save right after Disconnect
      // could post a credential id for a bag that no longer exists.
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

// ---- knowledge connections (V5-24: BYO Qdrant/Pinecone/Weaviate, hosted re-rankers, Ragie managed search) ----

/**
 * `options.enabled` (ask #234): `create-kb-dialog.tsx` only needs the list
 * once a connection-backed storage choice is picked, so it passes
 * `{ enabled: open && storage !== "platform" }` — creating a plain
 * platform-default knowledge base never calls `GET /v1/knowledge-connections`.
 * Every other caller omits it and gets react-query's default (`true`).
 */
export function useKnowledgeConnections(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: keys.knowledgeConnections,
    queryFn: () => api.get<KnowledgeConnectionPage>("knowledge-connections"),
    enabled: options?.enabled,
  });
}

export function useCreateKnowledgeConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: KnowledgeConnectionCreate) =>
      api.post<KnowledgeConnectionOut>("knowledge-connections", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
  });
}

/** `PUT /v1/knowledge-connections/{id}` — `settings` replaces the whole bag; send every field. */
export function useUpdateKnowledgeConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: KnowledgeConnectionUpdate }) =>
      api.put<KnowledgeConnectionOut>(`knowledge-connections/${id}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
  });
}

export function useDeleteKnowledgeConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`knowledge-connections/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
  });
}

/** `POST /v1/knowledge-connections/{id}/test` — the api records the outcome on the row too. */
export function useTestKnowledgeConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<KnowledgeConnectionTestOut>(`knowledge-connections/${id}/test`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
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
      // `connection_id` on the new row changes the connection's `knowledge_base_count`.
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
  });
}

export function useDeleteKb() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`knowledge-bases/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.kbs });
      void queryClient.invalidateQueries({ queryKey: keys.knowledgeConnections });
    },
  });
}

/** `GET /v1/knowledge-bases/{id}/source` (V5-45/ask #232): only meaningful for a `kind: "external"` knowledge base. */
export function useKbSource(kbId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: keys.kbSource(kbId),
    queryFn: () => api.get<KbSourceOut>(`knowledge-bases/${kbId}/source`),
    enabled: kbId.length > 0 && (options?.enabled ?? true),
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

// ---- knowledge base evals (V5-05 harness, V5-10 console) ----

/** The knowledge base's golden-question set, in the order it was put. */
export function useKbEvals(kbId: string) {
  return useQuery({
    queryKey: keys.kbEvals(kbId),
    queryFn: () => api.get<KbEvalSetOut>(`knowledge-bases/${kbId}/evals`),
    enabled: kbId.length > 0,
  });
}

/** `PUT .../evals` replaces the whole set (≤ 500 questions); the dialog sends every row back each save. */
export function usePutKbEvals(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: KbEvalSetIn) => api.put<KbEvalSetOut>(`knowledge-bases/${kbId}/evals`, body),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.kbEvals(kbId), data);
    },
  });
}

/** `POST .../evaluate`: enqueues a run and returns its (still `pending`) job id; the card polls `useKbEvalRun` with it. */
export function useEvaluateKb(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body?: KbEvaluateIn) => api.post<KbEvalRunOut>(`knowledge-bases/${kbId}/evaluate`, body),
    onSuccess: (data) => {
      queryClient.setQueryData(keys.kbEvalRun(kbId, data.job_id), data);
    },
  });
}

/** Polls one evaluation run until it leaves `pending`/`running`. `jobId: null` disables the query (nothing run yet this session). */
export function useKbEvalRun(kbId: string, jobId: string | null) {
  return useQuery({
    queryKey: keys.kbEvalRun(kbId, jobId ?? "none"),
    queryFn: () => api.get<KbEvalRunOut>(`knowledge-bases/${kbId}/evaluate/${jobId}`),
    enabled: kbId.length > 0 && jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "running" ? 1500 : false;
    },
  });
}

/** The last finished run, if any — `null` (not an error banner) when the knowledge base has never been evaluated (404). */
export function useLatestKbEvalRun(kbId: string) {
  return useQuery({
    queryKey: keys.kbEvalLatest(kbId),
    queryFn: async () => {
      try {
        return await api.get<KbEvalRunOut>(`knowledge-bases/${kbId}/evaluate/latest`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: kbId.length > 0,
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

// ---- V5-38: the Live tab (listen-in and whisper, ask #251) ----

/**
 * `POST /v1/sessions/{id}/listen-token` — a fresh, admin-authenticated call
 * each time it's invoked. Not a `useQuery`/`useMutation`: it's called from
 * inside a `TokenSource.custom` fetcher (`live/listen-session.ts`), which the
 * LiveKit client itself decides when to invoke (once on mount, again after an
 * unexpected drop) — 15-minute tokens, ask again before `expiresAt` (#251).
 * 409 `not_live` when the session isn't `active`.
 */
export function fetchSessionListenToken(sessionId: string): Promise<SessionListenTokenOut> {
  return api.post<SessionListenTokenOut>(`sessions/${sessionId}/listen-token`);
}

/**
 * `POST /v1/sessions/{id}/whisper` (202): written guidance for the agent,
 * never heard or seen by the caller. 409 `no_agent` when no agent is in the
 * room. The Live tab clears its box on success and surfaces the api's
 * message on failure (`ApiError.message`).
 */
export function useSessionWhisper(sessionId: string) {
  return useMutation({
    mutationFn: (body: SessionWhisperIn) => api.post<SessionWhisperOut>(`sessions/${sessionId}/whisper`, body),
  });
}

/** How often the Live tab polls for new supervisor events while it's open. */
const LIVE_EVENTS_POLL_MS = 4_000;
/** Enough rows to show the last few whispers/escalations without paging. */
const LIVE_EVENTS_LIMIT = 100;

/**
 * The Live tab's own event feed (distinct from `useSessionEvents`'s one-shot
 * read above): polls while `enabled`, so a whisper the worker just applied or
 * an escalation the agent just raised shows up within a few seconds without
 * a room-level RPC (a hidden listener can't call one — ask #251).
 */
export function useLiveSessionEvents(sessionId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.sessionLiveEvents(sessionId),
    queryFn: () => api.get<SessionEventPage>(`sessions/${sessionId}/events`, { limit: LIVE_EVENTS_LIMIT }),
    enabled: enabled && sessionId.length > 0,
    refetchInterval: enabled ? LIVE_EVENTS_POLL_MS : false,
  });
}

// ---- tool providers: Apps (Composio, docs/v5/COMPOSIO.md §6, V5-22) ----

/** Every route lives under `/v1/tool-providers/composio/*` (docs/v5/COMPOSIO.md §4). */
const APPS_BASE = "tool-providers/composio";

/** `GET /v1/tool-providers/composio/status` — the Apps section header. */
export function useAppsStatus() {
  return useQuery({
    queryKey: keys.appsStatus,
    queryFn: () => api.get<AppsStatusOut>(`${APPS_BASE}/status`),
  });
}

/** `POST .../key/test` — a pasted key, used once and never stored (D-V5-C13). */
export function useTestAppsKey() {
  return useMutation({
    mutationFn: (apiKey: string) => api.post<AppKeyTestOut>(`${APPS_BASE}/key/test`, { api_key: apiKey }),
  });
}

/** `POST .../enable` — turns Apps on for the workspace (a key must already exist). */
export function useEnableApps() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AppsStatusOut>(`${APPS_BASE}/enable`),
    onSuccess: (status) => {
      queryClient.setQueryData(keys.appsStatus, status);
      // Every `["apps", …]` query (toolkits, connections, both details), not
      // just `keys.appsStatus`: `AppCard`/`ConnectionRow` read those to
      // decide what to show, and "enabled" changes what they should return.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    },
  });
}

/** `POST .../disable` — keeps the key and connections, pauses the tools that use them. */
export function useDisableApps() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AppsStatusOut>(`${APPS_BASE}/disable`),
    onSuccess: (status) => {
      queryClient.setQueryData(keys.appsStatus, status);
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["credentials"] });
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

export interface ToolkitListParams {
  query?: string;
  category?: string;
  limit?: number;
  connectedOnly?: boolean;
}

/**
 * `GET .../toolkits` — the app gallery, paged by `cursor` (docs/v5/COMPOSIO.md
 * §4: "page with `cursor`"). `useInfiniteQuery` so "Load more" appends to the
 * same list rather than the caller juggling a manual accumulator; a search,
 * category or "Connected only" change is a new `queryKey` and starts over.
 * `placeholderData: keepPreviousData` keeps the last page's apps on screen
 * while the new query settles, instead of a loading flash between keystrokes.
 */
export function useToolProviderToolkits(params: ToolkitListParams, options?: { enabled?: boolean; retry?: boolean }) {
  return useInfiniteQuery({
    queryKey: keys.appsToolkits(params),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api.get<ToolkitPage>(`${APPS_BASE}/toolkits`, {
        query: params.query || undefined,
        category: params.category || undefined,
        cursor: pageParam || undefined,
        limit: params.limit,
        connected_only: params.connectedOnly || undefined,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: options?.enabled ?? true,
    // `AppsTab`'s speculative prefetch (fired before `status` confirms Apps
    // are even enabled) passes `retry: false`: a 409 `apps_not_enabled`
    // never succeeds on retry, so the default retry would just double a
    // request that was already a guess.
    retry: options?.retry,
    placeholderData: keepPreviousData,
  });
}

/** One toolkit category, as `GET /v1/tool-providers/composio/categories` returns it — kept
 * local to the web hook rather than added to `@/contracts/lkap-contracts` (the api response
 * model is api-local too; see `lkap_api.tool_providers.service.ToolProviderCategoryOut`). */
export interface ToolProviderCategoryOut {
  id: string;
  name: string;
}

export interface ToolProviderCategoryPage {
  items: ToolProviderCategoryOut[];
}

/**
 * `GET .../categories` — every category Composio's apps are grouped under
 * (docs/v5/COMPOSIO.md §6), for the gallery's category filter. Unlike
 * `useToolProviderToolkits`'s categories (only the ones seen among the apps
 * loaded so far), this is the complete list.
 */
export function useToolProviderCategories(options?: { enabled?: boolean; retry?: boolean }) {
  return useQuery({
    queryKey: keys.appsCategories,
    queryFn: () => api.get<ToolProviderCategoryPage>(`${APPS_BASE}/categories`),
    enabled: options?.enabled ?? true,
    retry: options?.retry,
  });
}

/** `GET .../toolkits/{slug}` — one app's connect methods and fields. */
export function useToolProviderToolkit(slug: string | null) {
  return useQuery({
    queryKey: keys.appsToolkit(slug ?? ""),
    queryFn: () => api.get<ToolkitOut>(`${APPS_BASE}/toolkits/${encodeURIComponent(slug ?? "")}`),
    enabled: Boolean(slug),
  });
}

export interface AppActionListParams {
  query?: string;
  important?: boolean;
  limit?: number;
}

/** `GET .../toolkits/{slug}/actions` — a paged, appending list of one app's actions (the Actions dialog). */
export function useToolProviderActions(slug: string | null, params: AppActionListParams) {
  return useInfiniteQuery({
    queryKey: keys.appsActions(slug ?? "", params),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api.get<AppActionPage>(`${APPS_BASE}/toolkits/${encodeURIComponent(slug ?? "")}/actions`, {
        query: params.query || undefined,
        important: params.important || undefined,
        cursor: pageParam || undefined,
        limit: params.limit,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: Boolean(slug),
  });
}

/** `GET .../connections` — every connected app of the workspace. */
export function useToolProviderConnections(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: keys.appsConnections,
    queryFn: () => api.get<AppConnectionPage>(`${APPS_BASE}/connections`),
    enabled: options?.enabled ?? true,
  });
}

/** `GET .../connections/{id}` — refresh one connection; polls while `status === "initiated"`. */
export function useToolProviderConnection(id: string | null, options?: { poll?: boolean }) {
  return useQuery({
    queryKey: keys.appsConnection(id ?? ""),
    queryFn: () => api.get<AppConnectionOut>(`${APPS_BASE}/connections/${id}`),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      if (!options?.poll) return false;
      const data = query.state.data as AppConnectionOut | undefined;
      return data?.status === "initiated" ? 3000 : false;
    },
  });
}

/**
 * `GET .../connections/{id}?identify=true` — "Check now" (V6-35): asks the app
 * who the account is signed in as, right away, and refreshes every view of it.
 * Viewers may run it (it is the same read as the status check).
 */
export function useIdentifyConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.get<AppConnectionOut>(`${APPS_BASE}/connections/${id}`, { identify: "true" }),
    onSuccess: (connection) => {
      queryClient.setQueryData(keys.appsConnection(connection.id), connection);
      void queryClient.invalidateQueries({ queryKey: keys.appsConnections });
    },
  });
}

/** `POST .../connections` — start (or, for a key, finish) a connection. */
export function useConnectApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AppConnectIn) => api.post<AppConnectOut>(`${APPS_BASE}/connections`, body),
    onSuccess: () => {
      // `keys.appsToolkits(...)` is what `AppCard` actually reads to decide
      // Connect-button vs `ConnectionRow` (`ToolkitOut.connected`) — a
      // narrower invalidation (just `appsConnections`/`appsStatus`) leaves
      // the gallery showing "Connect" on an app that just connected.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
    },
  });
}

/**
 * `PATCH .../connections/{id}` — rename an account (also at Composio) and/or
 * make it its app's default (R-V5-13, `rename-account-dialog.tsx` and the
 * app card's "Make default"). A 409 is always the duplicate-label refusal
 * for a rename, or "not active yet" for a default it can't do — the callers
 * map the status themselves rather than trusting the generic `conflict` code.
 */
export function useUpdateConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ConnectionRenameIn }) =>
      api.patch<AppConnectionOut>(`${APPS_BASE}/connections/${id}`, body),
    onSuccess: () => {
      // Every account of the app: `AppCard`'s accounts dialog and the
      // Connected apps card's chooser both read `appsConnections`, and a
      // rename/default change must show up in both places right away.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
    },
  });
}

/** `POST .../connections/{id}/reconnect` — a new sign-in link, or a new key. */
export function useReconnectApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, fields }: { id: string; fields?: AppReconnectIn["fields"] }) =>
      api.post<AppConnectOut>(`${APPS_BASE}/connections/${id}/reconnect`, { fields: fields ?? {} }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
    },
  });
}

/** `DELETE .../connections/{id}` — disconnect; `purge` also removes the row. */
export function useDisconnectApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, purge }: { id: string; purge?: boolean }) =>
      api.delete<AppConnectionOut | void>(`${APPS_BASE}/connections/${id}${purge ? "?purge=true" : ""}`),
    onSuccess: () => {
      // `keys.appsConnections` alone does not match `keys.appsConnection(id)`
      // (a different second segment) — the disconnected row's own detail
      // query, which `ConnectionRow` renders from, would otherwise keep
      // answering "active" from cache.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
}

/** `POST .../materialise` — pick actions of a connected app (adds to, never replaces, the picks). */
export function useMaterialiseAppActions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AppActionsPickIn) => api.post<AppActionsPickOut>(`${APPS_BASE}/materialise`, body),
    onSuccess: () => {
      // Not just the connections list: `ConnectionRow` reads `picked_actions`
      // from `appsConnection(id)` when it reopens the Actions dialog.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
    },
  });
}

/**
 * `POST /v1/tool-providers/composio/tools/{id}/refresh-schema` (R-V5-8,
 * R-V5-6, D-V5-C8): what changed upstream for one `provider` tool's pinned
 * action inputs, and — with `apply` — writes them.
 *
 * `SchemaRefreshOut` is api-local (`api/src/lkap_api/tool_providers/materialise.py`),
 * not a `lkap_contracts` model, so it never reaches the generated
 * `lkap-contracts.d.ts` (`lkap_contracts.export` only emits pydantic models
 * that package owns) — mirrored by hand here, same as `ToolkitListParams`
 * above. Filed as docs/v5/_asks.md for the contracts owner to promote it,
 * the same pattern as asks #12/#27.
 */
export interface ProviderToolSchemaRefreshOut {
  tool_id: string;
  tool_slug: string;
  changed: boolean;
  applied: boolean;
  schema_version_before: string | null;
  schema_version_after: string | null;
  added: string[];
  removed: string[];
  modified: string[];
  required_before: string[];
  required_after: string[];
}

export function useRefreshProviderToolSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ toolId, apply = false }: { toolId: string; apply?: boolean }) =>
      api.post<ProviderToolSchemaRefreshOut>(`${APPS_BASE}/tools/${toolId}/refresh-schema${apply ? "?apply=true" : ""}`),
    onSuccess: async (result) => {
      // Awaited (not `void`): the dialog's `mutateAsync` reads the tool's
      // *current* `parameters` from its `tool` prop right after Apply
      // resolves, so a refetch that lands before that read matters — a
      // Save posted before the parent re-renders with fresh `parameters`
      // would otherwise overwrite the just-applied schema right back.
      if (result.applied) {
        await queryClient.invalidateQueries({ queryKey: ["tools"] });
      }
    },
  });
}
