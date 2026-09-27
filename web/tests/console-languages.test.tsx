import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { LanguagesCard } from "@/components/console/agents/editor/sections/languages-card";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, ProvidersResponse } from "@/contracts/lkap-contracts";

/**
 * V5-35 acceptance (`docs/v5/PLAN-V5.md`): the Languages card posts
 * `voice.languages`, `auto_detect` and `voices_by_language`. `voice.language`
 * (the Instructions tab's single-language field) is kept in lockstep with
 * `languages[0]` — the worker reads `languages` first, and the two fields
 * must never disagree (ask #203(3)).
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  Element.prototype.scrollIntoView = function scrollIntoView() {};
  latest = null;
});
afterEach(() => {
  vi.unstubAllGlobals();
  Element.prototype.matches = nativeMatches;
  Element.prototype.scrollIntoView = nativeScrollIntoView;
});

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const PROVIDERS: ProvidersResponse = {
  providers: [
    {
      id: "vendor-tts",
      kind: "tts",
      label: "Vendor Voice",
      vendor: "Vendor",
      package: "vendor-tts",
      python_class: "vendor.tts.TTS",
      requires_credential: false,
    },
  ],
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/providers")) return jsonResponse(PROVIDERS);
      return jsonResponse({});
    }),
  );
}

function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Claims",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    config: { instructions: "Hi there", pipeline: { mode: "cascaded" } },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;

function Harness({ agent: theAgent }: { agent: AgentOut }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <form>
          <LanguagesCard />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("LanguagesCard", () => {
  it("says there's just the one language until another is added", () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    expect(screen.getByText(/Just the agent's one language/)).toBeTruthy();
    expect((screen.getByRole("switch", { name: /Detect the caller's language/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("adding a language sets voice.languages and keeps voice.language as languages[0]", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    fireEvent.click(screen.getByRole("combobox", { name: "Add a language" }));
    fireEvent.click(screen.getByRole("option", { name: "Hindi" }));

    await waitFor(() => expect(latest?.config.voice.languages).toEqual(["hi"]));
    expect(latest?.config.voice.language).toBe("hi");
    expect(screen.getByText("Hindi")).toBeTruthy();
    expect(screen.getByText("Default")).toBeTruthy();
  });

  it("adding a second language enables auto-detect, and 'Make default' reorders (and re-syncs voice.language)", async () => {
    stubFetch();
    const withHindi = agent({
      config: { instructions: "Hi", pipeline: { mode: "cascaded" }, voice: { language: "hi", languages: ["hi"] } },
    });
    render(<Harness agent={withHindi} />);

    fireEvent.click(screen.getByRole("combobox", { name: "Add a language" }));
    fireEvent.click(screen.getByRole("option", { name: "English" }));
    await waitFor(() => expect(latest?.config.voice.languages).toEqual(["hi", "en"]));

    const autoDetect = screen.getByRole("switch", { name: /Detect the caller's language/ }) as HTMLButtonElement;
    expect(autoDetect.disabled).toBe(false);
    fireEvent.click(autoDetect);
    await waitFor(() => expect(latest?.config.voice.auto_detect).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Make default" }));
    await waitFor(() => expect(latest?.config.voice.languages).toEqual(["en", "hi"]));
    expect(latest?.config.voice.language).toBe("en");
  });

  it("removing a language updates the list and, when it was the default, re-syncs voice.language", async () => {
    stubFetch();
    const withTwo = agent({
      config: {
        instructions: "Hi",
        pipeline: { mode: "cascaded" },
        voice: { language: "hi", languages: ["hi", "en"] },
      },
    });
    render(<Harness agent={withTwo} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove Hindi" }));
    await waitFor(() => expect(latest?.config.voice.languages).toEqual(["en"]));
    expect(latest?.config.voice.language).toBe("en");
  });

  it("sets a language's voice through the existing slot editor, in a dialog", async () => {
    stubFetch();
    const withHindi = agent({
      config: { instructions: "Hi", pipeline: { mode: "cascaded" }, voice: { language: "hi", languages: ["hi"] } },
    });
    render(<Harness agent={withHindi} />);

    fireEvent.click(screen.getByRole("button", { name: "Voice" }));
    const dialog = await screen.findByRole("dialog", { name: /Voice for Hindi/ });
    fireEvent.click(await within(dialog).findByRole("radio", { name: /Vendor Voice/ }));

    await waitFor(() => expect(latest?.config.voice.voices_by_language?.hi?.provider_id).toBe("vendor-tts"));

    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Voice: vendor-tts")).toBeTruthy();
  });
});
