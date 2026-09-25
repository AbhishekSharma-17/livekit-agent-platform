import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ModelCombobox } from "@/components/console/registry/model-combobox";
import { ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import type { ProviderRef } from "@/contracts/lkap-contracts";
import {
  anyCallCarries,
  bigCatalog,
  catalogResponse,
  CREDENTIAL,
  OPENROUTER_KEY,
  record,
  REGISTRY,
  routeFetch,
  spec,
} from "./fixtures/provider-models";

/**
 * V4-09's model combobox (docs/v4/CUSTOM-MODELS.md D-V4-23, §3): the four
 * groups, the vendor search row, the model-id mirror (R-V4-31) and "no
 * request for a value that fails the rule" (R-V4-32).
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

const openrouterLlm = spec("openrouter-llm");
const openaiLlm = spec("openai-llm");

function ComboHarness({
  providerId = "openrouter-llm",
  initial = "",
  onChange,
}: {
  providerId?: string;
  initial?: string;
  onChange?: (value: string) => void;
}) {
  const provider = spec(providerId);
  const [value, setValue] = React.useState(initial);
  return (
    <ModelCombobox
      id="m"
      models={provider.models ?? []}
      value={value}
      defaultModel={provider.default_model}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
      provider={provider}
      credentialId={CREDENTIAL.id}
    />
  );
}

function catalogHandler(items = bigCatalog()) {
  return (req: { url: string }) => (req.url.includes("/catalog") && !req.url.includes("search_vendor") ? { body: catalogResponse(items) } : undefined);
}

function group(heading: string): HTMLElement {
  const node = screen.getByText(heading, { selector: "[cmdk-group-heading], [cmdk-group-heading] *" }).closest("[cmdk-group]");
  if (!node) throw new Error(`no group ${heading}`);
  return node as HTMLElement;
}

async function openAndType(text: string) {
  fireEvent.click(screen.getByRole("combobox"));
  const input = await screen.findByPlaceholderText(/Search models or type an id/);
  fireEvent.change(input, { target: { value: text } });
  return input;
}

describe("ModelCombobox — groups", () => {
  it("with a 300-item catalog and `gem` typed: Catalog shows the matches, Suggested its own, and the custom row is offered", async () => {
    const { fetch } = routeFetch({ handlers: [catalogHandler()] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    fireEvent.click(screen.getByRole("combobox"));
    await screen.findByText("Gemini X 0");
    const input = screen.getByPlaceholderText(/Search models or type an id/);
    fireEvent.change(input, { target: { value: "gem" } });

    await waitFor(() => expect(within(group("Suggested")).getByText("Gemini 3.5 Flash")).toBeTruthy());
    const catalog = group("Catalog");
    expect(within(catalog).getAllByText(/^Gemini X \d+$/)).toHaveLength(12);
    expect(within(catalog).queryByText(/Acme model/)).toBeNull();
    expect(within(group("Suggested")).queryByText("GPT-4.1")).toBeNull();
    expect(screen.getByText(/Use custom model:/).textContent).toContain("gem");
  });

  it("asks for the whole list once (limit=1000) with the slot's key, and never sends what is typed", async () => {
    const { fetch, calls } = routeFetch({ handlers: [catalogHandler()] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    await openAndType("gemini-x");
    await screen.findByText("Gemini X 3");
    const catalogCalls = calls.filter((c) => c.url.includes("/catalog"));
    expect(catalogCalls).toHaveLength(1);
    expect(catalogCalls[0].url).toContain("limit=1000");
    expect(catalogCalls[0].url).toContain(`credential_id=${CREDENTIAL.id}`);
    // V4-16 adds one `POST /pricing/quotes` per open, for the catalog's *own*
    // ids (fired once, from the just-fetched list) — it necessarily carries
    // ids like `google/gemini-x-0`, which is not "what was typed" (D-V4-25's
    // actual concern: the catalog/vendor-search calls must never see the
    // search text). Excluded here on that basis, not silenced.
    const searchableCalls = calls.filter((c) => !c.url.includes("/pricing/quotes"));
    expect(anyCallCarries(searchableCalls, "gemini-x")).toBe(false);
  });

  it("lists this workspace's custom models under “Your custom models”, each with its tested chip", async () => {
    const { fetch, calls } = routeFetch({
      handlers: [
        catalogHandler([]),
        (req) =>
          req.url.includes("/providers/openrouter-llm/models?")
            ? {
                body: {
                  items: [
                    record(),
                    record({ id: "rec-2", model_id: "acme/broken-1", last_test_ok: false, last_test_message: "model not found" }),
                  ],
                  total: 2,
                },
              }
            : undefined,
      ],
    });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    fireEvent.click(screen.getByRole("combobox"));
    await screen.findByText("Your custom models");
    const mine = group("Your custom models");
    expect(within(mine).getByText("acme/private-ft-7")).toBeTruthy();
    expect(within(mine).getByText(/Tested ✓/)).toBeTruthy();
    expect(within(mine).getByText("Test failed · model not found")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/providers/openrouter-llm/models?custom=true"))).toBe(true);
  });

  it("the vision toggle keeps catalog items whose meta says `image` input (flat or OpenRouter-nested)", async () => {
    const { fetch } = routeFetch({ handlers: [catalogHandler()] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    fireEvent.click(screen.getByRole("combobox"));
    await screen.findByText("Gemini X 0");
    fireEvent.click(screen.getByRole("button", { name: "Vision only" }));
    const catalog = group("Catalog");
    expect(within(catalog).getAllByText(/^Gemini X [0-3]$/)).toHaveLength(4);
    expect(within(catalog).getByText("Acme Seer")).toBeTruthy();
    expect(within(catalog).queryByText("Gemini X 7")).toBeNull();
    expect(within(catalog).queryByText(/Acme model/)).toBeNull();
  });
});

describe("ModelCombobox — the vendor search row (OpenRouter only)", () => {
  it("offers “Search OpenRouter for …” on openrouter-llm and sends search_vendor=true only when picked", async () => {
    const { fetch, calls } = routeFetch({
      handlers: [
        (req) =>
          req.url.includes("search_vendor=true")
            ? { body: catalogResponse([{ id: "vendor/found-9", label: "Found by the vendor" }]) }
            : undefined,
        catalogHandler([]),
      ],
    });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    await openAndType("found");
    const row = await screen.findByText(/Search OpenRouter for/);
    expect(calls.some((c) => c.url.includes("search_vendor"))).toBe(false);
    fireEvent.click(row);
    await screen.findByText("Found by the vendor");
    const search = calls.find((c) => c.url.includes("search_vendor=true"));
    expect(search?.url).toContain("q=found");
  });

  it("does not offer it on openai-llm", async () => {
    const { fetch } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness providerId="openai-llm" />);
    await openAndType("found");
    await screen.findByText(/Use custom model:/);
    expect(screen.queryByText(/Search OpenRouter/)).toBeNull();
    expect(openaiLlm.catalog?.adapter.startsWith("openrouter_")).toBe(false);
    expect(openrouterLlm.catalog?.adapter.startsWith("openrouter_")).toBe(true);
  });
});

describe("ModelCombobox — the model-id rule (R-V4-31, R-V4-32)", () => {
  it("typing an OpenRouter key disables the custom row with the reason, never shows the value, and makes zero requests", async () => {
    const { fetch, calls } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    fireEvent.click(screen.getByRole("combobox"));
    const input = await screen.findByPlaceholderText(/Search models or type an id/);
    // Let the opening requests (catalog, custom rows, key list) settle, then watch.
    await waitFor(() => expect(calls.some((c) => c.url.includes("custom=true"))).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    fetch.mockClear();

    fireEvent.change(input, { target: { value: OPENROUTER_KEY } });
    const row = await screen.findByText(/looks like an API key/);
    const item = row.closest("[cmdk-item]");
    expect(item?.getAttribute("aria-disabled")).toBe("true");
    expect(screen.queryByText(/Search OpenRouter/)).toBeNull();
    expect(screen.queryByText(/Use custom model:/)).toBeNull();

    // The value lives only in the input box: no rendered text repeats it (or a fragment of it).
    const rendered = document.body.textContent ?? "";
    expect(rendered).not.toContain(OPENROUTER_KEY);
    expect(rendered).not.toContain(OPENROUTER_KEY.slice(10, 20));

    fireEvent.keyDown(input, { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).not.toHaveBeenCalled();
    expect(anyCallCarries(calls, OPENROUTER_KEY)).toBe(false);
  });

  it("a 32-hex bare token is refused as a model id too", async () => {
    const { fetch } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness />);
    await openAndType("0123456789abcdef0123456789abcdef");
    const row = await screen.findByText(/looks like an API key/);
    expect(row.closest("[cmdk-item]")?.getAttribute("aria-disabled")).toBe("true");
  });

  it("picking a custom id shows the Custom badge in the trigger", async () => {
    const onChange = vi.fn();
    const { fetch } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    withClient(<ComboHarness onChange={onChange} />);
    await openAndType("acme/private-ft-7");
    fireEvent.click(await screen.findByText(/Use custom model:/));
    expect(onChange).toHaveBeenCalledWith("acme/private-ft-7");
    const trigger = screen.getByRole("combobox");
    expect(within(trigger).getByText("Custom")).toBeTruthy();
    expect(trigger.textContent).toContain("Custom model");
  });
});

describe("ProviderSlotEditor — custom ids", () => {
  function SlotHarness({ initial }: { initial: ProviderRef }) {
    const [value, setValue] = React.useState<ProviderRef | null>(initial);
    return <ProviderSlotEditor kind="llm" value={value} onChange={setValue} providers={REGISTRY} idPrefix="t-llm" />;
  }

  it("picking a custom id shows the Custom badge and the Untested chip", async () => {
    const { fetch } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    withClient(
      <SlotHarness initial={{ provider_id: "openrouter-llm", credential_id: CREDENTIAL.id, model: null, fields: {} }} />,
    );
    const trigger = screen.getByRole("combobox", { name: /model/i });
    fireEvent.click(trigger);
    fireEvent.change(await screen.findByPlaceholderText(/Search models or type an id/), { target: { value: "acme/new-1" } });
    fireEvent.click(await screen.findByText(/Use custom model:/));
    await waitFor(() => expect(within(screen.getByRole("combobox", { name: /model/i })).getByText("Custom")).toBeTruthy());
    expect((await screen.findByTestId("tested-chip")).textContent).toBe("Untested");
    expect(screen.getByRole("button", { name: /Test model/ })).toBeTruthy();
  });

  it("reads the model's record only for an id that passes the rule", async () => {
    const { fetch, calls } = routeFetch({ handlers: [catalogHandler([])] });
    vi.stubGlobal("fetch", fetch);
    const { unmount } = withClient(
      <SlotHarness initial={{ provider_id: "openrouter-llm", credential_id: CREDENTIAL.id, model: OPENROUTER_KEY, fields: {} }} />,
    );
    // The stored bad id is reported inline, value-free, and nothing is asked about it.
    expect(await screen.findByText(/Can't use this model id: it looks like an API key/)).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(calls.some((c) => /\/models\//.test(c.url))).toBe(false);
    expect(anyCallCarries(calls, OPENROUTER_KEY)).toBe(false);
    expect(screen.queryByRole("button", { name: /Test model/ })).toBeNull();
    unmount();

    withClient(
      <SlotHarness initial={{ provider_id: "openrouter-llm", credential_id: CREDENTIAL.id, model: "acme/private-ft-7", fields: {} }} />,
    );
    await waitFor(() => expect(calls.some((c) => c.url.includes("/providers/openrouter-llm/models/acme/private-ft-7"))).toBe(true));
  });
});
