import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VendorMark } from "@/components/shared/vendor-mark";
import { MONOGRAM_ONLY, VENDOR_MARKS, vendorKey, vendorMarkFor } from "@/components/shared/vendor-marks";

/**
 * The third-party mark table (docs/ui/DESIGN-SYSTEM.md section 5 and
 * docs/ui/VENDOR-MARKS.md). Every vendor the console names resolves to its
 * Simple Icons mark, else its Lobe Icons mark, else the official mark from the
 * company's own site, else the monogram.
 */

type Source = "simple-icons" | "lobehub" | "official";
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
  // The company's own mark (scripts/vendor-marks-official/manifest.json)
  ["Pinecone", "pinecone", "official"],
  ["Weaviate", "weaviate", "official"],
  ["Ragie", "ragie", "official"],
  ["Cartesia", "cartesia", "official"],
  ["Speechmatics", "speechmatics", "official"],
  ["Inworld", "inworld", "official"],
  ["Hume", "hume", "official"],
  ["Speechify", "speechify", "official"],
  ["Composio", "composio", "official"],
  ["Beyond Presence", "beyondpresence", "official"],
  ["Tavus", "tavus", "official"],
  ["D-ID", "did", "official"],
  // Simple Icons' "Rime" is an input method, never borrowed. This is rime.ai's own wordmark.
  ["Rime", "rime", "official"],
  // Logo use needs a licence or written permission (docs/ui/VENDOR-MARKS.md), so the monogram
  ["Twilio", null],
  // No usable official mark (wordmark only, or the symbol only as a raster), so the monogram
  ["Telnyx", null],
  ["Simli", null],
  ["Anam", null],
  // Not looked up yet, so the monogram
  ["LiveAvatar", null],
  ["LemonSlice", null],
  ["Gladia", null],
  ["Soniox", null],
  ["LKAP", null],
];

/** Knowledge stores, apps and MCP servers the console shows. */
const SERVICES: Expected[] = [
  ["Milvus", "milvus", "simple-icons"],
  ["pgvector", "postgresql", "simple-icons"],
  ["Postgres", "postgresql", "simple-icons"],
  ["Together AI", "together", "lobehub"],
  ["SambaNova", "sambanova", "lobehub"],
  ["Zilliz", "zilliz", "official"],
  // The mark depends on colour or tone a one-ink tile cannot carry (docs/ui/VENDOR-MARKS.md)
  ["Chroma", null],
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
  // Logo use needs a licence or written permission, and Outlook never borrows the Microsoft mark
  ["Outlook", null],
  ["Microsoft Outlook", null],
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
  ["rime-tts", "rime", "official"],
  ["cartesia-tts", "cartesia", "official"],
  ["speechmatics-stt", "speechmatics", "official"],
  ["inworld-tts", "inworld", "official"],
  ["hume-tts", "hume", "official"],
  ["speechify-tts", "speechify", "official"],
  ["bey-avatar", "beyondpresence", "official"],
  ["tavus-avatar", "tavus", "official"],
  ["did-avatar", "did", "official"],
  ["twilio-sms", null],
  ["telnyx-sms", null],
  ["simli-avatar", null],
  ["anam-avatar", null],
  ["microsoft-outlook", null],
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

describe("monogram-only brands", () => {
  it.each([...MONOGRAM_ONLY])("%s never resolves to a mark, as a label or an id prefix", (key) => {
    expect(vendorMarkFor(key)).toBeNull();
    expect(vendorMarkFor(`${key}-integration`)).toBeNull();
  });
});

type Manifest = { marks: Record<string, { file: string; title: string; source: string; foundOn: string; guidelines: string; form: string; fetched: string; sha256: string }> };

describe("official marks", () => {
  const dir = path.resolve(__dirname, "../scripts/vendor-marks-official");
  const manifest = JSON.parse(readFileSync(path.join(dir, "manifest.json"), "utf8")) as Manifest;
  const officialInTable = new Set(
    Object.values(VENDOR_MARKS)
      .filter((icon) => icon.source === "official")
      .map((icon) => icon.slug),
  );

  it.each(Object.entries(manifest.marks))("%s records its source, guideline note, date and hash, and is mapped", (_name, entry) => {
    expect(entry.source).toMatch(/^https:\/\//);
    expect(entry.foundOn).toMatch(/^https:\/\//);
    expect(entry.guidelines.length).toBeGreaterThan(10);
    expect(entry.form.length).toBeGreaterThan(10);
    expect(entry.fetched).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(entry.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(officialInTable.has(entry.file.replace(/\.svg$/, "")), entry.file).toBe(true);
  });

  it("follows the copy rule in every manifest note (no em dashes)", () => {
    expect(readFileSync(path.join(dir, "manifest.json"), "utf8")).not.toContain("—");
  });
});

describe("parseOfficialIcon", () => {
  const ok = (body: string) =>
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><title>T</title>\n${body}\n</svg>\n`;
  const load = async () =>
    (await import("../scripts/gen-vendor-marks.mjs")) as { parseOfficialIcon: (svg: string, file: string) => { d: string; fillRule?: string }[] };

  it("reads plain paths and their fill rule", async () => {
    const { parseOfficialIcon } = await load();
    expect(parseOfficialIcon(ok('<path d="M0 0h24v24h-24z"/>\n<path fill-rule="evenodd" d="M1 1h2v2h-2z"/>'), "t.svg")).toEqual([
      { d: "M0 0h24v24h-24z" },
      { d: "M1 1h2v2h-2z", fillRule: "evenodd" },
    ]);
  });

  it.each([
    ["a script", ok('<script>alert(1)</script><path d="M0 0h24v24z"/>')],
    ["a link", ok('<a href="https://example.com"><path d="M0 0h24v24z"/></a>')],
    ["an embedded raster", ok('<image href="data:image/png;base64,AAAA"/>')],
    ["a style", ok('<path style="fill:red" d="M0 0h24v24z"/>')],
    ["a paint", ok('<path fill="#f00" d="M0 0h24v24z"/>')],
    ["a transform", ok('<path transform="scale(2)" d="M0 0h24v24z"/>')],
    ["an event handler", ok('<path onload="x()" d="M0 0h24v24z"/>')],
    ["an external reference", ok('<path d="M0 0h24v24z" fill-rule="url(#x)"/>')],
    ["another viewBox", ok('<path d="M0 0h24v24z"/>').replace("0 0 24 24", "0 0 48 48")],
    ["a coloured root", ok('<path d="M0 0h24v24z"/>').replace('fill="currentColor"', 'fill="#000"')],
  ])("rejects %s", async (_label, svg) => {
    const { parseOfficialIcon } = await load();
    expect(() => parseOfficialIcon(svg, "t.svg")).toThrow();
  });
});

describe("vendor-mark-data.ts", () => {
  it("matches simple-icons, @lobehub/icons-static-svg and scripts/vendor-marks-official (rerun scripts/gen-vendor-marks.mjs when they change)", async () => {
    const { render: generate } = (await import("../scripts/gen-vendor-marks.mjs")) as { render: () => Promise<string> };
    const current = readFileSync(path.resolve(__dirname, "../src/components/shared/vendor-mark-data.ts"), "utf8");
    expect(current).toBe(await generate());
  });

  it("is the only way marks reach the app: no source file imports the icon packages", () => {
    const shared = path.resolve(__dirname, "../src/components/shared");
    for (const file of ["vendor-marks.ts", "vendor-mark.tsx", "vendor-mark-data.ts"]) {
      const text = readFileSync(path.join(shared, file), "utf8");
      expect(text, file).not.toMatch(/(?:from|import)\s*\(?\s*"(?:simple-icons|@lobehub\/icons[^"]*)"/);
    }
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

  it("draws an official mark from the company's own site, every path, in currentColor", () => {
    const { container } = render(<VendorMark vendor="D-ID" />);
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("did");
    expect(mark.getAttribute("data-source")).toBe("official");
    expect(mark.querySelector("svg")!.getAttribute("fill")).toBe("currentColor");
    expect(mark.querySelectorAll("svg path").length).toBe(VENDOR_MARKS.did.paths.length);
  });

  it("falls back to the monogram for a vendor with no mark", () => {
    const { container } = render(<VendorMark vendor="Telnyx" />);
    const mark = container.querySelector('[data-slot="vendor-mark"]')!;
    expect(mark.getAttribute("data-mark")).toBe("monogram");
    expect(mark.textContent).toBe("Te");
    expect(mark.querySelector("svg")).toBeNull();
  });
});
