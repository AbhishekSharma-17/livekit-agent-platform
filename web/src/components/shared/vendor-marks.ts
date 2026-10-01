import {
  siAirtable,
  siAnthropic,
  siAsana,
  siAtlassian,
  siBitbucket,
  siBox,
  siBrave,
  siCaldotcom,
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

/**
 * Third-party marks (docs/ui/DESIGN-SYSTEM.md section 5, "Third-party logos").
 *
 * One table from a vendor's normalised name to its Simple Icons mark (CC0,
 * `simple-icons`, imported per icon so only these paths reach the bundle).
 * Keys are `vendorKey(name)`: lower case, letters and digits only, so
 * "Mistral AI", "mistral-ai" and "mistralai" are one key. A vendor that is not
 * here, or whose Simple Icons entry is a different company that happens to
 * share the name (the input method "Rime", "X" for xAI), falls back to the
 * monogram in `VendorMark`. Never draw a mark by hand.
 *
 * Kept out of `/s/[slug]`: only console screens import `VendorMark`
 * (`tests/flow-bundle-split.test.ts` holds the session graph to that).
 */
export const VENDOR_MARKS: Readonly<Record<string, SimpleIcon>> = {
  // Model, speech and inference providers
  anthropic: siAnthropic,
  claude: siAnthropic,
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
export function vendorMarkFor(vendor: string): SimpleIcon | null {
  const exact = VENDOR_MARKS[vendorKey(vendor)];
  if (exact) return exact;
  const words = vendor.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (let n = words.length - 1; n >= 1; n--) {
    const hit = VENDOR_MARKS[words.slice(0, n).join("")];
    if (hit) return hit;
  }
  return null;
}
