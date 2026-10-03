# Vendor marks

How the console shows a third-party service's identity, where every mark comes from, and which vendors keep the
monogram on purpose. The code is `web/src/components/shared/vendor-marks.ts` (the table),
`web/src/components/shared/vendor-mark.tsx` (`VendorMark`) and `web/scripts/gen-vendor-marks.mjs` (the generator).
docs/ui/DESIGN-SYSTEM.md section 5 ("Third-party logos") sets the visual rule.

## Usage policy

- **Nominative use only.** A mark identifies a service next to its printed name, in a list, a picker or a card. It
  never stands in for our own brand, never suggests endorsement or partnership, and never appears in marketing.
- **Always paired with the name.** `VendorMark` is decorative (`aria-hidden`) beside a visible name. Pass `labelled`
  only where no name is printed, so assistive tech still hears the vendor.
- **One ink, both themes.** Every mark is drawn in `currentColor`, which is the foreground token on a muted tile
  (near black in the light theme, near white in the dark theme). For brands that forbid recolouring, this is their
  approved black or white version, so no colour is changed. Brand colours are not shown (with the one exception
  below), which keeps every mark legible on both themes without a per-brand contrast check.
- **One colour exception.** A brand that offers no one-colour version keeps its own published fills
  (`"ink": "colour"` in the manifest), on the same muted tile. Today that is only Microsoft Outlook, whose icon is a
  self-contained blue tile with a white O, so it reads on both themes as it is. The generator accepts a hex fill and
  an optional opacity per path for such a file, and nothing else (no gradients, no styles).
- **Unmodified shapes.** Marks are copied, never drawn. Normalising means applying transforms, scaling uniformly to a
  24 x 24 box, centring, outlining a stroke to the same filled geometry, writing a rect or polygon as the same path,
  removing an artboard clip path (Slack's trims under 0.1% of an edge), and isolating a symbol from its lockup only
  where the brand itself uses the symbol alone (its favicon or app icon). Nothing is redrawn or approximated, and no
  third-party re-creation is used.
- **The real company only.** A mark is never borrowed from a different company that shares the name (Simple Icons'
  "Rime" is an input method, so Rime's mark comes from rime.ai), and a product never borrows its parent's mark
  ("Microsoft Outlook" does not show the Microsoft logo).
- **Licence first.** Where a brand's guidelines require a licence or written permission to show its logo, the vendor
  keeps the monogram unless the workspace owner approves the mark. On 2026-10-01 the workspace owner approved the
  official marks of Slack, Twilio, Salesforce and Microsoft Outlook, for identifying the service beside its name
  only. `MONOGRAM_ONLY` in `vendor-marks.ts` pins any key that must keep the monogram, so no prefix match can reach
  another mark. Today it pins Visual Studio Code (`vscode`, `visualstudiocode`), whose guidelines forbid the way a
  one-ink tile draws a mark.
- **Inline beside a name.** Where a cell, a tab, a label or a card title prints a company or product, `VendorName`
  puts the compact `xs` mark (16 px, no tile) before the printed name. It shows a real mark only, never a monogram
  beside an unknown name (pass `monogram` to opt in). Sentences keep their words without marks in the middle. A
  card or row whose help text names several services lists their marks after the text instead (the setup
  checklist, the "How it talks" mode cards). `ModelName` does the same for a model id such as `deepgram/nova-3` on
  a gateway, with the maker's mark before the whole id. A bare id prints alone.
- **No runtime fetch.** The generator copies each mark's path data into `vendor-mark-data.ts`. Only console screens
  import it, and `tests/flow-bundle-split.test.ts` keeps it out of the `/s/[slug]` session bundle.

## Sources, in order

1. **Simple Icons** (`simple-icons`, CC0), wherever it has the brand.
2. **Lobe Icons** (`@lobehub/icons-static-svg`, MIT, monochrome files only) for the AI vendors Simple Icons lacks.
3. **Official** marks in `web/scripts/vendor-marks-official/`, taken from each company's own site, brand page or brand
   kit. `manifest.json` there records each file's source URL, the page it was found on, the guideline note, the form
   (standalone symbol, isolated symbol or full logo), the original colours, the fetch date and the sha256 of the file
   as downloaded. The generator accepts only a root with `xmlns`, `viewBox="0 0 24 24"` and `fill="currentColor"`,
   one `<title>`, and `<path>` elements with `d` and `fill-rule`. Scripts, links, images, styles, paints and
   transforms are rejected. A colour mark (`"ink": "colour"`) drops the root fill and gives every path a
   `fill="#rrggbb"` and an optional `opacity`, nothing more.
4. **Monogram** otherwise, a two-letter tile tinted with a stable per-vendor hue.

To add an official mark, save the company's own SVG, normalise it to the format above, add the file and a manifest
entry, map its keys in `OFFICIAL_MARKS`, run `node scripts/gen-vendor-marks.mjs` from `web/`, and add the vendor to
`tests/vendor-marks.test.tsx`.

## Coverage

Keys are `vendorKey(name)` (lower case, letters and digits only). A provider id falls back through its leading words,
so `bey-avatar` resolves through `bey` and `did-avatar` through `did`.

### Official (fetched 2026-10-01)

| Vendor | Keys | Source | Form | Guideline note |
| --- | --- | --- | --- | --- |
| Beyond Presence | `beyondpresence`, `bey` | https://cdn.prod.website-files.com/67ff9faac266bb379ddc0ea2/6807e5a16cc2f96271e59302_Logo.svg (header of beyondpresence.ai) | Symbol isolated from the lockup, used alone as the site's app icon | None public |
| Cartesia | `cartesia`, `cartesiaai` | https://www.cartesia.ai/Cartesia-brand-assets.zip (`Archie/svg/black-archie.svg`), from https://www.cartesia.ai/brand | Standalone symbol (Archie) | No recolouring without permission. Approved black and white versions, rendered as such |
| Composio | `composio` | https://brand.composio.dev/logos/Logomark-Black.svg, from https://brand.composio.dev/logo | Standalone logomark, stroke outlined | Black on light, white on dark, logomark for tight spaces, no recolouring (https://brand.composio.dev/guidelines) |
| D-ID | `did` | https://www.d-id.com/wp-content/uploads/2023/11/d-id-logo.svg (header of d-id.com) | Full logo, also D-ID's favicon | None public |
| Hume | `hume`, `humeai` | Inline header logo on https://www.hume.ai/ | Symbol (seven dots) isolated, used alone as the favicon | None public |
| Inworld | `inworld`, `inworldai` | Inline header logo on https://inworld.ai/ | Symbol isolated, used alone as the favicon | None public |
| Microsoft Outlook | `outlook`, `microsoftoutlook` | https://res.cdn.office.net/files/fabric-cdn-prod_20230815.002/assets/brand-icons/product/svg/outlook_24x1.svg (Fluent UI's Office brand icons) | Flat 24 px product icon, rects and polygons written as paths, in its own colours | https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks needs an express licence for product icons and forbids altering them. The workspace owner approved it. Colour only, so it keeps its fills, and it never falls back to the Microsoft logo |
| Pinecone | `pinecone` | Inline header logo on https://www.pinecone.io/ | Symbol isolated, used alone as the favicon | None public |
| Ragie | `ragie`, `ragieai` | https://cdn.prod.website-files.com/66834c6ee9ee484e8e47a9af/68658d24b3e078f3510ea992_ragie-logo-h-tm.svg (header of ragie.ai) | Symbol isolated, strokes outlined, used alone as the app icon | None public |
| Rime | `rime`, `rimeai`, `rimelabs` | Inline header logo on https://www.rime.ai/ | Full wordmark, also Rime's favicon (no separate symbol) | None public |
| Salesforce | `salesforce` | https://a.sfdcstatic.com/shared/images/c360-nav/salesforce-no-type-logo.svg (header of salesforce.com) | The cloud without type, as the site header serves it | https://www.salesforce.com/company/legal/tmcusageguidelines/ needs written permission and forbids altering the marks. The workspace owner approved it. The public file is blue only (fails 3:1 on the light tile), so one ink |
| Slack | `slack` | https://a.slack-edge.com/9cc0056/marketing/img/nav/logo.svg (header of slack.com/media-kit) | The Slack mark alone, clip path removed | https://slack.com/terms-of-service/slack-brand needs a written licence for most uses and allows black, white or colour. The workspace owner approved it. Rendered as the approved one-colour version |
| Speechify | `speechify` | https://preview.website.cdn.speechify.com/Speechify-Logo.zip (`Logomark_black.svg`), from https://speechify.com/brand-kit/ | Standalone logomark | Black or white versions recommended for most uses |
| Speechmatics | `speechmatics` | https://www.speechmatics.com/_next/static/media/SM-Logo-main.b945b6cd.svg, from https://www.speechmatics.com/brand | Symbol isolated, used alone as the favicon | No colour changes. Approved black and white versions, rendered as such (the offered black and white SVGs embed a raster, so the geometry comes from the vector main logo) |
| Tavus | `tavus` | https://cdn.prod.website-files.com/68c8e57d6e512b9573db146f/68defe67290d1a25593c5525_Tavus%20Brand%20Lite.zip (`Logos/Tavus Symbol/TAVUS-SYMBOL4.svg`), linked from https://www.tavus.io/ | Standalone symbol | None in the kit, which ships dark and white versions |
| Twilio | `twilio` | Inline header logo on https://www.twilio.com/en-us | Symbol (the bug) isolated, used alone as the favicon | https://www.twilio.com/en-us/legal/logo-use needs express written permission. The workspace owner approved it |
| Weaviate | `weaviate`, `weaviatecloud` | https://weaviate.io/img/site/2026/weaviate-logo-2-colours-dark-green.svg (header of weaviate.io) | Symbol isolated, used alone as the favicon | None public |
| Zilliz | `zilliz`, `zillizcloud` | Inline header logo on https://zilliz.com/ | Symbol isolated, used alone as the favicon | Kits on https://zilliz.com/brand-assets bind the downloader to its terms, so the public header logo was used instead |

### Monogram on purpose

| Vendor | Keys | Why |
| --- | --- | --- |
| Telnyx | `telnyx` | https://telnyx.com/media-kit names the horizontal wordmark as the logo and forbids cropping it. The wordmark is unreadable at 12 to 18 px, and the symbol exists only as a raster favicon and raster kit files |
| Simli | `simli` | simli.com offers only a wordmark SVG, unreadable at 12 to 18 px. Its "S" symbol exists only as a raster favicon |
| Anam | `anam` | anam.ai draws only a wordmark (as a CSS mask), unreadable at 12 to 18 px. Its "A" symbol exists only as a raster favicon |
| Chroma | `chroma` | The mark is two overlapping discs told apart only by colour (blue, yellow, red). No one-colour version is offered, one ink would merge the discs into a blob, and the yellow fails 3:1 on the light tile |
| Turbopuffer | `turbopuffer` | The mark is pixel art in several tones. Its approved monochrome version still needs two tones (white fill, black outline), which one ink cannot carry, and each tone fails contrast on one theme |
| Visual Studio Code | `vscode`, `visualstudiocode` (pinned in `MONOGRAM_ONLY`) | https://code.visualstudio.com/brand (read 2026-10-03) asks for the blue icon everywhere, the white one only on blue, and forbids recolouring or adding a background. A one-ink mark on a muted tile does both, the icon's shading is a gradient the generator rejects, and Simple Icons removed the icon at Microsoft's request |
| Continue | (none) | Not named or offered anywhere in the console, so not added. Its symbol (from https://www.continue.dev/continue-logo-black.svg, the same shape as its favicon) can be added as an official mark under the key `continuedev` if a client list ever names it. A bare `continue` key would also mark tools named `continue_*` |
| Microsoft Copilot | (none) | No key. `copilot` alone would be ambiguous, so only `githubcopilot` maps to the GitHub Copilot mark |
| LiveAvatar, LemonSlice, Gladia, Soniox | (none) | Not yet looked up |

### Simple Icons (CC0)

| Vendor | Keys |
| --- | --- |
| Airtable | `airtable` |
| Anthropic | `anthropic` |
| Asana | `asana` |
| Atlassian | `atlassian` |
| Bitbucket | `bitbucket` |
| Box | `box` |
| Brave | `brave`, `bravesearch` |
| Cal.com | `calcom` |
| Calendly | `calendly` |
| Claude (also Claude Code and Claude Desktop) | `claude`, `claudecode`, `claudedesktop`, `claudeai` |
| ClickUp | `clickup` |
| Cline | `cline` |
| Cloudflare | `cloudflare` |
| Confluence | `confluence` |
| Cursor | `cursor` |
| Deepgram | `deepgram` |
| Discord | `discord` |
| Dropbox | `dropbox` |
| DuckDuckGo | `duckduckgo` |
| Elasticsearch | `elasticsearch` |
| ElevenLabs | `elevenlabs` |
| Evernote | `evernote` |
| Facebook | `facebook` |
| Figma | `figma` |
| Fish Audio | `fishaudio` |
| GitHub | `github` |
| GitHub Copilot | `githubcopilot` |
| GitLab | `gitlab` |
| Gmail | `gmail` |
| Google | `google`, `googlecloud`, `googleworkspace` |
| Google Calendar | `googlecalendar` |
| Google Docs | `googledocs` |
| Google Drive | `googledrive` |
| Google Gemini | `gemini`, `googlegemini` |
| Google Meet | `googlemeet` |
| Google Sheets | `googlesheets` |
| HubSpot | `hubspot` |
| Hugging Face | `huggingface` |
| Instagram | `instagram` |
| Intercom | `intercom` |
| JetBrains | `jetbrains` |
| Jira | `jira` |
| Linear | `linear` |
| LiveKit | `livekit`, `livekitinference`, `livekitcloud` |
| Loom | `loom` |
| MailChimp | `mailchimp` |
| Mailgun | `mailgun` |
| Meta | `meta`, `metaai` |
| Milvus | `milvus` |
| MiniMax | `minimax` |
| Miro | `miro` |
| Mistral AI | `mistral`, `mistralai` |
| Model Context Protocol | `mcp`, `modelcontextprotocol` |
| MongoDB | `mongodb` |
| Naver | `naver`, `clova` |
| Notion | `notion` |
| NVIDIA | `nvidia` |
| Ollama | `ollama` |
| OpenRouter | `openrouter` |
| PayPal | `paypal` |
| Perplexity | `perplexity` |
| PostgreSQL | `postgres`, `postgresql`, `pgvector` |
| Qdrant | `qdrant` |
| QuickBooks | `quickbooks` |
| Reddit | `reddit` |
| Redis | `redis` |
| Sentry | `sentry` |
| Shopify | `shopify` |
| Stripe | `stripe` |
| Supabase | `supabase` |
| Telegram | `telegram` |
| Todoist | `todoist` |
| Trello | `trello` |
| Typeform | `typeform` |
| WhatsApp | `whatsapp` |
| Windsurf | `windsurf` |
| Xero | `xero` |
| YouTube | `youtube` |
| Zapier | `zapier` |
| Zed | `zed`, `zedindustries` |
| Zendesk | `zendesk` |
| Zoom | `zoom` |

### Lobe Icons (MIT)

| Vendor | Keys |
| --- | --- |
| AssemblyAI | `assemblyai` |
| AWS | `amazon`, `aws`, `amazonwebservices` |
| Baseten | `baseten` |
| Cerebras | `cerebras` |
| Codex | `codex`, `codexcli`, `openaicodex` |
| Cohere | `cohere`, `cohererank` |
| DeepInfra | `deepinfra` |
| DeepSeek | `deepseek` |
| Exa | `exa` |
| fal | `fal`, `falai` |
| Firecrawl | `firecrawl` |
| Fireworks AI | `fireworks`, `fireworksai` |
| Gemini CLI | `geminicli` |
| Goose | `goose` |
| Grok | `grok` |
| Groq | `groq` |
| Hedra | `hedra` |
| Microsoft | `microsoft`, `azure` |
| OpenAI (also ChatGPT, whose app icon is the same blossom) | `openai`, `chatgpt` |
| Qwen | `qwen` |
| Runway | `runway` |
| SambaNova | `sambanova` |
| Tavily | `tavily` |
| Together AI | `together`, `togetherai` |
| Voyage AI | `voyage`, `voyageai` |
| xAI | `xai` |

## Bundle cost

The official marks add 22.3 kB raw and 8.4 kB gzipped to `vendor-mark-data.ts` (103.6 kB to 125.8 kB raw, 41.8 kB to
50.2 kB gzipped, measured on the file itself with `gzip -9`). Of that, Slack, Twilio, Salesforce and Outlook add
6.0 kB raw and 2.1 kB gzipped (119.8 kB to 125.8 kB, 48.1 kB to 50.2 kB). The AI coding agent and MCP client marks
(Cline, Cursor, GitHub Copilot, JetBrains, Model Context Protocol, Windsurf, Zed, Codex, Gemini CLI, Goose) add
9.7 kB raw and 3.8 kB gzipped (125.8 kB to 135.5 kB, 50.2 kB to 54.0 kB). The file reaches console routes only.
`/s/[slug]` is unchanged, which `tests/flow-bundle-split.test.ts` checks on the static import graph.

## Where the console shows them (V6-38)

- **Settings, AI agents.** A "Works with" row lists Claude Code, Codex CLI, Cursor and Any MCP client. The Connect
  dialog puts a mark on its client cards and snippet tabs, on the skill label and on the Codex note of its last step.
  The Client and Last client columns of the key and activity tables, and their phone cards, lead each name with its
  mark. A client id such as `cursor-vscode` reaches the Cursor mark through the leading word walk. An unknown client
  prints its name alone, and Visual Studio Code prints its name alone too, since `VendorName` never shows a monogram
  unless asked.
- **Overview setup checklist.** The "Connect an AI agent" row lists Claude Code, Codex, Cursor and MCP after its text.
- **Agent editor, "How it talks".** The Cascaded card lists LiveKit and the Realtime card lists Gemini and OpenAI
  after their text. Half-cascade names no service, so it has no marks.
- **Cost views.** Analytics cost drivers, the session Cost tab and the session usage rows lead the provider id with the
  provider's mark, and a `maker/model` id with the maker's mark. The phone card of a cost driver keeps its plain job
  label.
- **Other names.** The "LiveKit Cloud" type card on a new connection, the "OpenAI key" label in the guardrail
  dialog, the MCP server dialog title and its preset list, and the Composio dialog title.
- **Docs links.** `NEXT_PUBLIC_DOCS_URL` is not set anywhere, so the sidebar shows no Documentation link yet (ask 361).
  The 19 vendor docs links in `tools/mcp-presets.ts` all answered 200 on 2026-10-03. Of the 73 links in the provider
  registry, 66 answered 200, four key pages refused a scripted request, two Ragie links failed a TLS handshake from this machine, and one docs link is dead (ask 364).
