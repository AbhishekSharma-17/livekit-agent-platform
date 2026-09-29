import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { ProvidersSection } from "@/components/console/agents/providers-section/providers-section";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

import { REGISTRY } from "./provider-models";

/**
 * V6-33: every part leads with its job (the technical name stays as small text), the providers
 * section opens with a one-line summary of who does what, and the Cloud-versus-self-hosted
 * differences show where they apply. The provider list is the generated registry export, so the
 * summary reads the same flags the worker does.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
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

const ALL_PROVIDERS = REGISTRY.map(
  (p) => ({ ...p, v: 2, enabled: true, installed_on: ["conn-cloud", "conn-dgx"] }) as unknown as ProviderOut,
);

const CLOUD: ConnectionOut = {
  id: "conn-cloud",
  slug: "acme",
  name: "Acme Cloud",
  url: "wss://acme.livekit.cloud",
  deployment_type: "cloud",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
  capabilities: {
    inference_available: true,
    turn_detector_mode: "hosted",
    noise_cancellation_tier: "krisp",
    cloud_hosting: true,
    sip_enabled: true,
  },
};

const DGX: ConnectionOut = {
  id: "conn-dgx",
  slug: "dgx",
  name: "DGX-LivekitServer",
  url: "ws://dgx.internal:7880",
  deployment_type: "self_hosted",
  deployment_mode: "external",
  is_default: false,
  status: "ok",
  capabilities: {
    inference_available: false,
    turn_detector_mode: "local",
    noise_cancellation_tier: "none",
    cloud_hosting: false,
    sip_enabled: false,
  },
};

function ref(providerId: string, model: string | null = null) {
  return { provider_id: providerId, credential_id: null, model, fields: {} };
}

const FLUX_PIPELINE = {
  mode: "cascaded" as const,
  stt: ref("deepgram-flux-stt"),
  llm: ref("openrouter-llm", "openai/gpt-4.1-mini"),
  tts: ref("deepgram-tts"),
};

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown = { items: [], total: 0 };
      if (url.includes("/api/console/providers")) body = { v: 2, providers: ALL_PROVIDERS };
      else if (url.includes("/api/console/connections")) body = { items: [CLOUD, DGX], total: 2 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function Harness({ connectionId, pipeline }: { connectionId: string; pipeline: Partial<AgentEditorForm["config"]["pipeline"]> }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      connection_id: connectionId,
      config: {
        pipeline: {
          mode: "cascaded",
          stt: null,
          llm: null,
          tts: null,
          realtime: null,
          avatar_options: { participant_name: "Avatar", video_quality: null, idle_timeout_s: null, max_duration_s: null },
          ...pipeline,
        },
        capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: false },
      } as AgentEditorForm["config"],
    },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <ProvidersSection agent={{} as AgentOut} />
      </FormProvider>
    </QueryClientProvider>
  );
}

function headings(): string[] {
  const list = screen.getByRole("list", { name: "Pipeline parts" });
  return within(list)
    .getAllByRole("heading", { level: 3 })
    .map((h) => h.textContent ?? "");
}

/** The summary sentence under "How this agent works". */
async function summaryLine(): Promise<string> {
  const box = (await screen.findByText("How this agent works")).closest('[data-slot="pipeline-summary"]');
  if (!box) throw new Error("no summary");
  return box.querySelector("p")?.textContent ?? "";
}

describe("providers section: each part by its job", () => {
  it("leads each part with its job and keeps the technical name as small text", async () => {
    stubApi();
    const { container } = render(<Harness connectionId="conn-cloud" pipeline={FLUX_PIPELINE} />);
    await screen.findByText("How this agent works");

    expect(headings()).toEqual([
      "Listens: turns speech into text",
      "Thinks: understands, decides, calls tools, writes the reply",
      "Speaks: turns the reply into voice",
    ]);
    const names = Array.from(container.querySelectorAll('[data-slot="slot-technical-name"]')).map((el) => el.textContent);
    expect(names).toEqual(["Speech-to-text", "Language model", "Text-to-speech"]);
  });

  it("labels the optional and advanced parts by their jobs too", async () => {
    stubApi();
    const { container } = render(
      <Harness connectionId="conn-cloud" pipeline={{ ...FLUX_PIPELINE, avatar: ref("lemonslice-avatar") }} />,
    );
    await screen.findByText("How this agent works");
    expect(screen.getByRole("heading", { level: 3, name: "Gives the agent a face" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Advanced: hearing speech/ }));
    const advanced = Array.from(container.querySelectorAll('[data-slot="provider-slot-card"]'))
      .filter((el) => ["vad", "turn_detection", "noise_cancellation"].includes(el.getAttribute("data-kind") ?? ""))
      .map((el) => el.querySelector("h3")?.textContent);
    expect(advanced).toEqual([
      "Decides when the caller has finished",
      "Decides when the caller has finished",
      "Filters background noise",
    ]);

    fireEvent.click(screen.getByRole("button", { name: /Add image generation/ }));
    fireEvent.click(screen.getByRole("button", { name: /workflow model/ }));
    expect(screen.getByRole("heading", { level: 3, name: "Draws pictures" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Background helper: capturing details, summaries" })).toBeTruthy();
  });

  it("labels a realtime agent's one part with its job, and says the model decides", async () => {
    stubApi();
    render(<Harness connectionId="conn-cloud" pipeline={{ mode: "realtime", realtime: ref("openai-realtime") }} />);
    expect(await summaryLine()).toBe(
      "GPT Realtime listens, thinks and speaks, and decides when you've finished · runs on Acme Cloud (LiveKit Cloud)",
    );
    expect(headings()).toEqual(["Listens, thinks and speaks in one model"]);
  });
});

describe("providers section: the pipeline summary", () => {
  it("summarises a Flux agent on Cloud: the listener ends the turns", async () => {
    stubApi();
    render(<Harness connectionId="conn-cloud" pipeline={FLUX_PIPELINE} />);
    expect(await summaryLine()).toBe(
      "Deepgram Flux listens and decides when you've finished · GPT-4.1 mini thinks · Deepgram Aura speaks · runs on Acme Cloud (LiveKit Cloud)",
    );
  });

  it("summarises the same agent on the DGX and lists what that connection lacks", async () => {
    stubApi();
    render(<Harness connectionId="conn-dgx" pipeline={FLUX_PIPELINE} />);
    expect(await summaryLine()).toBe(
      "Deepgram Flux listens and decides when you've finished · GPT-4.1 mini thinks · Deepgram Aura speaks · runs on DGX-LivekitServer (self-hosted)",
    );
    expect(screen.getByText("What LiveKit Cloud has that this connection doesn't")).toBeTruthy();
    expect(screen.getByText(/Cloud only\. Here each part needs its own provider key\./)).toBeTruthy();
    expect(screen.getByText(/LiveKit hosts it on Cloud only; here it runs inside the agent's worker\./)).toBeTruthy();
    expect(screen.getByText(/LiveKit's own noise filter is Cloud only\./)).toBeTruthy();
    expect(screen.getByText(/Phone calls use LiveKit Cloud telephony/)).toBeTruthy();
  });

  it("keeps the connection notes shut on a Cloud connection that has everything", async () => {
    stubApi();
    render(<Harness connectionId="conn-cloud" pipeline={FLUX_PIPELINE} />);
    await summaryLine();
    expect(screen.getByText("What this connection has")).toBeTruthy();
    expect(screen.queryByText(/Here each part needs its own provider key/)).toBeNull();
  });

  it("gives a Nova-3 agent on the DGX LiveKit's detector inside the worker", async () => {
    stubApi();
    render(<Harness connectionId="conn-dgx" pipeline={{ ...FLUX_PIPELINE, stt: ref("deepgram-stt") }} />);
    const line = await summaryLine();
    expect(line).toContain("Deepgram listens · LiveKit's turn detector (runs inside the agent's worker) decides when you've finished");
    expect(line).not.toContain("listens and decides");
  });

  it("gives a Nova-3 agent on Cloud the detector hosted by LiveKit", async () => {
    stubApi();
    render(<Harness connectionId="conn-cloud" pipeline={{ ...FLUX_PIPELINE, stt: ref("deepgram-stt") }} />);
    expect(await summaryLine()).toContain(
      "Deepgram listens · LiveKit's turn detector (hosted by LiveKit) decides when you've finished",
    );
  });

  it("names an explicit turn-detection part ahead of the transcriber", async () => {
    stubApi();
    render(
      <Harness connectionId="conn-cloud" pipeline={{ ...FLUX_PIPELINE, turn_detection: ref("inference-turn-detector") }} />,
    );
    expect(await summaryLine()).toContain(
      "Deepgram Flux listens · LiveKit's turn detector (hosted by LiveKit) decides when you've finished",
    );
  });

  it("says on the turn-detection part what leaving it unset does", async () => {
    stubApi();
    render(<Harness connectionId="conn-dgx" pipeline={FLUX_PIPELINE} />);
    await summaryLine();
    fireEvent.click(screen.getByRole("button", { name: /Advanced: hearing speech/ }));
    expect(
      await screen.findByText("If you leave this unset: Deepgram Flux decides when the caller has finished, as part of listening."),
    ).toBeTruthy();
  });
});

describe("providers section: Cloud versus self-hosted notes on a part", () => {
  it("notes on an avatar and on noise cancellation what a self-hosted connection needs", async () => {
    stubApi();
    render(<Harness connectionId="conn-dgx" pipeline={{ ...FLUX_PIPELINE, avatar: ref("lemonslice-avatar") }} />);
    await summaryLine();
    expect(screen.getByText(/needs a room that is reachable from the internet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Advanced: hearing speech/ }));
    expect(await screen.findByText(/LiveKit's own noise filter works on LiveKit Cloud only/)).toBeTruthy();
  });

  it("keeps those notes off a Cloud connection", async () => {
    stubApi();
    render(<Harness connectionId="conn-cloud" pipeline={{ ...FLUX_PIPELINE, avatar: ref("lemonslice-avatar") }} />);
    await summaryLine();
    fireEvent.click(screen.getByRole("button", { name: /Advanced: hearing speech/ }));
    expect(screen.queryByText(/reachable from the internet/)).toBeNull();
    expect(screen.queryByText(/noise filter works on LiveKit Cloud only/)).toBeNull();
  });
});
