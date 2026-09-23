import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { ProvidersSection } from "@/components/console/agents/providers-section/providers-section";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

/**
 * The providers section (V2-13, replacing WP-4's `ProvidersTab`), covering
 * two of the card's acceptance lines:
 *   - "half-cascade card is disabled with a reason when the realtime
 *     provider lacks text_modality";
 *   - "a provider not installed on the bound connection renders disabled
 *     with the 'not installed on <connection>' hint".
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

const CONNECTION_A: ConnectionOut = {
  id: "conn-a",
  slug: "cloud-a",
  name: "cloud-a",
  url: "wss://cloud-a.livekit.cloud",
  deployment_type: "cloud",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
  capabilities: { inference_available: true },
};

function ref(providerId: string) {
  return { provider_id: providerId, credential_id: null, model: null, fields: {} };
}

const STT_PROVIDER: ProviderOut = {
  v: 2,
  id: "deepgram-stt",
  kind: "stt",
  label: "Deepgram",
  vendor: "Deepgram",
  status: "mvp",
  availability: "available",
  worker_image: "slim",
  enabled: true,
  installed_on: [], // not installed on conn-a
  package: "livekit-plugins-deepgram",
  python_class: "livekit.plugins.deepgram.STT",
  requires_credential: true,
  secret_fields: [],
  fields: [],
  models: [],
  default_model: null,
  capabilities: {},
};

const REALTIME_TEXT_ONLY: ProviderOut = {
  v: 2,
  id: "google-realtime",
  kind: "realtime",
  label: "Google Realtime",
  vendor: "Google",
  status: "mvp",
  availability: "available",
  worker_image: "slim",
  enabled: true,
  installed_on: ["conn-a"],
  package: "livekit-plugins-google",
  python_class: "livekit.plugins.google.realtime.RealtimeModel",
  requires_credential: false,
  secret_fields: [],
  fields: [],
  models: [],
  default_model: null,
  capabilities: { text_modality: true, video_input: true },
};

const REALTIME_NO_TEXT: ProviderOut = {
  ...REALTIME_TEXT_ONLY,
  id: "openai-realtime",
  label: "OpenAI Realtime",
  vendor: "OpenAI",
  capabilities: { text_modality: false },
};

const LLM_PROVIDER: ProviderOut = {
  v: 2,
  id: "livekit-inference-llm",
  kind: "llm",
  label: "LiveKit Inference LLM",
  vendor: "LiveKit",
  status: "mvp",
  availability: "available",
  worker_image: "slim",
  enabled: true,
  installed_on: ["conn-a"],
  package: "livekit-plugins-inference",
  python_class: "livekit.plugins.inference.LLM",
  requires_credential: false,
  secret_fields: [],
  fields: [],
  models: [],
  default_model: null,
  capabilities: {},
};

const TTS_PROVIDER: ProviderOut = {
  ...LLM_PROVIDER,
  id: "livekit-inference-tts",
  kind: "tts",
  label: "LiveKit Inference TTS",
};

function stubApi(providers: ProviderOut[], connections: ConnectionOut[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown = { items: [], total: 0 };
      if (url.includes("/api/console/providers")) body = { v: 2, providers };
      else if (url.includes("/api/console/connections")) body = { items: connections, total: connections.length };
      else if (url.includes("/api/console/credentials")) body = { items: [], total: 0 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function Harness({ connectionId, pipeline }: { connectionId: string | null; pipeline: Partial<AgentEditorForm["config"]["pipeline"]> }) {
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

describe("ProvidersSection", () => {
  it("disables the half-cascade mode card with a reason when no realtime provider on the connection has text_modality", async () => {
    stubApi([REALTIME_NO_TEXT, TTS_PROVIDER], [CONNECTION_A]);
    render(<Harness connectionId="conn-a" pipeline={{ mode: "cascaded" }} />);

    const halfCascadeLabel = await screen.findByText("Half-cascade");
    const card = halfCascadeLabel.closest("label")!;
    const radio = within(card).getByRole("radio", { hidden: true }) as HTMLInputElement;
    expect(radio.disabled).toBe(true);
    expect(within(card).getByText(/needs one/)).toBeTruthy();
  });

  it("enables half-cascade once a text_modality realtime provider is installed on the connection", async () => {
    stubApi([REALTIME_TEXT_ONLY, TTS_PROVIDER], [CONNECTION_A]);
    render(<Harness connectionId="conn-a" pipeline={{ mode: "cascaded" }} />);

    const halfCascadeLabel = await screen.findByText("Half-cascade");
    const card = halfCascadeLabel.closest("label")!;
    const radio = within(card).getByRole("radio", { hidden: true }) as HTMLInputElement;
    expect(radio.disabled).toBe(false);
  });

  it("renders a provider not installed on the bound connection disabled, with a 'not installed on <connection>' hint", async () => {
    stubApi([STT_PROVIDER, LLM_PROVIDER, TTS_PROVIDER], [CONNECTION_A]);
    render(<Harness connectionId="conn-a" pipeline={{ mode: "cascaded", llm: ref("livekit-inference-llm"), tts: ref("livekit-inference-tts") }} />);

    fireEvent.click(await screen.findByRole("button", { name: /Edit speech-to-text/i }));
    const sttCard = screen.getByRole("region", { name: "Speech-to-text" });
    // No LiveKit Inference STT in this fixture, so the vendor list (and its
    // "More providers" disclosure) shows directly — no "Run it with" step.
    const more = await within(sttCard).findByText(/More providers/);
    fireEvent.click(more);
    expect(within(sttCard).getByText(/not installed on/i)).toBeTruthy();
    expect(within(sttCard).getByText(/cloud-a/)).toBeTruthy();
    // The unavailable provider is listed, never offered as a selectable radio.
    expect(within(sttCard).queryByRole("radio", { name: /Deepgram/ })).toBeNull();
  });
});
