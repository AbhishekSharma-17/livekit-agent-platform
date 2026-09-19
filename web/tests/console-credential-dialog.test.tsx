import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CreateCredentialDialog } from "@/components/console/registry/create-credential-dialog";
import type { ProviderSpec } from "@/contracts/lkap-contracts";

const SECRET_VALUE = "sk-super-secret-do-not-leak";

const typedProviderSpec: ProviderSpec = {
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

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function stubFetchOnce(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: true,
          status: 201,
          json: async () => body,
        }) as Response,
    ),
  );
}

describe("CreateCredentialDialog", () => {
  beforeEach(() => {
    stubFetchOnce({
      id: "cred_1",
      provider_id: typedProviderSpec.id,
      label: "Test key",
      fingerprint: "…zzzz",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("never leaves the submitted secret value in the DOM", async () => {
    const onCreated = vi.fn();
    const { getByText, getByLabelText, getByPlaceholderText, queryByText } = renderWithClient(
      <CreateCredentialDialog spec={typedProviderSpec} onCreated={onCreated} trigger={<button>Open</button>} />,
    );

    fireEvent.click(getByText("Open"));

    const labelInput = await waitFor(() => getByPlaceholderText(/Production OpenAI key/));
    fireEvent.change(labelInput, { target: { value: "Test key" } });

    const secretInput = getByLabelText(/API key/);
    fireEvent.change(secretInput, { target: { value: SECRET_VALUE } });
    expect(secretInput.getAttribute("type")).toBe("password");
    expect((secretInput as HTMLInputElement).value).toBe(SECRET_VALUE);

    fireEvent.click(getByText("Save credential"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));

    // The dialog closes (its form, including the secret input, unmounts)...
    expect(queryByText("Save credential")).toBeNull();
    // ...and the raw value is gone both from markup and from any live input
    // element's current value (guards against the assertion only passing
    // because of unmount timing).
    expect(document.body.innerHTML.includes(SECRET_VALUE)).toBe(false);
    expect(Array.from(document.querySelectorAll("input")).every((input) => input.value !== SECRET_VALUE)).toBe(true);
  });

  it("does not bubble its submit to an outer form (nested inside the agent editor form)", async () => {
    const onCreated = vi.fn();
    const outerSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    const { getByText, getByLabelText, getByPlaceholderText } = renderWithClient(
      <form onSubmit={outerSubmit}>
        <CreateCredentialDialog spec={typedProviderSpec} onCreated={onCreated} trigger={<button type="button">Open</button>} />
      </form>,
    );

    fireEvent.click(getByText("Open"));
    const labelInput = await waitFor(() => getByPlaceholderText(/Production OpenAI key/));
    fireEvent.change(labelInput, { target: { value: "Test key" } });
    fireEvent.change(getByLabelText(/API key/), { target: { value: SECRET_VALUE } });
    fireEvent.click(getByText("Save credential"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(outerSubmit).not.toHaveBeenCalled();
  });

  it("renders a free-form NAME=value editor for secret_bag providers (http-tool-secret)", async () => {
    const onCreated = vi.fn();
    const { getByText } = renderWithClient(
      <CreateCredentialDialog spec={secretBagSpec} onCreated={onCreated} trigger={<button>Open bag</button>} />,
    );

    fireEvent.click(getByText("Open bag"));
    const addButton = await waitFor(() => getByText("Add pair"));
    expect(addButton).toBeTruthy();
  });
});
