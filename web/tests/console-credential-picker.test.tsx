import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { CredentialDialog } from "@/components/console/registry/credential-dialog";
import { ProviderRow } from "@/components/console/providers/provider-row";
import type { CredentialOut, CredentialPage, Me, ProviderOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * V4-04 (docs/v4/PLAN-V4.md, `OPENROUTER.md` D-V4-10): one OpenRouter key
 * serves every OpenRouter slot. V4-03 made the api answer `GET
 * /v1/credentials?provider_id=<alias>` with the rows stored under the
 * credential home and store a `POST` under the home regardless of which
 * alias it was posted with (ask #13); this file proves the console side —
 * the picker lists the shared key for every alias, the dialog names the
 * arrangement, and a key added from one alias slot shows up selected
 * without a page reload.
 *
 * No `providers.ts` fixture exists yet (`ls web/tests/fixtures/`), so the
 * five OpenRouter entries plus one ordinary multi-credential vendor
 * (Deepgram STT, which must *not* see the OpenRouter key) are declared
 * locally, mirroring `contracts/src/lkap_contracts/providers.py`.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix/floating-ui ask jsdom for `:popover-open`/`:modal` (very slow
  // there); see console-editor-shell.test.tsx.
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const OPENROUTER_KEY_FIELD = { name: "api_key", label: "OpenRouter API key", type: "secret", required: true } as const;

const openrouterLlm: ProviderSpec = {
  v: 2,
  id: "openrouter-llm",
  kind: "llm",
  label: "OpenRouter",
  vendor: "OpenRouter",
  package: "livekit-plugins-openai",
  python_class: "livekit.plugins.openai.LLM.with_openrouter",
  secret_fields: [OPENROUTER_KEY_FIELD],
  fields: [],
  models: [],
  default_model: "openai/gpt-4.1-mini",
  get_key_url: "https://openrouter.ai/settings/keys",
};

const openrouterStt: ProviderSpec = {
  v: 2,
  id: "openrouter-stt",
  kind: "stt",
  label: "OpenRouter (STT)",
  vendor: "OpenRouter",
  package: "livekit-plugins-openai",
  python_class: "livekit.plugins.openai.STT",
  credential_provider: "openrouter-llm",
  secret_fields: [OPENROUTER_KEY_FIELD],
  fields: [],
  models: [],
  default_model: "openai/gpt-4o-mini-transcribe",
  notes:
    "Batch transcription over HTTP: no interim results; each turn is transcribed after end-of-speech, so expect " +
    "roughly half a second to two seconds more per turn than a streaming STT. For low latency prefer LiveKit " +
    "Inference STT or Deepgram.",
  get_key_url: "https://openrouter.ai/settings/keys",
};

const openrouterTts: ProviderSpec = {
  v: 2,
  id: "openrouter-tts",
  kind: "tts",
  label: "OpenRouter (TTS)",
  vendor: "OpenRouter",
  package: "livekit-plugins-openai",
  python_class: "livekit.plugins.openai.TTS",
  credential_provider: "openrouter-llm",
  secret_fields: [OPENROUTER_KEY_FIELD],
  fields: [],
  models: [],
  default_model: "google/gemini-3.8-flash-tts",
  get_key_url: "https://openrouter.ai/settings/keys",
};

const openrouterImageGen: ProviderSpec = {
  v: 2,
  id: "openrouter-image-gen",
  kind: "image_gen",
  label: "OpenRouter image generation",
  vendor: "OpenRouter",
  package: "openai",
  python_class: "lkap_agent.providers.image_gen.OpenRouterImageGen",
  credential_provider: "openrouter-llm",
  secret_fields: [OPENROUTER_KEY_FIELD],
  fields: [],
  models: [],
  default_model: "openai/gpt-image-1",
  get_key_url: "https://openrouter.ai/settings/keys",
};

const openrouterEmbedding: ProviderSpec = {
  v: 2,
  id: "openrouter-embedding",
  kind: "embedding",
  label: "OpenRouter embeddings",
  vendor: "OpenRouter",
  package: "openai",
  python_class: "lkap_agent.providers.embedding.OpenRouterEmbedding",
  credential_provider: "openrouter-llm",
  secret_fields: [OPENROUTER_KEY_FIELD],
  fields: [],
  models: [],
  default_model: "openai/text-embedding-3-small",
  get_key_url: "https://openrouter.ai/settings/keys",
};

const deepgramStt: ProviderSpec = {
  v: 2,
  id: "deepgram-stt",
  kind: "stt",
  label: "Deepgram",
  vendor: "Deepgram",
  package: "livekit-plugins-deepgram",
  python_class: "livekit.plugins.deepgram.STT",
  secret_fields: [{ name: "api_key", label: "Deepgram API key", type: "secret", required: true }],
  fields: [],
  models: [],
  default_model: null,
};

const REGISTRY = [openrouterLlm, openrouterStt, openrouterTts, openrouterImageGen, openrouterEmbedding, deepgramStt];

const HOME_CREDENTIAL: CredentialOut = {
  id: "cred_or",
  provider_id: "openrouter-llm",
  label: "OpenRouter key",
  fingerprint: "…or01",
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/**
 * `GET /v1/credentials?provider_id=X` answers with the rows stored under
 * `credentialHome(X)` — that's the api behaviour V4-03 shipped (ask #13);
 * this stub reproduces it so the console-side test proves the console reads
 * the response correctly rather than re-testing the api.
 */
function stubApi(options: { credentialsByHome?: Record<string, CredentialOut[]> } = {}) {
  const calls: Call[] = [];
  const home: Record<string, string> = Object.fromEntries(REGISTRY.map((p) => [p.id, p.credential_provider ?? p.id]));
  // Mutable so a POST in the test can add to what a later GET answers —
  // that's what "the picker refetches on save" (the fix below) actually proves.
  const byHome: Record<string, CredentialOut[]> = options.credentialsByHome ?? { "openrouter-llm": [HOME_CREDENTIAL] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      const call: Call = { url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);
      const respond = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body }) as Response;

      if (url.includes("/auth/me")) {
        const me: Me = { user: { id: "u1", email: "owner@example.com" }, workspaces: [{ id: "w1", name: "Test", slug: "test", role: "admin" }] };
        return respond(me);
      }
      if (url.includes("/providers")) {
        // The dialog fetches the full registry whenever it's opened from an
        // aliased slot (R-V4-7's follow-up) to enumerate every sibling kind
        // a shared credential home covers — see `credentialDisplay`.
        return respond({ providers: REGISTRY });
      }
      if (url.includes("/credentials") && method === "GET") {
        const match = /provider_id=([^&]+)/.exec(url);
        const providerId = match ? decodeURIComponent(match[1]) : undefined;
        const items = providerId ? (byHome[home[providerId] ?? providerId] ?? []) : Object.values(byHome).flat();
        const page: CredentialPage = { items, total: items.length };
        return respond(page);
      }
      if (url.includes("/credentials") && method === "POST") {
        const body = call.body as { provider_id: string; label: string };
        // The api stores a create posted under an alias id (ask #13(b)) under
        // the home; the console never rewrites the request body itself.
        const storedUnder = home[body.provider_id] ?? body.provider_id;
        const created: CredentialOut = {
          id: "cred_new",
          provider_id: storedUnder,
          label: body.label,
          fingerprint: "…new1",
          created_at: "2026-09-25T00:00:00Z",
          updated_at: "2026-09-25T00:00:00Z",
        };
        byHome[storedUnder] = [...(byHome[storedUnder] ?? []), created];
        return respond(created, 201);
      }
      if (url.includes("/credentials/") && (method === "PUT" || url.endsWith("/test"))) {
        return respond({ ok: true, message: "ok" });
      }
      throw new Error(`Unhandled fetch: ${method} ${url}`);
    }),
  );
  return calls;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** A stateful host so `onChange` round-trips into the picker's `value`, the way the real slot editor does. */
function ControlledPicker({ spec }: { spec: ProviderSpec }) {
  const [value, setValue] = React.useState<string | null>(null);
  return <CredentialPicker spec={spec} value={value} onChange={setValue} />;
}

describe("CredentialPicker — the shared OpenRouter key (R-V4-7, V4-04)", () => {
  it.each([
    ["openrouter-stt", openrouterStt],
    ["openrouter-tts", openrouterTts],
    ["openrouter-image-gen", openrouterImageGen],
  ] as const)("the %s picker's list includes the key stored under the openrouter-llm home", async (_id, spec) => {
    stubApi();
    // Radix's `SelectContent` only mounts its options once opened (no
    // `forceMount`), which jsdom can't drive reliably here (no existing test
    // in this codebase opens a raw `Select`). Passing the home credential's
    // id as the already-selected `value` proves the same fact without that:
    // `selected = items.find((item) => item.id === value)` only renders
    // `SelectedKeyTest` (the "Test key" action) when the fetched `items`
    // actually contains that id — i.e. the alias's query really did resolve
    // to the home's row.
    renderWithClient(<CredentialPicker spec={spec} value={HOME_CREDENTIAL.id} onChange={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Test key" })).toBeTruthy();
    expect(screen.queryByText(new RegExp(`No ${spec.vendor} .* keys yet`))).toBeNull();
  });

  it("the deepgram-stt picker does not see the OpenRouter key", async () => {
    stubApi();
    renderWithClient(<CredentialPicker spec={deepgramStt} value={null} onChange={vi.fn()} />);
    expect(await screen.findByText(/No Deepgram keys yet/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Test key" })).toBeNull();
  });

  it("a key added from an alias slot (openrouter-tts) appears selected without a reload", async () => {
    stubApi({ credentialsByHome: {} });
    renderWithClient(<ControlledPicker spec={openrouterTts} />);
    // "No OpenRouter keys yet", not "No OpenRouter (TTS) keys yet" — the
    // empty-state hint reads at the vendor level too (`credentialDisplay`),
    // since this is the shared OpenRouter key, not a TTS-only one.
    expect(await screen.findByText(/No OpenRouter keys yet/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Test key" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Add key/ }));
    fireEvent.change(await screen.findByLabelText(/^Name/), { target: { value: "Shared key" } });
    fireEvent.change(screen.getByLabelText(/OpenRouter API key/), { target: { value: "sk-or-v1-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    // Saved: the dialog shows the fingerprint and its own "Test key" action.
    expect(await screen.findByRole("heading", { name: "Key saved" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    // Closing the dialog leaves exactly one "Test key" action: the picker's
    // own, for the now-selected key — proof the refetch-on-save fix picked
    // up the credential the alias's own POST created (stored under the home).
    expect(await screen.findByRole("button", { name: "Test key" })).toBeTruthy();
    expect(screen.queryByText(/No OpenRouter keys yet/)).toBeNull();
  });
});

describe("CredentialDialog — the shared-key notice (V4-04, R-V4-7 follow-up)", () => {
  it("names the arrangement for an aliased provider (openrouter-tts): title and the full service list", async () => {
    stubApi();
    renderWithClient(<CredentialDialog spec={openrouterTts} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    // "Add OpenRouter key", not "Add OpenRouter (TTS) key" — opened from any
    // OpenRouter slot this should read the same, since the key it saves
    // works for every one of them.
    expect(await screen.findByRole("heading", { name: "Add OpenRouter key" })).toBeTruthy();
    // Kinds are listed in `KIND_ORDER` (stt, llm, tts, image_gen, embedding
    // among this registry's kinds), not alphabetically or by alias order.
    // `findByText` (not `getByText`): the full sibling list only arrives
    // once the registry fetch this notice depends on resolves.
    // Kinds are listed in `KIND_ORDER` (stt, llm, tts, image_gen, embedding
    // among this registry's kinds), not alphabetically or by alias order.
    // `findByText` (not `getByText`): the full sibling list only arrives
    // once the registry fetch this notice depends on resolves.
    // The sentence is split across several text nodes (one JSX expression
    // each), so it's asserted against the dialog's own text content rather
    // than `getByText`, which can't match a string split across siblings.
    await waitFor(() =>
      expect(document.querySelector('[data-slot="dialog-body"]')?.textContent).toContain(
        "One key for all OpenRouter services: Speech-to-text, Language models, Text-to-speech, Image generation, Embeddings.",
      ),
    );
  });

  it("says nothing for the credential home itself (openrouter-llm)", async () => {
    stubApi();
    renderWithClient(<CredentialDialog spec={openrouterLlm} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    await screen.findByLabelText(/^Name/);
    expect(screen.queryByText(/shared by every/)).toBeNull();
  });

  it("says nothing for a provider with no credential home at all (deepgram-stt)", async () => {
    stubApi();
    renderWithClient(<CredentialDialog spec={deepgramStt} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    await screen.findByLabelText(/^Name/);
    expect(screen.queryByText(/shared by every/)).toBeNull();
  });
});

describe("ProviderRow — the key badge for an alias (V4-04)", () => {
  it("openrouter-stt shows the key stored under openrouter-llm, with no web-side lookup change", async () => {
    stubApi();
    const provider: ProviderOut = { ...openrouterStt, enabled: true, installed_on: [] };
    renderWithClient(<ProviderRow provider={provider} connections={[]} />);
    expect(await screen.findByText("Key set")).toBeTruthy();
  });

  it("a provider with no key anywhere (deepgram-stt) shows Key required", async () => {
    stubApi();
    const provider: ProviderOut = { ...deepgramStt, enabled: true, installed_on: [] };
    renderWithClient(<ProviderRow provider={provider} connections={[]} />);
    expect(await screen.findByText("Key required")).toBeTruthy();
  });
});
