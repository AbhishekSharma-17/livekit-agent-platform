import * as React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EditorContextProvider } from "@/components/console/agents/editor/editor-context";
import { testedStateFor, TESTED_WINDOW_MS } from "@/components/console/registry/model-test-panel";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import { newProviderRef, ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";
import {
  anyCallCarries,
  catalogResponse,
  CREDENTIAL,
  HEX32,
  record,
  REGISTRY,
  routeFetch,
  spec,
  testResult,
  type Handler,
  type Role,
} from "./fixtures/provider-models";

/**
 * V4-09's slot editor half (docs/v4/CUSTOM-MODELS.md D-V4-26 "Shown as"):
 * the model field for every model kind, Test model and its inline result
 * panel, the tested chip, "This model can…", the slot card's badge and chip,
 * and where id-field issues land.
 */

const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
  Element.prototype.scrollIntoView = nativeScrollIntoView;
});
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function Harness({ kind, initial, providers = REGISTRY }: { kind: ProviderSpec["kind"]; initial: ProviderRef; providers?: ProviderSpec[] }) {
  const [value, setValue] = React.useState<ProviderRef | null>(initial);
  return <ProviderSlotEditor kind={kind} value={value} onChange={setValue} providers={providers} idPrefix={`t-${kind}`} />;
}

const CUSTOM_LLM: ProviderRef = {
  provider_id: "openrouter-llm",
  credential_id: CREDENTIAL.id,
  model: "acme/private-ft-7",
  fields: {},
};

function stub(role: Role, handlers: Handler[] = []) {
  const routed = routeFetch({ role, handlers });
  vi.stubGlobal("fetch", routed.fetch);
  return routed;
}

const testPost = (body: unknown, status = 200) => (req: { url: string; method: string }) =>
  req.method === "POST" && req.url.includes("/test-model") ? { status, body } : undefined;

async function clickTest() {
  // The button is gated until the role loads (a fresh element then replaces the disabled one).
  await waitFor(() => expect(screen.getByRole("button", { name: /Test model/ }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: /Test model/ }));
}

describe("the model field for every model kind (R-V4-22)", () => {
  it("assemblyai-stt (no `models`) shows the model combobox for its type=\"model\" field, once, not under Options", () => {
    stub("admin");
    withClient(<Harness kind="stt" initial={{ provider_id: "assemblyai-stt", credential_id: null, model: null, fields: { model: "" } }} />);
    const combos = screen.getAllByRole("combobox").filter((node) => node.id.endsWith("-model"));
    expect(combos).toHaveLength(1);
    expect(combos[0].textContent).toContain("universal-3-5-pro");
    expect(document.querySelector('[id="t-stt-field-model"]')).toBeNull();
  });

  it("a model field's own default is a suggestion, not Custom, and reads no record (editor and card)", async () => {
    const { calls } = stub("admin");
    const assembly = spec("assemblyai-stt");
    const value = newProviderRef(assembly);
    expect(value.fields?.model).toBe("universal-3-5-pro");
    withClient(
      <>
        <Harness kind="stt" initial={value} />
        <ProviderSlotCard
          title="Speech-to-text"
          kind="stt"
          value={value}
          onChange={() => {}}
          expanded={false}
          onExpandedChange={() => {}}
          providers={REGISTRY}
          idPrefix="card-stt"
        />
      </>,
    );
    const trigger = screen.getAllByRole("combobox").find((node) => node.id === "t-stt-model") as HTMLElement;
    expect(trigger.textContent).toContain("universal-3-5-pro");
    expect(trigger.textContent).not.toContain("Custom");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByText("Custom")).toBeNull();
    expect(calls.some((c) => /\/models\//.test(c.url))).toBe(false);
  });

  it("a model kind with neither `models` nor a model field still offers the combobox with the free-text hint", () => {
    stub("admin");
    withClient(<Harness kind="stt" initial={{ provider_id: "speechmatics-stt", credential_id: null, model: null, fields: {} }} />);
    expect(screen.getByRole("combobox", { name: /model/i })).toBeTruthy();
    expect(screen.getByText("Type any id the provider accepts.")).toBeTruthy();
  });

  it("picking from an assemblyai-stt model field writes fields.model, not ProviderRef.model", async () => {
    stub("admin");
    const onChange = vi.fn();
    function Spy() {
      const [value, setValue] = React.useState<ProviderRef | null>({ provider_id: "assemblyai-stt", credential_id: null, model: null, fields: {} });
      return (
        <ProviderSlotEditor
          kind="stt"
          value={value}
          onChange={(next) => {
            setValue(next);
            onChange(next);
          }}
          providers={REGISTRY}
          idPrefix="t-stt"
        />
      );
    }
    withClient(<Spy />);
    fireEvent.click(screen.getByRole("combobox", { name: /model/i }));
    fireEvent.change(await screen.findByPlaceholderText(/Search models or type an id/), { target: { value: "universal-2" } });
    fireEvent.click(await screen.findByText(/Use custom model:/));
    const last = onChange.mock.calls.at(-1)?.[0] as ProviderRef;
    expect(last.fields?.model).toBe("universal-2");
    expect(last.model).toBeNull();
  });
});

describe("Test model", () => {
  it("posts {model, credential_id, fields, probes} and renders latency, probe rows, the sample in a mono box and the caveat", async () => {
    const { calls } = stub("builder", [testPost(testResult())]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();

    await screen.findByText("The vendor accepted this model");
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/test-model"));
    expect(post?.url).toContain("/api/console/providers/openrouter-llm/test-model");
    expect(post?.body).toEqual({ model: "acme/private-ft-7", credential_id: CREDENTIAL.id, fields: {}, probes: ["basic", "tools"] });

    expect(screen.getByTestId("test-latency").textContent).toBe("812 ms");
    const checks = screen.getByRole("list", { name: "Checks" });
    expect(within(checks).getByText("Reply")).toBeTruthy();
    expect(within(checks).getByText("Tool call")).toBeTruthy();
    expect(within(checks).getAllByText("Passed")).toHaveLength(2);

    const sample = screen.getByLabelText("What the model said");
    expect(sample.tagName).toBe("PRE");
    expect(sample.className).toContain("font-mono");
    // Plain text: markup in the vendor's answer is shown, never interpreted.
    expect(sample.textContent).toBe("ok **not markdown** <b>not html</b>");
    expect(sample.querySelector("b, strong")).toBeNull();

    expect(screen.getByTestId("test-caveat").textContent).toMatch(/proves the vendor accepts this model with this key, not that the agent can load it/);
    expect(screen.getByTestId("test-cost").textContent).toMatch(/no price on file/i);
    expect((await screen.findByTestId("tested-chip")).textContent).toMatch(/^Tested ✓/);
  });

  it("sends the slot's option fields (a TTS voice) with the test", async () => {
    const { calls } = stub("admin", [testPost(testResult({ provider_id: "openrouter-tts", kind: "tts", model: "acme/tts-1" }))]);
    withClient(
      <Harness
        kind="tts"
        initial={{ provider_id: "openrouter-tts", credential_id: CREDENTIAL.id, model: "acme/tts-1", fields: { voice: "Kore", speed: 1 } }}
      />,
    );
    await clickTest();
    await screen.findByText("The vendor accepted this model");
    const post = calls.find((c) => c.method === "POST");
    expect((post?.body as { fields: unknown }).fields).toMatchObject({ voice: "Kore", speed: 1 });
  });

  it("a failed test reads “Test failed · <reason>” with the vendor's message in the mono box", async () => {
    stub("builder", [
      testPost(
        testResult({
          ok: false,
          message: "model_not_found: no such model",
          probes: [{ name: "basic", ok: false, latency_ms: 90, message: "404" }],
          sample: null,
        }),
      ),
    ]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();
    await screen.findByText("The vendor refused this model");
    expect(screen.getByLabelText("What the vendor said").textContent).toBe("model_not_found: no such model");
    expect((await screen.findByTestId("tested-chip")).textContent).toBe("Test failed · model_not_found: no such model");
  });

  it("an inconclusive result (ok: null) is headed “Couldn't verify this model”, never “Not tested”, with its message (R-V4-43)", async () => {
    stub("builder", [
      testPost(
        testResult({
          ok: null,
          message: "the key works, but the id is not among this account's own faces (0 listed)",
          probes: [{ name: "basic", ok: null, latency_ms: 120, message: "0 faces listed" }],
          sample: null,
        }),
      ),
    ]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();
    await screen.findByText("Couldn't verify this model");
    expect(screen.queryByText("Not tested")).toBeNull();
    expect(screen.queryByText("The vendor accepted this model")).toBeNull();
    expect(screen.queryByText("The vendor refused this model")).toBeNull();
    expect(screen.getByLabelText("What the vendor said").textContent).toBe(
      "the key works, but the id is not among this account's own faces (0 listed)",
    );
  });

  it("a 429 reads “try again in N s” (retry_after rounded up)", async () => {
    stub("builder", [
      testPost({ error: { code: "rate_limited", message: "too many", details: { retry_after_s: 11.2, retry_after: 11.2 } } }, 429),
    ]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();
    expect(await screen.findByText("Too many tests right now — try again in 12 s.")).toBeTruthy();
  });

  it("a 422 renders the api's message and never the id", async () => {
    stub("builder", [
      testPost({ error: { code: "unprocessable", message: "the voice field is required for this vendor", details: { field: "voice" } } }, 422),
    ]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();
    const message = await screen.findByText(/Couldn't run the test: the voice field is required/);
    expect(message.textContent).not.toContain("acme/private-ft-7");
  });

  it("a cached result offers “Run it again now”, which forces a fresh call", async () => {
    const { calls } = stub("builder", [testPost(testResult({ cached: true, probes: [] }))]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await clickTest();
    fireEvent.click(await screen.findByRole("button", { name: /Run it again now/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect((calls.filter((c) => c.method === "POST")[1].body as { force?: boolean }).force).toBe(true);
  });

  it("a viewer sees the chip but cannot run a test", async () => {
    stub("viewer");
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    const button = await screen.findByRole("button", { name: /Test model/ });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("a stored record shows “Tested ✓” with a relative time; a rotated key resets it to Untested", async () => {
    stub("builder", [(req) => (req.url.includes("/models/acme/private-ft-7") ? { body: record() } : undefined)]);
    const { unmount } = withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await waitFor(async () => expect((await screen.findByTestId("tested-chip")).textContent).toMatch(/^Tested ✓ .*(min ago|just now)/));
    unmount();

    stub("builder", [(req) => (req.url.includes("/models/acme/private-ft-7") ? { body: record({ last_test_fingerprint: "…zzzz" }) } : undefined)]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect((await screen.findByTestId("tested-chip")).textContent).toBe("Untested");
  });
});

describe("testedStateFor", () => {
  const withProbe = { probe: "openai_chat" };
  it("is “no test” for a provider without a probe", () => {
    expect(testedStateFor({ spec: { probe: null }, record: record(), fingerprint: CREDENTIAL.fingerprint }).kind).toBe("no-probe");
  });
  it("expires after 30 days", () => {
    const old = record({ last_test_at: new Date(Date.now() - TESTED_WINDOW_MS - 60_000).toISOString() });
    expect(testedStateFor({ spec: withProbe, record: old, fingerprint: CREDENTIAL.fingerprint }).kind).toBe("untested");
  });
  it("accepts any fingerprint for a key-less slot", () => {
    expect(testedStateFor({ spec: withProbe, record: record({ last_test_fingerprint: "…conn" }), fingerprint: null }).kind).toBe("ok");
  });
});

describe("This model can… (capability toggles)", () => {
  const catalogWithMeta = (req: { url: string }) =>
    req.url.includes("/catalog")
      ? { body: catalogResponse([{ id: "other/model", label: "Other", meta: {} }]) }
      : undefined;

  it("admins declare with PUT …/models/{id} {declared}", async () => {
    const { calls } = stub("admin", [
      catalogWithMeta,
      (req) =>
        req.method === "PUT" && req.url.includes("/models/acme/private-ft-7")
          ? { body: record({ declared: { vision: false } }) }
          : undefined,
      (req) => (req.url.includes("/models/acme/private-ft-7") ? { body: record() } : undefined),
    ]);
    withClient(<Harness kind="llm" initial={CUSTOM_LLM} />);
    const group = await screen.findByRole("radiogroup", { name: "See images" });
    await waitFor(() => expect(within(group).getByLabelText("No")).toBeTruthy());
    fireEvent.click(within(group).getByLabelText("No"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toContain("/api/console/providers/openrouter-llm/models/acme/private-ft-7");
    expect(put?.body).toEqual({ declared: { vision: false } });
    expect(await screen.findByText("Saved")).toBeTruthy();
    expect(screen.getByTestId("capability-vision-source").textContent).toBe("No · set by an admin");
  });

  it("builders see the resolved answer and its source, read-only (“from OpenRouter's catalog”)", async () => {
    stub("builder", [
      (req) =>
        req.url.includes("/catalog")
          ? {
              body: catalogResponse([
                { id: "vendor/seer-2", label: "Seer 2", meta: { architecture: { input_modalities: ["text", "image"] }, supported_parameters: ["tools"] } },
              ]),
            }
          : undefined,
    ]);
    withClient(<Harness kind="llm" initial={{ ...CUSTOM_LLM, model: "vendor/seer-2" }} />);
    await waitFor(() => expect(screen.getByTestId("capability-vision-source").textContent).toBe("Yes · from OpenRouter's catalog"));
    expect(screen.getByTestId("capability-tools-source").textContent).toBe("Yes · from OpenRouter's catalog");
    expect(screen.queryByRole("radiogroup", { name: "See images" })).toBeNull();
    // A catalog id is not "custom": no Custom badge in the trigger.
    expect(within(screen.getByRole("combobox", { name: /model/i })).queryByText("Custom")).toBeNull();
  });

  it("the resolution order is declared, then detected, then the catalog, then the registry", async () => {
    const { resolveCapability } = await import("@/components/console/registry/model-capabilities");
    const openrouter = spec("openrouter-llm");
    const item = { id: "x/y", label: "Y", meta: { architecture: { input_modalities: ["image"] } } };
    const base = { spec: openrouter, modelId: "x/y", catalogItem: item };
    expect(resolveCapability("vision", { ...base, record: record({ declared: { vision: false }, detected: { vision: true } }) })).toEqual({
      value: false,
      source: "declared",
    });
    expect(resolveCapability("vision", { ...base, record: record({ declared: null, detected: { vision: false } }) })).toEqual({
      value: false,
      source: "detected",
    });
    expect(resolveCapability("vision", { ...base, record: record({ declared: null, detected: null }) })).toEqual({ value: true, source: "catalog" });
    expect(resolveCapability("tools", { ...base, catalogItem: null, record: null })).toEqual({ value: true, source: "registry" });
    expect(resolveCapability("vision", { ...base, catalogItem: null, record: null })).toEqual({ value: null, source: null });
  });
});

describe("ProviderSlotCard — custom badge, chip, vision from the record", () => {
  function Card({ value }: { value: ProviderRef }) {
    return (
      <ProviderSlotCard
        title="Language model"
        kind="llm"
        value={value}
        onChange={() => {}}
        expanded={false}
        onExpandedChange={() => {}}
        providers={REGISTRY}
        issuePath="pipeline.llm"
      />
    );
  }

  it("shows Custom and the tested chip beside the model, and the vision badge from the record", async () => {
    stub("builder", [
      (req) => (req.url.includes("/catalog") ? { body: catalogResponse([]) } : undefined),
      (req) =>
        req.url.includes("/models/acme/private-ft-7") ? { body: record({ detected: { tools: true, vision: true } }) } : undefined,
    ]);
    withClient(<Card value={CUSTOM_LLM} />);
    const card = document.querySelector('[data-slot="provider-slot-card"]') as HTMLElement;
    expect(card.getAttribute("data-issue-path")).toBe("pipeline.llm");
    await waitFor(() => expect(within(card).getByText(/^Tested ✓/)).toBeTruthy());
    expect(within(card).getByText("Custom")).toBeTruthy();
    expect(card.querySelector('[data-slot="capability-badge"][data-kind="vision"]')).not.toBeNull();
  });

  it("a registry model shows no Custom badge and reads no record", async () => {
    const { calls } = stub("builder");
    withClient(<Card value={{ ...CUSTOM_LLM, model: "openai/gpt-4.1" }} />);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByText("Custom")).toBeNull();
    expect(calls.some((c) => /\/models\//.test(c.url))).toBe(false);
  });

  it("a key-like stored model is never sent anywhere from the card", async () => {
    const { calls } = stub("builder");
    withClient(<Card value={{ ...CUSTOM_LLM, model: HEX32 }} />);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(anyCallCarries(calls, HEX32)).toBe(false);
  });
});

describe("id-field issues (R-V4-31; V4-08 moved them to <slot>.fields.<name>)", () => {
  const simli: ProviderRef = { provider_id: "simli-avatar", credential_id: null, model: null, fields: { "simli_config.face_id": HEX32 } };

  it("a 32-hex face id shows the value-free warning inline and is not marked invalid", async () => {
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse([], "avatars") } : undefined)]);
    withClient(<Harness kind="avatar" initial={simli} />);
    const warning = await screen.findByText(/Check this value: it looks like an API key; if it is the vendor's id, ignore this\./);
    expect(warning.closest('[data-slot="field-warning"]')).not.toBeNull();
    const input = document.getElementById("t-avatar-field-simli_config.face_id") as HTMLInputElement;
    expect(input.getAttribute("aria-invalid")).not.toBe("true");
    expect(document.body.textContent).not.toContain(HEX32.slice(0, 8));
  });

  it("a prefixed key in an id field is an error", async () => {
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse([], "avatars") } : undefined)]);
    withClient(<Harness kind="avatar" initial={{ ...simli, fields: { "simli_config.face_id": "sk_Zq9WkX7vRt3LmN8pYb2HcJ5d" } }} />);
    expect(await screen.findByText(/Can't use this value: it looks like an API key, not a model id\./)).toBeTruthy();
  });

  it("an api issue at pipeline.avatar.fields.<name> lands under that field, and the control carries the path", async () => {
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse([], "avatars") } : undefined)]);
    const issue = {
      key: "i1",
      path: "pipeline.avatar.fields.simli_config.emotion_id",
      message: "pipeline.avatar.fields.simli_config.emotion_id: field 'simli_config.emotion_id' is not a known emotion",
      severity: "warning" as const,
      section: "providers",
      source: "server" as const,
    };
    withClient(
      <EditorContextProvider
        value={{
          issues: [issue],
          activeSection: "providers",
          focusIssue: () => {},
          goToSection: () => {},
          sections: [],
          agent: {} as never,
        }}
      >
        <ProviderSlotCard
          title="Avatar"
          kind="avatar"
          value={{ ...simli, fields: {} }}
          onChange={() => {}}
          expanded
          onExpandedChange={() => {}}
          providers={REGISTRY}
          issuePath="pipeline.avatar"
          idPrefix="t-avatar"
        />
      </EditorContextProvider>,
    );
    expect(await screen.findByText(/is not a known emotion/)).toBeTruthy();
    const control = document.querySelector('[data-issue-path="pipeline.avatar.fields.simli_config.emotion_id"]');
    expect(control).not.toBeNull();
  });
});

describe("guards (grep)", () => {
  const files = [
    "src/components/console/registry/model-combobox.tsx",
    "src/components/console/registry/model-test-panel.tsx",
    "src/components/console/registry/model-capabilities.tsx",
    "src/components/console/registry/provider-slot-editor.tsx",
    "src/components/console/registry/provider-slot-card.tsx",
    "src/components/console/registry/registry-form.tsx",
    "src/components/console/providers/catalog-dialog.tsx",
  ];

  it.each(files)("%s imports no side drawer and renders no vendor text as markup", (file) => {
    const source = readFileSync(path.resolve(__dirname, "..", file), "utf8");
    expect(source).not.toMatch(/@\/components\/ui\/(sheet|drawer)|from "vaul"/);
    expect(source).not.toMatch(/dangerouslySetInnerHTML|react-markdown|ReactMarkdown|marked\(/);
  });
});
