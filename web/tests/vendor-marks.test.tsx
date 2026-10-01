import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VendorMark } from "@/components/shared/vendor-mark";
import { VENDOR_MARKS, vendorKey, vendorMarkFor } from "@/components/shared/vendor-marks";

/**
 * The third-party mark table (docs/ui/DESIGN-SYSTEM.md section 5): every
 * vendor the console names resolves to its Simple Icons mark or, where
 * Simple Icons has none (or only another company's), to the monogram.
 */

type Source = "simple-icons" | "lobehub";
type Expected = [vendor: string, slug: string | null, source?: Source];

/** Every vendor label in the provider registry (`contracts/generated/providers.json`) → expected mark. */
const REGISTRY: Expected[] = [
  // Simple Icons first
  ["Anthropic", "anthropic", "simple-icons"],
  ["Google", "google", "simple-icons"],
  ["Deepgram", "deepgram", "simple-icons"],
  ["ElevenLabs", "elevenlabs", "simple-icons"],
  ["OpenRouter", "openrouter", "simple-icons"],
  ["Mistral AI", "mistralai", "simple-icons"],
  ["LiveKit", "livekit", "simple-icons"],
  ["Qdrant", "qdrant", "simple-icons"],
  ["NVIDIA", "nvidia", "simple-icons"],
  ["Perplexity", "perplexity", "simple-icons"],
  ["Meta", "meta", "simple-icons"],
  ["MiniMax", "minimax", "simple-icons"],
  ["Fish Audio", "fishaudio", "simple-icons"],
  ["Naver", "naver", "simple-icons"],
  ["Brave", "brave", "simple-icons"],
  // Lobe Icons for the AI vendors Simple Icons lacks
  ["OpenAI", "openai", "lobehub"],
  ["xAI", "xai", "lobehub"],
  ["Cerebras", "cerebras", "lobehub"],
  ["Groq", "groq", "lobehub"],
  ["AssemblyAI", "assemblyai", "lobehub"],
  ["Cohere", "cohere", "lobehub"],
  ["Voyage AI", "voyage", "lobehub"],
  ["Baseten", "baseten", "lobehub"],
  ["Fireworks", "fireworks", "lobehub"],
  ["Fal", "fal", "lobehub"],
  ["Runway", "runway", "lobehub"],
  ["Hedra", "hedra", "lobehub"],
  ["Tavily", "tavily", "lobehub"],
  ["Amazon", "aws", "lobehub"],
  ["Microsoft", "microsoft", "lobehub"],
  // Neither source has an official mark: monogram
  ["Pinecone", null],
  ["Weaviate", null],
  ["Ragie", null],
  ["Cartesia", null],
  ["Speechmatics", null],
  ["Inworld", null],
  ["Hume", null],
  ["Speechify", null],
  ["Twilio", null],
  ["Telnyx", null],
  ["Composio", null],
  ["Beyond Presence", null],
  ["Simli", null],
  ["Tavus", null],
  ["Anam", null],
  ["D-ID", null],
  ["LiveAvatar", null],
  ["LemonSlice", null],
  ["Gladia", null],
  ["Soniox", null],
  ["LKAP", null],
  // Same name, different company in Simple Icons: never borrowed
  ["Rime", null],
];

/** Knowledge stores, apps and MCP servers the console shows. */
const SERVICES: Expected[] = [
  ["Milvus", "milvus", "simple-icons"],
  ["pgvector", "postgresql", "simple-icons"],
  ["Postgres", "postgresql", "simple-icons"],
  ["Together AI", "together", "lobehub"],
  ["SambaNova", "sambanova", "lobehub"],
  ["Chroma", null],
  ["Zilliz", null],
  ["Turbopuffer", null],
  ["Gmail", "gmail", "simple-icons"],
  ["Google Sheets", "googlesheets", "simple-icons"],
  ["googlecalendar", "googlecalendar", "simple-icons"],
  ["Google Calendar", "googlecalendar", "simple-icons"],
  ["Gemini", "googlegemini", "simple-icons"],
  ["Airtable", "airtable", "simple-icons"],
  ["Cal.com", "caldotcom", "simple-icons"],
  ["Linear", "linear", "simple-icons"],
  ["Jira", "jira", "simple-icons"],
  ["Zendesk", "zendesk", "simple-icons"],
  ["Notion", "notion", "simple-icons"],
  ["GitHub", "github", "simple-icons"],
  ["HubSpot", "hubspot", "simple-icons"],
  ["Stripe", "stripe", "simple-icons"],
  ["Atlassian (Jira, Confluence, Bitbucket, Loom)", "atlassian", "simple-icons"],
  ["Google Workspace", "google", "simple-icons"],
  ["Outlook", null],
  ["Microsoft Outlook", "microsoft", "lobehub"],
  ["Slack", null],
  ["Salesforce", null],
];

/** Provider ids, which some rows hold instead of a label. */
const PROVIDER_IDS: Expected[] = [
  ["deepgram-stt", "deepgram", "simple-icons"],
  ["deepgram-flux-stt", "deepgram", "simple-icons"],
  ["livekit-inference-llm", "livekit", "simple-icons"],
  ["livekit-inference", "livekit", "simple-icons"],
  ["google-realtime", "google", "simple-icons"],
  ["elevenlabs-tts", "elevenlabs", "simple-icons"],
  ["mistral-llm", "mistralai", "simple-icons"],
  ["openai-llm", "openai", "lobehub"],
  ["xai-realtime", "xai", "lobehub"],
  ["aws-polly-tts", "aws", "lobehub"],
  ["azure-tts", "microsoft", "lobehub"],
  ["cohere-rerank", "cohere", "lobehub"],
  ["rime-tts", null],
];

describe("vendorMarkFor", () => {
  it.each([...REGISTRY, ...SERVICES, ...PROVIDER_IDS])("%s → %s (%s)", (vendor, slug, source) => {
    const mark = vendorMarkFor(vendor);
    expect(mark?.slug ?? null).toBe(slug);
    expect(mark?.source).toBe(source);
  });

  it("covers every registry vendor with either a mark or the monogram, on purpose", () => {
    const registry = JSON.parse(readFileSync(path.resolve(__dirname, "../../contracts/generated/providers.json"), "utf8")) as
      | { providers: { vendor: string }[] }
      | { vendor: string }[];
    const vendors = new Set((Array.isArray(registry) ? registry : registry.providers).map((p) => p.vendor));
    const listed = new Set(REGISTRY.map(([vendor]) => vendor));
    const marked = [...vendors].filter((vendor) => vendorMarkFor(vendor));
    // Every vendor that gets a mark is one this test names, so a new mapping is a reviewed change.
    expect(marked.filter((vendor) => !listed.has(vendor))).toEqual([]);
  });

  it("keys the table by the normalised name, and every entry has a path", () => {
    for (const [key, icon] of Object.entries(VENDOR_MARKS)) {
      expect(vendorKey(key), key).toBe(key);
      expect(icon.paths.length, key).toBeGreaterThan(0);
      for (const path of icon.paths) expect(path.d.length, key).toBeGreaterThan(10);
    }
    expect(vendorKey("Mistral AI")).toBe("mistralai");
    expect(vendorKey("Cal.com")).toBe("calcom");
  });
});

describe("lobehub-marks.ts", () => {
  it("matches @lobehub/icons-static-svg (rerun scripts/gen-lobehub-marks.mjs when it changes)", async () => {
    const { render: generate } = (await import("../scripts/gen-lobehub-marks.mjs")) as { render: () => string };
    const current = readFileSync(path.resolve(__dirname, "../src/components/shared/lobehub-marks.ts"), "utf8");
    expect(current).toBe(generate());
  });
});

describe("VendorMark", () => {
  it("draws a known vendor's real mark in currentColor, decorative beside its name", () => {
    const { container } = render(
      <span>
        <VendorMark vendor="Deepgram" />
        Deepgram
      </span>,
    );
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("deepgram");
    expect(mark.getAttribute("aria-hidden")).toBe("true");
    const svg = mark.querySelector("svg")!;
    expect(svg.getAttribute("fill")).toBe("currentColor");
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.querySelector("path")?.getAttribute("d")).toBe(VENDOR_MARKS.deepgram.paths[0].d);
    expect(mark.className).toContain("text-foreground");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("names a standalone mark when labelled", () => {
    render(<VendorMark vendor="Google Calendar" labelled />);
    const mark = screen.getByRole("img", { name: "Google Calendar" });
    expect(mark.getAttribute("data-mark")).toBe("googlecalendar");
    expect(mark.getAttribute("title")).toBe("Google Calendar");
  });

  it("draws a Lobe Icons mark, every path, with its fill rule", () => {
    const { container } = render(<VendorMark vendor="Cerebras" />);
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("cerebras");
    expect(mark.getAttribute("data-source")).toBe("lobehub");
    const paths = mark.querySelectorAll("svg path");
    expect(paths.length).toBe(VENDOR_MARKS.cerebras.paths.length);
    expect(paths[0].getAttribute("fill-rule")).toBe("evenodd");
  });

  it("falls back to the monogram for a vendor with no mark", () => {
    const { container } = render(<VendorMark vendor="Pinecone" />);
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("monogram");
    expect(mark.textContent).toBe("Pi");
    expect(mark.querySelector("svg")).toBeNull();
  });
});
