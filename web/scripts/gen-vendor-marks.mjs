#!/usr/bin/env node
/**
 * Copies the chosen third-party marks into `src/components/shared/vendor-mark-data.ts`
 * from three sources:
 *
 * - `simple-icons` (CC0, https://simpleicons.org), the first source;
 * - `@lobehub/icons-static-svg` (MIT, https://github.com/lobehub/lobe-icons), for
 *   the AI vendors Simple Icons lacks (its monochrome `currentColor` files only);
 * - `scripts/vendor-marks-official/`, for the vendors neither package has. Each
 *   file is the company's own mark, taken from its site or brand kit and
 *   normalised to a 24 x 24 `currentColor` file (or, for a brand that offers
 *   no one-colour version, a file with its own hex fills, marked
 *   `"ink": "colour"` in the manifest). `manifest.json` there records
 *   each file's source URL, brand guideline note, fetch date and hash, and
 *   docs/ui/VENDOR-MARKS.md holds the policy.
 *
 * Why a copy instead of a runtime import: `simple-icons`' entry module is
 * 5.2 MB and takes about half a second to parse, which every console test
 * and every `next dev` compile would pay, and the Lobe Icons package ships only
 * `.svg` files, which the console has no loader for. Copying just the chosen
 * icons' path data keeps both out of the app: only these paths reach the
 * bundle, and nothing parses the full packages at run time.
 *
 * Usage (from `web/`): `node scripts/gen-vendor-marks.mjs` writes the file,
 * `--check` fails when it no longer matches its sources
 * (`tests/vendor-marks.test.tsx` runs the same comparison).
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Export name → Simple Icons slug. Add a brand here (and to `vendor-marks.ts`), then rerun. */
export const SIMPLE_ICONS = {
  siAirtable: "airtable",
  siAnthropic: "anthropic",
  siAsana: "asana",
  siAtlassian: "atlassian",
  siBitbucket: "bitbucket",
  siBox: "box",
  siBrave: "brave",
  siCaldotcom: "caldotcom",
  siCalendly: "calendly",
  siClaude: "claude",
  siClickup: "clickup",
  siCline: "cline",
  siCloudflare: "cloudflare",
  siConfluence: "confluence",
  siCursor: "cursor",
  siDeepgram: "deepgram",
  siDiscord: "discord",
  siDropbox: "dropbox",
  siDuckduckgo: "duckduckgo",
  siElasticsearch: "elasticsearch",
  siElevenlabs: "elevenlabs",
  siEvernote: "evernote",
  siFacebook: "facebook",
  siFigma: "figma",
  siFishaudio: "fishaudio",
  siGithub: "github",
  siGithubcopilot: "githubcopilot",
  siGitlab: "gitlab",
  siGmail: "gmail",
  siGoogle: "google",
  siGooglecalendar: "googlecalendar",
  siGoogledocs: "googledocs",
  siGoogledrive: "googledrive",
  siGooglegemini: "googlegemini",
  siGooglemeet: "googlemeet",
  siGooglesheets: "googlesheets",
  siHubspot: "hubspot",
  siHuggingface: "huggingface",
  siInstagram: "instagram",
  siIntercom: "intercom",
  siJetbrains: "jetbrains",
  siJira: "jira",
  siLinear: "linear",
  siLivekit: "livekit",
  siLoom: "loom",
  siMailchimp: "mailchimp",
  siMailgun: "mailgun",
  siMeta: "meta",
  siMilvus: "milvus",
  siMinimax: "minimax",
  siMiro: "miro",
  siMistralai: "mistralai",
  siModelcontextprotocol: "modelcontextprotocol",
  siMongodb: "mongodb",
  siNaver: "naver",
  siNotion: "notion",
  siNvidia: "nvidia",
  siOllama: "ollama",
  siOpenrouter: "openrouter",
  siPaypal: "paypal",
  siPerplexity: "perplexity",
  siPostgresql: "postgresql",
  siQdrant: "qdrant",
  siQuickbooks: "quickbooks",
  siReddit: "reddit",
  siRedis: "redis",
  siSentry: "sentry",
  siShopify: "shopify",
  siStripe: "stripe",
  siSupabase: "supabase",
  siTelegram: "telegram",
  siTodoist: "todoist",
  siTrello: "trello",
  siTypeform: "typeform",
  siWhatsapp: "whatsapp",
  siWindsurf: "windsurf",
  siXero: "xero",
  siYoutube: "youtube",
  siZapier: "zapier",
  siZedindustries: "zedindustries",
  siZendesk: "zendesk",
  siZoom: "zoom",
};

/**
 * Export name → Lobe Icons file (without `.svg`) and the brand's name. The name is ours:
 * some upstream titles are wrong (xai.svg says "Grok", microsoft.svg says "Azure").
 */
export const LOBEHUB_ICONS = {
  lhAssemblyai: { file: "assemblyai", title: "AssemblyAI" },
  lhAws: { file: "aws", title: "AWS" },
  lhBaseten: { file: "baseten", title: "Baseten" },
  lhCerebras: { file: "cerebras", title: "Cerebras" },
  lhCodex: { file: "codex", title: "Codex" },
  lhCohere: { file: "cohere", title: "Cohere" },
  lhDeepinfra: { file: "deepinfra", title: "DeepInfra" },
  lhDeepseek: { file: "deepseek", title: "DeepSeek" },
  lhExa: { file: "exa", title: "Exa" },
  lhFal: { file: "fal", title: "fal" },
  lhFirecrawl: { file: "firecrawl", title: "Firecrawl" },
  lhFireworks: { file: "fireworks", title: "Fireworks AI" },
  lhGeminicli: { file: "geminicli", title: "Gemini CLI" },
  lhGoose: { file: "goose", title: "Goose" },
  lhGrok: { file: "grok", title: "Grok" },
  lhGroq: { file: "groq", title: "Groq" },
  lhHedra: { file: "hedra", title: "Hedra" },
  lhMicrosoft: { file: "microsoft", title: "Microsoft" },
  lhOpenai: { file: "openai", title: "OpenAI" },
  lhQwen: { file: "qwen", title: "Qwen" },
  lhRunway: { file: "runway", title: "Runway" },
  lhSambanova: { file: "sambanova", title: "SambaNova" },
  lhTavily: { file: "tavily", title: "Tavily" },
  lhTogether: { file: "together", title: "Together AI" },
  lhVoyage: { file: "voyage", title: "Voyage AI" },
  lhXai: { file: "xai", title: "xAI" },
};

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOBE_DIR = path.join(root, "node_modules/@lobehub/icons-static-svg/icons");
const OFFICIAL_DIR = path.join(root, "scripts/vendor-marks-official");

/**
 * Export name → official file and the brand's name, read from
 * `vendor-marks-official/manifest.json`. Add a brand there (the file and its
 * manifest entry, then a key in `vendor-marks.ts`) and rerun.
 */
export const OFFICIAL_MARKS = Object.fromEntries(
  Object.entries(JSON.parse(readFileSync(path.join(OFFICIAL_DIR, "manifest.json"), "utf8")).marks).map(
    ([name, { file, title, ink }]) => [name, ink === "colour" ? { file, title, ink } : { file, title }],
  ),
);
const OUT = path.join(root, "src/components/shared/vendor-mark-data.ts");

/** `{ d, fillRule? }[]` from one monochrome Lobe Icons SVG; throws on anything but plain paths. */
export function parseLobeIcon(svg, file) {
  if (!/viewBox="0 0 24 24"/.test(svg)) throw new Error(`${file}: expected a 24 x 24 viewBox`);
  if (!/<svg[^>]*fill="currentColor"/.test(svg)) throw new Error(`${file}: not a currentColor (monochrome) icon`);
  const body = svg.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "").replace(/<title>[^<]*<\/title>/, "");
  const leftover = body.replace(/<path\b[^>]*?(?:\/>|><\/path>)/g, "").trim();
  if (leftover) throw new Error(`${file}: unsupported markup ${leftover.slice(0, 60)}`);
  const rootRule = svg.match(/<svg[^>]*\sfill-rule="(\w+)"/)?.[1];
  const paths = [...body.matchAll(/<path\b([^>]*?)(?:\/>|><\/path>)/g)].map(([, attrs]) => {
    if (/\b(?:fill|opacity|fill-opacity|transform|style)=/.test(attrs.replace(/\bfill-rule=/, "")))
      throw new Error(`${file}: a path carries its own paint or transform`);
    const d = attrs.match(/\sd="([^"]+)"/)?.[1];
    if (!d) throw new Error(`${file}: a path without d`);
    const fillRule = attrs.match(/\sfill-rule="(\w+)"/)?.[1] ?? rootRule;
    return fillRule === "evenodd" ? { d, fillRule: "evenodd" } : { d };
  });
  if (paths.length === 0) throw new Error(`${file}: no paths`);
  return paths;
}

/**
 * `{ d, fillRule?, fill?, opacity? }[]` from one normalised official SVG.
 * Stricter than the Lobe parser because these files are collected by hand.
 *
 * - One ink (the default): the root may carry only `xmlns`,
 *   `viewBox="0 0 24 24"` and `fill="currentColor"`, then one `<title>` and
 *   `<path>` elements with nothing but `d` and `fill-rule`.
 * - `{ colour: true }`, for a brand that offers no one-colour version
 *   (manifest `"ink": "colour"`): the root carries only `xmlns` and the
 *   viewBox, and every path must also carry `fill="#rrggbb"`, plus an optional
 *   `opacity` between 0 and 1. Nothing else is unlocked.
 *
 * A script, link, image, style, gradient, transform or any other paint throws.
 */
export function parseOfficialIcon(svg, file, { colour = false } = {}) {
  const open = svg.match(/^\s*<svg\b([^>]*)>/);
  if (!open) throw new Error(`${file}: not an <svg> document`);
  const rootAttrs = [...open[1].matchAll(/\s([\w:-]+)="([^"]*)"/g)].map(([, k, v]) => `${k}=${v}`).sort();
  const expected = colour
    ? ["viewBox=0 0 24 24", "xmlns=http://www.w3.org/2000/svg"]
    : ["fill=currentColor", "viewBox=0 0 24 24", "xmlns=http://www.w3.org/2000/svg"];
  if (rootAttrs.join("|") !== expected.join("|")) throw new Error(`${file}: the root may carry only ${expected.join(", ")}`);
  if (/<script|<image|<foreignObject|<use\b|<style|<defs|Gradient|href=|url\(|style=|\son\w+=/i.test(svg))
    throw new Error(`${file}: unsafe markup`);
  const body = svg
    .slice(open[0].length)
    .replace(/<\/svg>\s*$/, "")
    .replace(/<title>[^<]*<\/title>/, "");
  const leftover = body.replace(/<path\b[^>]*?\/>/g, "").trim();
  if (leftover) throw new Error(`${file}: unsupported markup ${leftover.slice(0, 60)}`);
  const allowed = colour ? ["d", "fill-rule", "fill", "opacity"] : ["d", "fill-rule"];
  const paths = [...body.matchAll(/<path\b([^>]*?)\/>/g)].map(([, attrs]) => {
    const names = [...attrs.matchAll(/\s([\w:-]+)=/g)].map(([, k]) => k);
    if (names.some((k) => !allowed.includes(k))) throw new Error(`${file}: a path carries ${names.join(", ")}`);
    const d = attrs.match(/\sd="([^"]+)"/)?.[1];
    if (!d || !/^[MLHVCSQTAZmlhvcsqtaz0-9.,\s-]+$/.test(d)) throw new Error(`${file}: a path without plain path data`);
    const path = /\sfill-rule="evenodd"/.test(attrs) ? { d, fillRule: "evenodd" } : { d };
    if (!colour) return path;
    const fill = attrs.match(/\sfill="(#[0-9a-f]{6})"/)?.[1];
    if (!fill) throw new Error(`${file}: a colour mark's path needs fill="#rrggbb"`);
    const rawOpacity = attrs.match(/\sopacity="([^"]*)"/)?.[1];
    if (rawOpacity === undefined) return { ...path, fill };
    const opacity = Number(rawOpacity);
    if (!/^(?:0|1|0?\.\d+)$/.test(rawOpacity) || !(opacity >= 0 && opacity <= 1)) throw new Error(`${file}: opacity ${rawOpacity}`);
    return { ...path, fill, opacity };
  });
  if (paths.length === 0) throw new Error(`${file}: no paths`);
  return paths;
}

/** The generated module's text. */
export async function render() {
  const simpleIcons = await import("simple-icons");
  const bySlug = new Map(Object.values(simpleIcons).filter((icon) => icon && icon.slug).map((icon) => [icon.slug, icon]));
  const simple = Object.entries(SIMPLE_ICONS).map(([name, slug]) => {
    const icon = bySlug.get(slug);
    if (!icon) throw new Error(`simple-icons has no "${slug}"`);
    const mark = { slug, title: icon.title, source: "simple-icons", paths: [{ d: icon.path }] };
    return `export const ${name}: VendorMarkIcon = ${JSON.stringify(mark)};`;
  });
  const lobe = Object.entries(LOBEHUB_ICONS).map(([name, { file, title }]) => {
    const svg = readFileSync(path.join(LOBE_DIR, `${file}.svg`), "utf8");
    const mark = { slug: file, title, source: "lobehub", paths: parseLobeIcon(svg, `${file}.svg`) };
    return `export const ${name}: VendorMarkIcon = ${JSON.stringify(mark)};`;
  });
  const official = Object.entries(OFFICIAL_MARKS).map(([name, { file, title, ink }]) => {
    const svg = readFileSync(path.join(OFFICIAL_DIR, file), "utf8");
    const colour = ink === "colour";
    const slug = file.replace(/\.svg$/, "");
    const paths = parseOfficialIcon(svg, file, { colour });
    const mark = colour ? { slug, title, source: "official", ink: "colour", paths } : { slug, title, source: "official", paths };
    return `export const ${name}: VendorMarkIcon = ${JSON.stringify(mark)};`;
  });
  return `${[
    "// Generated by scripts/gen-vendor-marks.mjs. Do not edit by hand: change the lists in the",
    "// script (or scripts/vendor-marks-official/) and rerun it. Marks from simple-icons (CC0,",
    "// https://simpleicons.org), @lobehub/icons-static-svg (MIT, https://github.com/lobehub/lobe-icons)",
    "// and the vendors' own sites (scripts/vendor-marks-official/manifest.json, docs/ui/VENDOR-MARKS.md).",
    "",
    "/**",
    " * One mark, whichever source it came from: 24 x 24 path data drawn in `currentColor`, or, for a",
    ' * brand that offers no one-colour version (`ink: "colour"`), in its own per-path fills.',
    " */",
    "export interface VendorMarkIcon {",
    "  slug: string;",
    '  /** The brand\'s own name ("Google Sheets", "OpenAI"), for a label beside the mark. */',
    "  title: string;",
    '  source: "simple-icons" | "lobehub" | "official";',
    '  ink?: "colour";',
    '  paths: readonly { d: string; fillRule?: "evenodd"; fill?: string; opacity?: number }[];',
    "}",
    "",
    ...simple,
    "",
    ...lobe,
    "",
    ...official,
  ].join("\n")}\n`;
}

async function main() {
  const next = await render();
  if (process.argv.includes("--check")) {
    if (readFileSync(OUT, "utf8") !== next) {
      console.error("vendor-mark-data.ts is out of date: run `node scripts/gen-vendor-marks.mjs`");
      process.exit(1);
    }
    console.log("vendor-mark-data.ts matches its sources");
    return;
  }
  writeFileSync(OUT, next);
  const count = Object.keys(SIMPLE_ICONS).length + Object.keys(LOBEHUB_ICONS).length + Object.keys(OFFICIAL_MARKS).length;
  console.log(`wrote ${path.relative(root, OUT)} (${count} marks)`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
