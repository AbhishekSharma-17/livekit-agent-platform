import {
  lhAssemblyai,
  lhAws,
  lhBaseten,
  lhCerebras,
  lhCodex,
  lhCohere,
  lhDeepinfra,
  lhDeepseek,
  lhExa,
  lhFal,
  lhFirecrawl,
  lhFireworks,
  lhGeminicli,
  lhGoose,
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
  ofBeyondPresence,
  ofCartesia,
  ofComposio,
  ofDid,
  ofHume,
  ofInworld,
  ofOutlook,
  ofPinecone,
  ofRagie,
  ofRime,
  ofSalesforce,
  ofSlack,
  ofSpeechify,
  ofSpeechmatics,
  ofTavus,
  ofTwilio,
  ofWeaviate,
  ofZilliz,
  siAirtable,
  siAnthropic,
  siAsana,
  siAtlassian,
  siBitbucket,
  siBox,
  siBrave,
  siCaldotcom,
  siCalendly,
  siClaude,
  siClickup,
  siCline,
  siCloudflare,
  siConfluence,
  siCursor,
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
  siGithubcopilot,
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
  siJetbrains,
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
  siModelcontextprotocol,
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
  siWindsurf,
  siXero,
  siYoutube,
  siZapier,
  siZedindustries,
  siZendesk,
  siZoom,
  type VendorMarkIcon,
} from "./vendor-mark-data";

export type { VendorMarkIcon };

/**
 * Third-party marks (docs/ui/DESIGN-SYSTEM.md section 5, "Third-party logos").
 *
 * One table from a vendor's normalised name to its official mark, from three
 * sources. `scripts/gen-vendor-marks.mjs` copies each chosen icon's path data
 * into `vendor-mark-data.ts`, so only these paths reach the bundle and the
 * full packages are never parsed at run time:
 *
 * 1. **Simple Icons** (`simple-icons`, CC0), wherever it has the brand.
 * 2. **Lobe Icons** (`@lobehub/icons-static-svg`, MIT) for the AI vendors it
 *    lacks (OpenAI, xAI, Cerebras, Groq, Cohere …).
 * 3. **Official** marks (`scripts/vendor-marks-official/`), each taken from the
 *    company's own site or brand kit, for vendors neither package has.
 *    docs/ui/VENDOR-MARKS.md records every source and the usage policy.
 *    Slack, Twilio, Salesforce and Microsoft Outlook need a licence for logo
 *    use, and the workspace owner approved showing them to identify the
 *    service. Outlook is the one mark in its own colours, since Microsoft
 *    offers no one-colour version.
 * 4. Otherwise the monogram in `VendorMark`. Never draw a mark by hand, and
 *    never borrow a different company's mark that shares the name (Simple
 *    Icons' "Rime" is an input method, so Rime's mark is the official one from
 *    rime.ai; Simple Icons' "X" is not xAI).
 *
 * Keys are `vendorKey(name)`: lower case, letters and digits only, so
 * "Mistral AI", "mistral-ai" and "mistralai" are one key.
 *
 * Kept out of `/s/[slug]`: only console screens import `VendorMark`
 * (`tests/flow-bundle-split.test.ts` holds the session graph to that).
 */
const SIMPLE_ICONS: Readonly<Record<string, VendorMarkIcon>> = {
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
  // AI coding agents and MCP clients (Settings, AI agents). Keys cover the
  // console's client ids ("claude-code") and the products' MCP `clientInfo`
  // names, which the leading-word walk reaches ("cursor-vscode" → "cursor").
  // Claude Code and Claude Desktop show the Claude mark.
  claudecode: siClaude,
  claudedesktop: siClaude,
  claudeai: siClaude,
  cursor: siCursor,
  githubcopilot: siGithubcopilot,
  windsurf: siWindsurf,
  zed: siZedindustries,
  zedindustries: siZedindustries,
  cline: siCline,
  jetbrains: siJetbrains,
  mcp: siModelcontextprotocol,
  modelcontextprotocol: siModelcontextprotocol,
};

/** Vendors Simple Icons lacks, from Lobe Icons. */
const LOBE_ICONS: Readonly<Record<string, VendorMarkIcon>> = {
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
  // AI coding agents and MCP clients. ChatGPT's own app icon is the OpenAI
  // blossom, so it is the product's mark, not a borrowed parent mark.
  codex: lhCodex,
  codexcli: lhCodex,
  openaicodex: lhCodex,
  geminicli: lhGeminicli,
  goose: lhGoose,
  chatgpt: lhOpenai,
};

/**
 * Vendors neither package has, from each company's own site or brand kit
 * (`scripts/vendor-marks-official/manifest.json` records every source). Keys
 * cover each label, slug and provider-id stem the console shows ("bey-avatar"
 * resolves through "bey", "did-avatar" through "did").
 */
const OFFICIAL_MARKS: Readonly<Record<string, VendorMarkIcon>> = {
  // Knowledge stores
  pinecone: ofPinecone,
  weaviate: ofWeaviate,
  weaviatecloud: ofWeaviate,
  zilliz: ofZilliz,
  zillizcloud: ofZilliz,
  ragie: ofRagie,
  ragieai: ofRagie,
  // Speech
  cartesia: ofCartesia,
  cartesiaai: ofCartesia,
  speechmatics: ofSpeechmatics,
  inworld: ofInworld,
  inworldai: ofInworld,
  rime: ofRime,
  rimeai: ofRime,
  rimelabs: ofRime,
  hume: ofHume,
  humeai: ofHume,
  speechify: ofSpeechify,
  // Tools and apps
  composio: ofComposio,
  slack: ofSlack,
  salesforce: ofSalesforce,
  outlook: ofOutlook,
  microsoftoutlook: ofOutlook,
  // Telephony
  twilio: ofTwilio,
  // Avatars
  beyondpresence: ofBeyondPresence,
  bey: ofBeyondPresence,
  tavus: ofTavus,
  did: ofDid,
};

/** The merged table: Simple Icons first, then Lobe Icons, then the official marks. */
export const VENDOR_MARKS: Readonly<Record<string, VendorMarkIcon>> = { ...OFFICIAL_MARKS, ...LOBE_ICONS, ...SIMPLE_ICONS };

/**
 * Keys that always get the monogram, even where a leading word would match
 * another mark (docs/ui/VENDOR-MARKS.md). Visual Studio Code is pinned: its
 * brand page asks for the blue icon (white only on blue), with no recolouring
 * and no background, which a one-ink tile cannot honour, and Simple Icons
 * dropped the icon for the same reason. Outlook has its own mark, so
 * "microsoft-outlook" resolves to it by its exact key and never walks to the
 * Microsoft corporate mark.
 */
export const MONOGRAM_ONLY: ReadonlySet<string> = new Set<string>(["vscode", "visualstudiocode"]);

/** "Mistral AI" / "mistral-ai" / "Cal.com" → "mistralai" / "mistralai" / "calcom". */
export function vendorKey(vendor: string): string {
  return vendor.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * The mark for a vendor label, slug or provider id, or `null` for the
 * monogram. A provider id falls back through its leading words
 * ("livekit-inference-stt" → "livekitinference"; "deepgram-flux-stt" →
 * "deepgram"), so a caller holding only an id still gets the vendor's mark.
 * The walk stops at a `MONOGRAM_ONLY` key ("Microsoft Outlook" never falls
 * through to "microsoft").
 */
export function vendorMarkFor(vendor: string): VendorMarkIcon | null {
  const key = vendorKey(vendor);
  if (MONOGRAM_ONLY.has(key)) return null;
  const exact = VENDOR_MARKS[key];
  if (exact) return exact;
  const words = vendor.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (let n = words.length - 1; n >= 1; n--) {
    const prefix = words.slice(0, n).join("");
    if (MONOGRAM_ONLY.has(prefix)) return null;
    const hit = VENDOR_MARKS[prefix];
    if (hit) return hit;
  }
  return null;
}
