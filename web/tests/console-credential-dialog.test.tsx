import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CredentialDialog } from "@/components/console/registry/credential-dialog";
import { classifyTestResult } from "@/components/console/registry/credential-test";
import { suggestedCredentialLabel } from "@/components/console/registry/provider-meta";
import type { CredentialOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * The credential dialog (docs/UI_UX_SPEC.md §4.5, §7.5 item 5; v2
 * amendments: "Test result + last tested"). Replaces the v1
 * `CreateCredentialDialog`; the secret-leak and nested-form guarantees are
 * kept from that dialog's tests.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // floating-ui / Radix ask jsdom for `:popover-open` / `:modal`, which is
  // very slow there; nothing is in the top layer in jsdom (see
  // console-editor-shell.test.tsx).
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

const SECRET_VALUE = "sk-super-secret-do-not-leak";

const openaiSpec: ProviderSpec = {
  v: 1,
  id: "openai-llm",
  kind: "llm",
  label: "OpenAI",
  vendor: "OpenAI",
  status: "mvp",
  package: "livekit-plugins-openai",
  python_class: "livekit.plugins.openai.LLM",
  requires_credential: true,
  secret_fields: [{ name: "api_key", label: "API key", type: "secret", required: true }],
  fields: [],
  models: [],
  default_model: null,
  get_key_url: "https://platform.openai.com/api-keys",
  capabilities: { video_input: false, tool_calling: true, silent_tool_reply: false, voices: [] },
};

const secretBagSpec: ProviderSpec = {
  v: 1,
  id: "http-tool-secret",
  kind: "secret_bag",
  label: "Tool secrets",
  vendor: "LKAP",
  status: "mvp",
  package: "",
  python_class: "",
  requires_credential: true,
  secret_fields: [],
  fields: [],
  models: [],
  default_model: null,
  capabilities: { video_input: false, tool_calling: false, silent_tool_reply: false, voices: [] },
};

const stored: CredentialOut = {
  id: "cred_1",
  provider_id: openaiSpec.id,
  label: "Test key",
  fingerprint: "…zzzz",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function stubApi(handler: (call: Call) => { status?: number; body?: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      calls.push(call);
      const { status = 200, body } = handler(call);
      return {
        ok: status < 400,
        status,
        json: async () => body,
      } as Response;
    }),
  );
  return calls;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CredentialDialog — create", () => {
  it("pre-fills a suggested name and locks the provider when opened from a slot", async () => {
    stubApi(() => ({ body: stored }));
    renderWithClient(<CredentialDialog spec={openaiSpec} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));

    const name = (await screen.findByLabelText(/^Name/)) as HTMLInputElement;
    expect(name.value).toBe(suggestedCredentialLabel(openaiSpec));
    expect(screen.getByRole("heading", { name: "Add OpenAI key" })).toBeTruthy();
    // Locked provider: no provider picker, and a "Get one from OpenAI" link from `get_key_url`.
    expect(screen.queryByLabelText(/^Provider/)).toBeNull();
    expect(screen.getByRole("link", { name: /Get one from OpenAI/ }).getAttribute("href")).toBe(openaiSpec.get_key_url);
  });

  it("never leaves the submitted secret in the DOM, and shows the fingerprint instead", async () => {
    const onSaved = vi.fn();
    const calls = stubApi(() => ({ status: 201, body: stored }));
    renderWithClient(
      <CredentialDialog spec={openaiSpec} onSaved={onSaved} trigger={<button type="button">Open</button>} />,
    );
    fireEvent.click(screen.getByText("Open"));

    const name = await screen.findByLabelText(/^Name/);
    fireEvent.change(name, { target: { value: "Test key" } });
    const secretInput = screen.getByLabelText(/API key/) as HTMLInputElement;
    fireEvent.change(secretInput, { target: { value: SECRET_VALUE } });
    expect(secretInput.getAttribute("type")).toBe("password");
    expect(secretInput.value).toBe(SECRET_VALUE);

    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));

    expect(calls[0]).toMatchObject({
      method: "POST",
      body: { provider_id: "openai-llm", label: "Test key", secrets: { api_key: SECRET_VALUE } },
    });
    // Saved state: fingerprint + "Test key", no secret input and no secret anywhere.
    expect(await screen.findByRole("heading", { name: "Key saved" })).toBeTruthy();
    expect(screen.getByText("…zzzz")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test key" })).toBeTruthy();
    expect(screen.queryByLabelText(/API key/)).toBeNull();
    expect(document.body.innerHTML.includes(SECRET_VALUE)).toBe(false);
    expect(Array.from(document.querySelectorAll("input")).every((input) => input.value !== SECRET_VALUE)).toBe(true);
  });

  it("toggles a secret between masked and visible", async () => {
    stubApi(() => ({ body: stored }));
    renderWithClient(<CredentialDialog spec={openaiSpec} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    const secretInput = (await screen.findByLabelText(/API key/)) as HTMLInputElement;
    expect(secretInput.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Show value" }));
    expect(secretInput.type).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide value" }));
    expect(secretInput.type).toBe("password");
  });

  it("validates inline (no toast, no request) when the name or a required secret is missing", async () => {
    const calls = stubApi(() => ({ body: stored }));
    renderWithClient(<CredentialDialog spec={openaiSpec} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    const name = await screen.findByLabelText(/^Name/);
    fireEvent.change(name, { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    expect(await screen.findByText("Give the key a name you'll recognise.")).toBeTruthy();
    expect(screen.getByText("Enter the api key.")).toBeTruthy();
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(calls).toHaveLength(0);
  });

  it("does not bubble its submit to an outer form (it opens inside the agent editor form)", async () => {
    const onSaved = vi.fn();
    const outerSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    stubApi(() => ({ status: 201, body: stored }));
    renderWithClient(
      <form onSubmit={outerSubmit}>
        <CredentialDialog spec={openaiSpec} onSaved={onSaved} trigger={<button type="button">Open</button>} />
      </form>,
    );
    fireEvent.click(screen.getByText("Open"));
    fireEvent.change(await screen.findByLabelText(/API key/), { target: { value: SECRET_VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(outerSubmit).not.toHaveBeenCalled();
  });

  it("renders a free-form NAME/value editor for secret_bag providers (http-tool-secret)", async () => {
    const calls = stubApi(() => ({ status: 201, body: { ...stored, provider_id: "http-tool-secret" } }));
    renderWithClient(<CredentialDialog spec={secretBagSpec} trigger={<button type="button">Open bag</button>} />);
    fireEvent.click(screen.getByText("Open bag"));

    fireEvent.change(await screen.findByLabelText("Secret 1 name"), { target: { value: "crm_token" } });
    fireEvent.change(screen.getByLabelText("Secret 1 value"), { target: { value: SECRET_VALUE } });
    fireEvent.click(screen.getByRole("button", { name: /Add pair/ }));
    expect(screen.getByLabelText("Secret 2 name")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    // Names are upper-cased; the empty second pair is dropped.
    expect(calls[0].body).toMatchObject({ secrets: { CRM_TOKEN: SECRET_VALUE } });
  });
});

describe("CredentialDialog — test result and last tested", () => {
  it("offers Test key after saving and shows the result with a last-tested time", async () => {
    stubApi(({ url }) =>
      url.endsWith("/credentials/cred_1/test")
        ? { body: { ok: true, message: "Listed 42 models", checked_at: new Date().toISOString() } }
        : { status: 201, body: stored },
    );
    renderWithClient(<CredentialDialog spec={openaiSpec} trigger={<button type="button">Open</button>} />);
    fireEvent.click(screen.getByText("Open"));
    fireEvent.change(await screen.findByLabelText(/API key/), { target: { value: SECRET_VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    fireEvent.click(await screen.findByRole("button", { name: "Test key" }));
    expect(await screen.findByText("Key works")).toBeTruthy();
    expect(screen.getByText("Listed 42 models")).toBeTruthy();
    expect(screen.getByText(/Last tested/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test again" })).toBeTruthy();
  });

  it("rotate: blank secrets send only the label, and the current key can be tested", async () => {
    const calls = stubApi(({ url }) =>
      url.endsWith("/test") ? { body: { ok: false, message: "401 Unauthorized" } } : { body: { ...stored, label: "Renamed key" } },
    );
    renderWithClient(
      <CredentialDialog mode="rotate" spec={openaiSpec} credential={stored} trigger={<button type="button">Open</button>} />,
    );
    fireEvent.click(screen.getByText("Open"));

    expect(await screen.findByRole("heading", { name: "Rotate Test key" })).toBeTruthy();
    expect(screen.getByText("Leave blank to keep the stored value.")).toBeTruthy();
    expect((screen.getByLabelText(/API key/) as HTMLInputElement).placeholder).toMatch(/unchanged/);

    // Test the stored key from the rotate view.
    const current = screen.getByText("Current key").closest("div")!.parentElement!.parentElement!;
    fireEvent.click(within(current).getByRole("button", { name: "Test key" }));
    expect(await screen.findByText("Test failed")).toBeTruthy();
    expect(screen.getByText("401 Unauthorized")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "Renamed key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/credentials/cred_1");
    expect(put.body).toEqual({ label: "Renamed key" });
  });

  it("rename mode shows only the name", async () => {
    const calls = stubApi(() => ({ body: { ...stored, label: "New" } }));
    renderWithClient(
      <CredentialDialog mode="rename" spec={openaiSpec} credential={stored} trigger={<button type="button">Open</button>} />,
    );
    fireEvent.click(screen.getByText("Open"));
    expect(await screen.findByRole("heading", { name: "Rename Test key" })).toBeTruthy();
    expect(screen.queryByLabelText(/API key/)).toBeNull();
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "New" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ method: "PUT", body: { label: "New" } });
  });

  it.each([
    [{ ok: true, message: "ok" }, "passed"],
    [{ ok: false, message: "bad key" }, "failed"],
    [{ ok: true, message: "not implemented for avatar providers" }, "not-implemented"],
  ] as const)("classifies %o as %s", (result, outcome) => {
    expect(classifyTestResult(result)).toBe(outcome);
  });
});
