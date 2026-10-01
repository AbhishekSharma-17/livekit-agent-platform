import {
  siAirtable,
  siAnthropic,
  siAsana,
  siAtlassian,
  siBitbucket,
  siBox,
  siBrave,
  siCaldotcom,
  siClaude,
  siCalendly,
  siClickup,
  siCloudflare,
  siConfluence,
  siDeepgram,
  siDiscord,
  siDropbox,
  siDuckduckgo,
  siElasticsearch,
  siElevenlabs,
  siEvernote,
  siFacebook,
  siFigma,
  siFishaudio,
  siGithub,
  siGitlab,
  siGmail,
  siGoogle,
  siGooglecalendar,
  siGoogledocs,
  siGoogledrive,
  siGooglegemini,
  siGooglemeet,
  siGooglesheets,
  siHubspot,
  siHuggingface,
  siInstagram,
  siIntercom,
  siJira,
  siLinear,
  siLivekit,
  siLoom,
  siMailchimp,
  siMailgun,
  siMeta,
  siMilvus,
  siMinimax,
  siMiro,
  siMistralai,
  siMongodb,
  siNaver,
  siNotion,
  siNvidia,
  siOllama,
  siOpenrouter,
  siPaypal,
  siPerplexity,
  siPostgresql,
  siQdrant,
  siQuickbooks,
  siReddit,
  siRedis,
  siSentry,
  siShopify,
  siStripe,
  siSupabase,
  siTelegram,
  siTodoist,
  siTrello,
  siTypeform,
  siWhatsapp,
  siXero,
  siYoutube,
  siZapier,
  siZendesk,
  siZoom,
  type SimpleIcon,
} from "simple-icons";

import {
  lhAssemblyai,
  lhAws,
  lhBaseten,
  lhCerebras,
  lhCohere,
  lhDeepinfra,
  lhDeepseek,
  lhExa,
  lhFal,
  lhFirecrawl,
  lhFireworks,
  lhGrok,
  lhGroq,
  lhHedra,
  lhMicrosoft,
  lhOpenai,
  lhQwen,
  lhRunway,
  lhSambanova,
  lhTavily,
  lhTogether,
  lhVoyage,
  lhXai,
  type LobehubMark,
} from "./lobehub-marks";

/** One mark, whichever source it came from: 24 x 24 path data drawn in `currentColor`. */
export interface VendorMarkIcon {
  slug: string;
  /** The brand's own name ("Google Sheets", "OpenAI"), for a label beside the mark. */
  title: string;
  source: "simple-icons" | "lobehub";
  paths: readonly { d: string; fillRule?: "evenodd" }[];
}

const si = (icon: SimpleIcon): VendorMarkIcon => ({
  slug: icon.slug,
  title: icon.title,
  source: "simple-icons",
  paths: [{ d: icon.path }],
});
const lh = (mark: LobehubMark): VendorMarkIcon => ({ slug: mark.slug, title: mark.title, source: "lobehub", paths: mark.paths });

/**
 * Third-party marks (docs/ui/DESIGN-SYSTEM.md section 5, "Third-party logos").
 *
 * One table from a vendor's normalised name to its official mark, from two
 * licence-clean sources, imported per icon so only these paths reach the
 * bundle:
 *
 * 1. **Simple Icons** (`simple-icons`, CC0), wherever it has the brand.
 * 2. **Lobe Icons** (`@lobehub/icons-static-svg`, MIT) for the AI vendors it
 *    lacks (OpenAI, xAI, Cerebras, Groq, Cohere …), copied into
 *    `lobehub-marks.ts` by `scripts/gen-lobehub-marks.mjs`.
 * 3. Otherwise the monogram in `VendorMark`. Never draw a mark by hand, and
 *    never borrow a different company's mark that shares the name (Simple
 *    Icons' "Rime" is an input method; its "X" is not xAI).
 *
 * Keys are `vendorKey(name)`: lower case, letters and digits only, so
 * "Mistral AI", "mistral-ai" and "mistralai" are one key.
 *
 * Kept out of `/s/[slug]`: only console screens import `VendorMark`
 * (`tests/flow-bundle-split.test.ts` holds the session graph to that).
 */
const SIMPLE_ICONS: Readonly<Record<string, SimpleIcon>> = {
  // Model, speech and inference providers
  anthropic: siAnthropic,
  claude: siClaude,
  google: siGoogle,
  googlecloud: siGoogle,
  googleworkspace: siGoogle,
  gemini: siGooglegemini,
  googlegemini: siGooglegemini,
  deepgram: siDeepgram,
  elevenlabs: siElevenlabs,
  openrouter: siOpenrouter,
  mistral: siMistralai,
  mistralai: siMistralai,
  livekit: siLivekit,
  livekitinference: siLivekit,
  livekitcloud: siLivekit,
  nvidia: siNvidia,
  perplexity: siPerplexity,
  meta: siMeta,
  metaai: siMeta,
  minimax: siMinimax,
  fishaudio: siFishaudio,
  naver: siNaver,
  clova: siNaver,
  huggingface: siHuggingface,
  ollama: siOllama,
  brave: siBrave,
  bravesearch: siBrave,
  // Vector stores and databases
  qdrant: siQdrant,
  milvus: siMilvus,
  postgres: siPostgresql,
  postgresql: siPostgresql,
  pgvector: siPostgresql,
  supabase: siSupabase,
  mongodb: siMongodb,
  redis: siRedis,
  elasticsearch: siElasticsearch,
  // Apps and MCP servers
  gmail: siGmail,
  googlesheets: siGooglesheets,
  googlecalendar: siGooglecalendar,
  googledrive: siGoogledrive,
  googledocs: siGoogledocs,
  googlemeet: siGooglemeet,
  airtable: siAirtable,
  calcom: siCaldotcom,
  calendly: siCalendly,
  linear: siLinear,
  jira: siJira,
  confluence: siConfluence,
  bitbucket: siBitbucket,
  atlassian: siAtlassian,
  loom: siLoom,
  zendesk: siZendesk,
  intercom: siIntercom,
  hubspot: siHubspot,
  notion: siNotion,
  github: siGithub,
  gitlab: siGitlab,
  sentry: siSentry,
  cloudflare: siCloudflare,
  zapier: siZapier,
  stripe: siStripe,
  paypal: siPaypal,
  shopify: siShopify,
  quickbooks: siQuickbooks,
  xero: siXero,
  asana: siAsana,
  trello: siTrello,
  clickup: siClickup,
  todoist: siTodoist,
  evernote: siEvernote,
  miro: siMiro,
  figma: siFigma,
  typeform: siTypeform,
  dropbox: siDropbox,
  box: siBox,
  mailchimp: siMailchimp,
  mailgun: siMailgun,
  zoom: siZoom,
  discord: siDiscord,
  whatsapp: siWhatsapp,
  telegram: siTelegram,
  youtube: siYoutube,
  reddit: siReddit,
  facebook: siFacebook,
  instagram: siInstagram,
  duckduckgo: siDuckduckgo,
};

/** Vendors Simple Icons lacks, from Lobe Icons. */
const LOBE_ICONS: Readonly<Record<string, LobehubMark>> = {
  openai: lhOpenai,
  xai: lhXai,
  grok: lhGrok,
  cerebras: lhCerebras,
  groq: lhGroq,
  assemblyai: lhAssemblyai,
  cohere: lhCohere,
  cohererank: lhCohere,
  voyage: lhVoyage,
  voyageai: lhVoyage,
  fireworks: lhFireworks,
  fireworksai: lhFireworks,
  together: lhTogether,
  togetherai: lhTogether,
  sambanova: lhSambanova,
  baseten: lhBaseten,
  deepinfra: lhDeepinfra,
  deepseek: lhDeepseek,
  qwen: lhQwen,
  hedra: lhHedra,
  runway: lhRunway,
  fal: lhFal,
  falai: lhFal,
  tavily: lhTavily,
  exa: lhExa,
  firecrawl: lhFirecrawl,
  amazon: lhAws,
  aws: lhAws,
  amazonwebservices: lhAws,
  microsoft: lhMicrosoft,
  azure: lhMicrosoft,
};

/** The merged table: Simple Icons first, Lobe Icons for the rest. */
export const VENDOR_MARKS: Readonly<Record<string, VendorMarkIcon>> = {
  ...Object.fromEntries(Object.entries(LOBE_ICONS).map(([key, mark]) => [key, lh(mark)])),
  ...Object.fromEntries(Object.entries(SIMPLE_ICONS).map(([key, icon]) => [key, si(icon)])),
};

/** "Mistral AI" / "mistral-ai" / "Cal.com" → "mistralai" / "mistralai" / "calcom". */
export function vendorKey(vendor: string): string {
  return vendor.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * The mark for a vendor label, slug or provider id, or `null` for the
 * monogram. A provider id falls back through its leading words
 * ("livekit-inference-stt" → "livekitinference"; "deepgram-flux-stt" →
 * "deepgram"), so a caller holding only an id still gets the vendor's mark.
 */
export function vendorMarkFor(vendor: string): VendorMarkIcon | null {
  const exact = VENDOR_MARKS[vendorKey(vendor)];
  if (exact) return exact;
  const words = vendor.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (let n = words.length - 1; n >= 1; n--) {
    const hit = VENDOR_MARKS[words.slice(0, n).join("")];
    if (hit) return hit;
  }
  return null;
}
