import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CatalogDialog } from "@/components/console/providers/catalog-dialog";
import { ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import { RegistryForm } from "@/components/console/registry/registry-form";
import type { CatalogItem, ProviderOut, ProviderRef } from "@/contracts/lkap-contracts";
import { catalogResponse, CREDENTIAL, record, REGISTRY, routeFetch, spec, testResult, type Handler, type Role } from "./fixtures/provider-models";

/**
 * V4-09's catalog half: the Providers page's Catalog dialog (search, 200 per
 * page with the api's `total`, Test per row for admins) and the catalog
 * pickers in the slot editor (ask #46: the api pages at 200 by default, so
 * the picker asks for the whole list; voices narrowed to the slot's model;
 * a search box above 20 items).
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
  vi.useRealTimers();
});

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const OPENROUTER: ProviderOut = { ...spec("openrouter-llm"), default_credential_id: CREDENTIAL.id };

/** 458 items served the way the api pages them: `q` over id/label, then `offset`/`limit`, `total` = matches. */
const ALL: CatalogItem[] = Array.from({ length: 458 }, (_, i) => ({
  id: i < 30 ? `google/gemini-${i}` : `vendor/model-${i}`,
  label: i < 30 ? `Gemini ${i}` : `Model ${i}`,
}));

function pagedCatalog(req: { url: string }) {
  if (!req.url.includes("/catalog")) return undefined;
  const params = new URL(req.url, "http://x").searchParams;
  const q = (params.get("q") ?? "").toLowerCase();
  const offset = Number(params.get("offset") ?? 0);
  const limit = Number(params.get("limit") ?? 200);
  const matches = ALL.filter((item) => !q || item.id.toLowerCase().includes(q) || item.label.toLowerCase().includes(q));
  return { body: { kind: "models", items: matches.slice(offset, offset + limit), total: matches.length, source: "vendor" } };
}

function stub(role: Role, extra: Handler[] = []) {
  const routed = routeFetch({ role, handlers: [...(extra ?? []), pagedCatalog] });
  vi.stubGlobal("fetch", routed.fetch);
  return routed;
}

async function openDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Catalog" }));
  return screen.findByRole("dialog");
}

describe("Catalog dialog — paging and search", () => {
  it("pages 200 at a time and shows the api's total", async () => {
    const { calls } = stub("admin");
    withClient(<CatalogDialog provider={OPENROUTER} />);
    const dialog = await openDialog();
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 1–200 of 458"));
    expect(calls.find((c) => c.url.includes("/catalog"))?.url).toContain("limit=200");

    fireEvent.click(within(dialog).getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 201–400 of 458"));
    expect(calls.some((c) => c.url.includes("offset=200"))).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 401–458 of 458"));
    expect(within(dialog).getByRole("button", { name: /Next/ }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: /Previous/ }));
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 201–400 of 458"));
  });

  it("search asks the api's cached list (`q`, never the vendor) and starts again on page one", async () => {
    const { calls } = stub("admin");
    withClient(<CatalogDialog provider={OPENROUTER} />);
    const dialog = await openDialog();
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 1–200 of 458"));
    fireEvent.click(within(dialog).getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toContain("201–400"));

    fireEvent.change(within(dialog).getByRole("searchbox", { name: /Search the models/ }), { target: { value: "gemini" } });
    await waitFor(() => expect(within(dialog).getByTestId("catalog-range").textContent).toBe("Showing 1–30 of 30"));
    expect(within(dialog).getByText("Gemini 0", { selector: "p" })).toBeTruthy();
    expect(within(dialog).queryByText("Model 40")).toBeNull();
    const search = calls.filter((c) => c.url.includes("q=gemini"));
    expect(search.length).toBeGreaterThan(0);
    expect(search.every((c) => !c.url.includes("search_vendor"))).toBe(true);
    expect(search.at(-1)?.url).not.toContain("offset=");
  });
});

describe("Catalog dialog — Test per row", () => {
  it("admins get a Test button per model row; a run posts the row's id with the default key and shows the chip", async () => {
    const { calls } = stub("admin", [
      (req) =>
        req.method === "POST" && req.url.includes("/test-model")
          ? { body: testResult({ model: "google/gemini-0", latency_ms: 321 }) }
          : undefined,
    ]);
    withClient(<CatalogDialog provider={OPENROUTER} />);
    const dialog = await openDialog();
    const row = (await within(dialog).findByText("Gemini 0", { selector: "p" })).closest("li") as HTMLElement;
    await waitFor(() => expect(within(row).getByRole("button", { name: /Test/ })).toBeTruthy());
    fireEvent.click(within(row).getByRole("button", { name: /Test/ }));
    await waitFor(() => expect(within(row).getByText(/^Tested ✓/)).toBeTruthy());
    expect(within(row).getByText("321 ms")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ model: "google/gemini-0", credential_id: CREDENTIAL.id, fields: {}, probes: ["basic", "tools"] });
  });

  it("shows a stored record's chip on its row", async () => {
    stub("admin", [
      (req) =>
        /\/providers\/openrouter-llm\/models\?/.test(req.url)
          ? { body: { items: [record({ model_id: "google/gemini-1", last_test_ok: false, last_test_message: "no endpoints" })], total: 1 } }
          : undefined,
    ]);
    withClient(<CatalogDialog provider={OPENROUTER} />);
    const dialog = await openDialog();
    const row = (await within(dialog).findByText("Gemini 1", { selector: "p" })).closest("li") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("Test failed · no endpoints")).toBeTruthy());
  });

  it.each(["builder", "viewer"] as const)("a %s gets no Test button", async (role) => {
    stub(role);
    withClient(<CatalogDialog provider={OPENROUTER} />);
    const dialog = await openDialog();
    await within(dialog).findByText("Gemini 0", { selector: "p" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(within(dialog).queryByRole("button", { name: /^Test/ })).toBeNull();
  });

  it("a voices catalog has no Test column", async () => {
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse([{ id: "v1", label: "Voice one" }], "voices") } : undefined)]);
    withClient(<CatalogDialog provider={{ ...spec("elevenlabs-tts"), default_credential_id: null }} />);
    const dialog = await openDialog();
    await within(dialog).findByText("Voice one");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(within(dialog).queryByRole("button", { name: /^Test/ })).toBeNull();
  });
});

describe("catalog pickers in the slot editor (ask #46)", () => {
  /** Two Gemini TTS models that share the voice `Kore` (ask #13 c), plus one voice of the second only. */
  const VOICES: CatalogItem[] = [
    { id: "Kore", label: "Kore (Gemini 3.8 Flash TTS)", meta: { model: "google/gemini-3.8-flash-tts" } },
    { id: "Puck", label: "Puck (Gemini 3.8 Flash TTS)", meta: { model: "google/gemini-3.8-flash-tts" } },
    { id: "Kore", label: "Kore (Gemini 3.8 Flash Lite TTS)", meta: { model: "google/gemini-3.8-flash-lite-tts" } },
    { id: "Zephyr", label: "Zephyr (Gemini 3.8 Flash Lite TTS)", meta: { model: "google/gemini-3.8-flash-lite-tts" } },
  ];
  function voicesByModel(req: { url: string }) {
    if (!req.url.includes("kind=voices")) return undefined;
    const model = new URL(req.url, "http://x").searchParams.get("model");
    const items = model ? VOICES.filter((v) => v.meta?.model === model) : VOICES;
    return { body: catalogResponse(items, "voices") };
  }

  it("the OpenRouter TTS voice picker lists one model's voices once a model is chosen (model=, limit=1000)", async () => {
    const { calls } = stub("admin", [voicesByModel, (req) => (req.url.includes("/catalog") ? { body: catalogResponse([]) } : undefined)]);
    const initial: ProviderRef = {
      provider_id: "openrouter-tts",
      credential_id: CREDENTIAL.id,
      model: "google/gemini-3.8-flash-lite-tts",
      fields: { voice: "" },
    };
    function Harness() {
      const [value, setValue] = React.useState<ProviderRef | null>(initial);
      return <ProviderSlotEditor kind="tts" value={value} onChange={setValue} providers={REGISTRY} idPrefix="t-tts" />;
    }
    withClient(<Harness />);
    await waitFor(() => expect(calls.some((c) => c.url.includes("kind=voices"))).toBe(true));
    const voiceCall = calls.find((c) => c.url.includes("kind=voices"));
    expect(voiceCall?.url).toContain("model=google%2Fgemini-3.8-flash-lite-tts");
    expect(voiceCall?.url).toContain("limit=1000");

    const trigger = await screen.findByRole("combobox", { name: "Voice" });
    fireEvent.click(trigger);
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Zephyr (Gemini 3.8 Flash Lite TTS)")).toBeTruthy();
    expect(within(listbox).getByText("Kore (Gemini 3.8 Flash Lite TTS)")).toBeTruthy();
    expect(within(listbox).queryByText(/Puck/)).toBeNull();
    expect(within(listbox).queryByText("Kore (Gemini 3.8 Flash TTS)")).toBeNull();
  });

  it("a voice-only catalog (ElevenLabs) is never narrowed by model", async () => {
    const { calls } = stub("admin");
    withClient(
      <RegistryForm
        fields={[{ name: "voice_id", label: "Voice", type: "catalog", catalog_kind: "voices" }]}
        values={{ voice_id: "" }}
        onChange={() => {}}
        catalogContext={{ providerId: "elevenlabs-tts", kind: "tts", catalog: spec("elevenlabs-tts").catalog, credentialId: null, model: "eleven_turbo_v2_5" }}
      />,
    );
    await waitFor(() => expect(calls.some((c) => c.url.includes("kind=voices"))).toBe(true));
    expect(calls.find((c) => c.url.includes("kind=voices"))?.url).not.toContain("model=");
  });

  it("a search box appears above 20 items and filters the list", async () => {
    const many: CatalogItem[] = Array.from({ length: 25 }, (_, i) => ({ id: `face-${i}`, label: i === 7 ? "Ada the agent" : `Face ${i}` }));
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse(many, "avatars") } : undefined)]);
    withClient(
      <RegistryForm
        fields={[{ name: "face_id", label: "Face", type: "catalog", catalog_kind: "avatars" }]}
        values={{ face_id: "" }}
        onChange={() => {}}
        catalogContext={{ providerId: "simli-avatar", kind: "avatar", catalog: { adapter: "simli_faces", kinds: ["avatars"] }, credentialId: null }}
      />,
    );
    const search = await screen.findByRole("searchbox", { name: /Search the avatars list/ });
    fireEvent.change(search, { target: { value: "ada" } });
    expect(await screen.findByText("1 of 25 match.")).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox", { name: "Face" }));
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Ada the agent")).toBeTruthy();
    expect(within(listbox).queryByText("Face 3")).toBeNull();
  });

  it("no search box at 20 items or fewer", async () => {
    const few: CatalogItem[] = Array.from({ length: 20 }, (_, i) => ({ id: `face-${i}`, label: `Face ${i}` }));
    stub("admin", [(req) => (req.url.includes("/catalog") ? { body: catalogResponse(few, "avatars") } : undefined)]);
    withClient(
      <RegistryForm
        fields={[{ name: "face_id", label: "Face", type: "catalog", catalog_kind: "avatars" }]}
        values={{ face_id: "" }}
        onChange={() => {}}
        catalogContext={{ providerId: "simli-avatar", kind: "avatar", catalog: { adapter: "simli_faces", kinds: ["avatars"] }, credentialId: null }}
      />,
    );
    await screen.findByRole("combobox", { name: "Face" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Face" }).textContent).toContain("Choose"));
    expect(screen.queryByRole("searchbox")).toBeNull();
  });
});
