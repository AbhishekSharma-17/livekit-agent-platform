/**
 * Fixtures for V4-09 (custom model ids, Test model, the Catalog dialog):
 * a routing `fetch` stub, `Me` bodies per role, a 300-item model catalog,
 * `provider_models` rows and `ModelTestResult` bodies.
 */
import { vi } from "vitest";

import type {
  CatalogItem,
  CatalogResponse,
  CredentialOut,
  Me,
  ModelTestResult,
  ProviderModelOut,
  ProviderSpec,
} from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

export const REGISTRY = (providersJson as unknown as { providers: ProviderSpec[] }).providers;

export function spec(id: string): ProviderSpec {
  const found = REGISTRY.find((p) => p.id === id);
  if (!found) throw new Error(`no registry entry ${id}`);
  return found;
}

export type Role = "viewer" | "builder" | "admin" | "owner";

export function meFor(role: Role): Me {
  return {
    user: { id: "u1", email: "person@example.com", name: "Person", is_platform_admin: false } as Me["user"],
    workspaces: [{ id: "ws1", slug: "default", name: "Default", role }],
  };
}

/** A 32-hex value: a Simli/Tavus-style key, or a vendor's real id (R-V4-31). */
export const HEX32 = "0123456789abcdef0123456789abcdef";
/** A pasted OpenRouter key. Must never appear in any request or rendered text. */
export const OPENROUTER_KEY = "sk-or-v1-Zq9WkX7vRt3LmN8pYb2HcJ5dQ1";

export const CREDENTIAL: CredentialOut = {
  id: "cred-or",
  provider_id: "openrouter-llm",
  label: "Team key",
  fingerprint: "…a1b2",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
} as CredentialOut;

/** 300 items: 12 Gemini-family ids (4 of them take images), the rest filler. */
export function bigCatalog(): CatalogItem[] {
  const items: CatalogItem[] = [];
  for (let i = 0; i < 12; i += 1) {
    items.push({
      id: `google/gemini-x-${i}`,
      label: `Gemini X ${i}`,
      meta: i < 4 ? { architecture: { input_modalities: ["text", "image"] } } : { input_modalities: ["text"] },
    });
  }
  for (let i = 0; items.length < 300; i += 1) {
    items.push({ id: `acme/model-${i}`, label: `Acme model ${i}`, meta: {} });
  }
  // The card's flat-key case: `meta.input_modalities` contains `image`.
  items.push({ id: "acme/seer-1", label: "Acme Seer", meta: { input_modalities: ["image", "text"] } });
  return items;
}

export function catalogResponse(items: CatalogItem[], kind: CatalogResponse["kind"] = "models"): CatalogResponse {
  return { kind, items, total: items.length, source: "vendor" };
}

export function record(overrides: Partial<ProviderModelOut> = {}): ProviderModelOut {
  return {
    id: "rec-1",
    provider_id: "openrouter-llm",
    provider_home: "openrouter-llm",
    kind: "llm",
    model_id: "acme/private-ft-7",
    created_at: "2026-09-20T00:00:00Z",
    updated_at: new Date().toISOString(),
    last_test_at: new Date(Date.now() - 2 * 60_000).toISOString(),
    last_test_ok: true,
    last_test_message: null,
    last_test_latency_ms: 412,
    last_test_fingerprint: CREDENTIAL.fingerprint,
    declared: null,
    detected: { tools: true },
    ...overrides,
  };
}

export function testResult(overrides: Partial<ModelTestResult> = {}): ModelTestResult {
  return {
    ok: true,
    provider_id: "openrouter-llm",
    model: "acme/private-ft-7",
    kind: "llm",
    checked_at: new Date().toISOString(),
    cached: false,
    latency_ms: 812,
    message: null,
    probes: [
      { name: "basic", ok: true, latency_ms: 420, message: null },
      { name: "tools", ok: true, latency_ms: 392, message: null },
    ],
    detected: { tools: true, vision: null },
    cost_estimate_usd: null,
    cost_note: "no price on file for this model; the probe used 9 tokens",
    sample: "ok **not markdown** <b>not html</b>",
    record_id: "rec-1",
    ...overrides,
  };
}

export interface Recorded {
  method: string;
  url: string;
  body: unknown;
}

export type Handler = (req: Recorded) => { status?: number; body?: unknown } | undefined;

/**
 * A `fetch` stub that answers by URL. `handlers` are tried in order; the
 * first to return a response wins. Unmatched calls get sensible empty bodies
 * (`auth/me` answers with `role`). Every call is recorded in `calls`.
 */
export function routeFetch({ role = "admin", handlers = [] as Handler[] }: { role?: Role; handlers?: Handler[] } = {}) {
  const calls: Recorded[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    const req = { method, url, body };
    calls.push(req);
    let res: { status?: number; body?: unknown } | undefined;
    for (const handler of handlers) {
      res = handler(req);
      if (res) break;
    }
    if (!res) {
      if (url.includes("/auth/me")) res = { body: meFor(role) };
      else if (url.includes("/credentials")) res = { body: { items: [CREDENTIAL], total: 1 } };
      else if (/\/providers\/[^/]+\/models\//.test(url)) res = { status: 404, body: { error: { code: "not_found", message: "no record" } } };
      else if (/\/providers\/[^/]+\/models/.test(url)) res = { body: { items: [], total: 0 } };
      else if (url.includes("/catalog")) res = { body: catalogResponse([]) };
      else if (url.endsWith("/providers") || url.includes("/providers?")) res = { body: { providers: REGISTRY } };
      else res = { body: { items: [], total: 0 } };
    }
    const status = res.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: String(status),
      json: async () => res?.body,
    } as Response;
  });
  return { fetch: fn, calls };
}

/** True when any recorded request carries `needle` in its URL (decoded) or body. */
export function anyCallCarries(calls: Recorded[], needle: string): boolean {
  return calls.some((call) => {
    const decoded = (() => {
      try {
        return decodeURIComponent(call.url);
      } catch {
        return call.url;
      }
    })();
    return call.url.includes(needle) || decoded.includes(needle) || JSON.stringify(call.body ?? "").includes(needle);
  });
}
