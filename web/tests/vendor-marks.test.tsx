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

/** Vendor labels from the provider registry (`contracts/generated/providers.json`) → expected mark slug. */
const REGISTRY: [string, string | null][] = [
  ["Anthropic", "anthropic"],
  ["Google", "google"],
  ["Deepgram", "deepgram"],
  ["ElevenLabs", "elevenlabs"],
  ["OpenRouter", "openrouter"],
  ["Mistral AI", "mistralai"],
  ["LiveKit", "livekit"],
  ["Qdrant", "qdrant"],
  ["NVIDIA", "nvidia"],
  ["Perplexity", "perplexity"],
  ["Meta", "meta"],
  ["MiniMax", "minimax"],
  ["Fish Audio", "fishaudio"],
  ["Naver", "naver"],
  ["Brave", "brave"],
  // No Simple Icons mark: monogram
  ["OpenAI", null],
  ["Pinecone", null],
  ["Weaviate", null],
  ["Cohere", null],
  ["Voyage AI", null],
  ["Ragie", null],
  ["Groq", null],
  ["Cartesia", null],
  ["AssemblyAI", null],
  ["Speechmatics", null],
  ["Inworld", null],
  ["Hume", null],
  ["Speechify", null],
  ["Amazon", null],
  ["Microsoft", null],
  ["Twilio", null],
  ["Telnyx", null],
  ["Composio", null],
  ["Tavily", null],
  ["Beyond Presence", null],
  ["Simli", null],
  ["Tavus", null],
  ["Hedra", null],
  ["Anam", null],
  ["D-ID", null],
  ["LiveAvatar", null],
  ["LemonSlice", null],
  ["LKAP", null],
  // Same name, different company in Simple Icons: never borrowed
  ["Rime", null],
  ["xAI", null],
];

/** Knowledge stores, apps and MCP servers the console shows. */
const SERVICES: [string, string | null][] = [
  ["Milvus", "milvus"],
  ["pgvector", "postgresql"],
  ["Postgres", "postgresql"],
  ["Chroma", null],
  ["Zilliz", null],
  ["Turbopuffer", null],
  ["Gmail", "gmail"],
  ["Google Sheets", "googlesheets"],
  ["googlecalendar", "googlecalendar"],
  ["Google Calendar", "googlecalendar"],
  ["Airtable", "airtable"],
  ["Cal.com", "caldotcom"],
  ["Linear", "linear"],
  ["Jira", "jira"],
  ["Zendesk", "zendesk"],
  ["Notion", "notion"],
  ["GitHub", "github"],
  ["HubSpot", "hubspot"],
  ["Stripe", "stripe"],
  ["Atlassian (Jira, Confluence, Bitbucket, Loom)", "atlassian"],
  ["Google Workspace", "google"],
  ["Outlook", null],
  ["Microsoft Outlook", null],
  ["Slack", null],
  ["Salesforce", null],
];

/** Provider ids, which some rows hold instead of a label. */
const PROVIDER_IDS: [string, string | null][] = [
  ["deepgram-stt", "deepgram"],
  ["deepgram-flux-stt", "deepgram"],
  ["livekit-inference-llm", "livekit"],
  ["livekit-inference", "livekit"],
  ["google-realtime", "google"],
  ["elevenlabs-tts", "elevenlabs"],
  ["mistral-llm", "mistralai"],
  ["openai-llm", null],
  ["xai-realtime", null],
  ["rime-tts", null],
  ["cohere-rerank", null],
];

describe("vendorMarkFor", () => {
  it.each([...REGISTRY, ...SERVICES, ...PROVIDER_IDS])("%s → %s", (vendor, slug) => {
    expect(vendorMarkFor(vendor)?.slug ?? null).toBe(slug);
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
      expect(icon.path.length, key).toBeGreaterThan(10);
    }
    expect(vendorKey("Mistral AI")).toBe("mistralai");
    expect(vendorKey("Cal.com")).toBe("calcom");
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
    expect(svg.querySelector("path")?.getAttribute("d")).toBe(VENDOR_MARKS.deepgram.path);
    expect(mark.className).toContain("text-foreground");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("names a standalone mark when labelled", () => {
    render(<VendorMark vendor="Google Calendar" labelled />);
    const mark = screen.getByRole("img", { name: "Google Calendar" });
    expect(mark.getAttribute("data-mark")).toBe("googlecalendar");
    expect(mark.getAttribute("title")).toBe("Google Calendar");
  });

  it("falls back to the monogram for a vendor with no mark", () => {
    const { container } = render(<VendorMark vendor="OpenAI" />);
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("monogram");
    expect(mark.textContent).toBe("OA");
    expect(mark.querySelector("svg")).toBeNull();
  });
});
