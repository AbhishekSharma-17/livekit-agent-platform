# UI/UX Spec — v2 Amendments

Status: **decided** (Fable 5.1, 2026-09-19). Deltas against `../UI_UX_SPEC.md`. Everything not mentioned here is unchanged: the design system, tokens, WP-0 primitives (`web/src/components/shared/`), motion, type, the session surface rules, the ≤400 kB session bundle budget, and the error channels. Reuse WP-0 primitives everywhere; no new colour tokens.

## 1. Information architecture v2

The console sidebar gains groups (Lucide icons in parentheses). Routes are additive; v1 routes keep their paths.

| Group | Screen | Route | Notes |
|---|---|---|---|
| — | Overview | `/console` | Setup checklist grows to 8 rows: sign in ✓, connection tested, provider key added, agent created, test call, panel added, recording on, webhook added. "Live now" lists sessions by connection. |
| Build (`bot`) | Agents | `/console/agents`, `/console/agents/new`, `/console/agents/[id]?section=providers\|instructions\|flow\|panel\|tools\|knowledge\|recording\|limits` | `flow` section only when `mode=flow`; `recording` and `limits` are new sections. |
| Build | Knowledge | `/console/knowledge`, `/console/knowledge/[id]` | Unchanged. |
| Build | Tools (P1 → now P0) | `/console/tools` | Shared tools list (was out of scope in v1; needed because tools are referenced by flows and multiple agents). Sonnet, part of WP-5. |
| Connect (`plug`) | Connections | `/console/connections`, `/console/connections/new`, `/console/connections/[id]?tab=overview\|fleet\|storage\|deploy` | New. |
| Connect | Providers | `/console/providers?kind=` | New; replaces "Credentials" as the entry point. `/console/credentials` remains as the flat key list (WP-4) and is linked from Providers. |
| Connect | Telephony (stretch) | `/console/telephony?tab=numbers\|trunks\|rules\|calls` | New. |
| Connect | Widget (stretch) | inside the agent editor header ("Add to website") | Dialog, not a page. |
| Observe (`activity`) | Sessions | `/console/sessions`, `/console/sessions/[id]?tab=timeline\|transcript\|recording\|cost\|qa\|panel\|raw` | Tabs extended. |
| Observe | Analytics | `/console/analytics?range=7d\|30d\|90d` | New; summary cards + by-day bars + by-agent table. |
| Observe | Evals (Phase 2) | `/console/evals` | Placeholder not shown in Phase 1 (no "coming soon" entries in nav). |
| Settings (`settings-2`) | Settings | `/console/settings?tab=workspace\|team\|api-keys\|webhooks\|storage\|danger` | Tabs replace the single page. |
| Outside | Login, invite | `/login`, `/invite/[token]` | Console-styled auth card; no marketing. |

Workspace switcher: a compact popover in the sidebar footer (name + role chip), only when the user belongs to > 1 workspace.

## 2. New screens — key UX

### 2.1 Connections
- **List**: `ResponsiveTable` with Name, Type (`Cloud`/`Self-hosted` `StatusChip`), URL host, Status (`ok/error/unverified`), Capabilities (`CapabilityBadge` row: inference, SIP, egress, NC tier), Fleet (`StateMeter`-like 4-bar for pool health + "1/1 ready"), Default star. Row menu: Test, Make default, Rotate keys, Delete (disabled with reason when agents are bound).
- **Create/edit** (form ≤720 px): Name, Type (radio cards: LiveKit Cloud / Self-hosted with a one-line "what you need" hint from research §2), URL, API key, API secret (secret fields, never echoed; fingerprint shown after save), Agent name (default `lkap-agent`, help: "must be unique inside this LiveKit project"), Use LiveKit Inference (switch, Cloud only), Worker image (`slim`/`full` radio with the provider count), Deployment mode (radio cards: External / Supervised / Cloud-hosted, with Cloud-hosted disabled for self-hosted). **Test connection** runs before Save is enabled the first time; results render as a `DescriptionList` of capabilities with reasons for false flags ("SIP: not reachable — deploy the SIP service and re-test").
- **Detail → Fleet tab**: desired replicas stepper, Start/Stop/Restart (supervised only), instances table (instance key, image, SDK, status, last heartbeat `RelativeTime`, installed providers count with a popover list). External mode shows three copyable snippets (`.env`, `docker compose` service, `lk agent` steps) with secrets redacted and a "Reveal" (admin) that fetches once. Cloud-hosted shows the bundle download + the exact `lk` commands and the public-API-URL prerequisite as an `Alert`.
- **Detail → Storage tab**: pick or create a storage config; "Test" writes and deletes a 1 KB object.

### 2.2 Providers catalog
- Grouped by kind tabs (Realtime · STT · LLM · TTS · Avatars · VAD/Turn/NC · Embeddings · Image). Search box filters across kinds. Each row: `VendorMark`, label, `CapabilityBadge`s (vision, text-modality, tools, no-key, cloud-only), verification chip (`Verified` / `Available`), install chips ("on cloud-a", greyed "not on self-a"), enabled switch (admin), key state (`key-set` / `key-required` → opens the credential sheet), and a "Catalog" button that previews models/voices/avatars from the vendor with the selected key.
- `incompatible`/`removed` entries are listed under a collapsed "Not available" section with the reason (MiniMax pin, Hedra disabled) so nobody files a bug.

### 2.3 Agent editor changes
- Header: connection chip (name + type) with a popover to change (warns when the new connection lacks installed providers or Inference used by the config); mode chip (`Prompt` / `Flow`) with a switch dialog; "Test call" split button gains "Test chat" (stretch) and "Call a number" (stretch).
- Providers section: connection-aware; three mode cards; slot cards STT → LLM → TTS (cascaded), Realtime (realtime), Realtime + TTS (half-cascade); "Advanced" disclosure for VAD / Turn detection / Noise cancellation with the Inference defaults explained; avatar card: provider, picker (catalog combobox with thumbnail when available, paste-id fallback), options (quality, idle timeout). Slot rows show `installed on <connection>` state; disabled providers do not appear.
- Panel section: the composer (blocks list with drag handles, add-block palette by type with one-line descriptions, per-block config form, live preview pane using the preview route scenes). Custom pack panels show "Custom panel: insurance_notebook — blocks the panel exposes: …".
- Recording section: switch, audio-only (fixed on in Phase 1), storage config, retention. Limits section: concurrency, max duration, rate limits, allowed origins (chips).
- Summary rail adds: connection, mode, recording on/off, version number with "History" link.
- Flow section (stretch): full-width canvas replaces the three-column layout for that section only; node inspector as a right sheet (400 px); validation dots on nodes; version history sheet with diff.

### 2.4 Sessions detail v2
- Header adds channel chip (`web/test/text/sip/widget/api`), connection, cost total, QA score chip (tone by score), recording state.
- Tabs: Timeline (adds `handoff`, `block_update`, `form_submitted`, `dtmf`, `transfer` rows), Transcript, Recording (audio player with a waveform-less simple scrubber, download), Cost (lines table + note rows for unknown prices), QA (score, sentiment, tags, summary, "Re-score"), Panel at end of call (composite or custom), Raw.

### 2.5 Settings tabs
- Workspace (name, slug, timezone, default connection), Team (`ResponsiveTable` members with role select, invite dialog with copyable link), API keys (create → one-time reveal dialog with `CopyButton`, scopes checkboxes, revoke), Webhooks (endpoint list, events multiselect, secret reveal once, test, deliveries drawer with redeliver), Storage (configs), Danger zone (delete workspace, owner only).

### 2.6 Session surface
- `embed=1` (stretch): no top strip, compact control bar, 100% height inside the iframe, `postMessage` state events. Text mode: transcript-first layout, composer pinned, no mic controls.
- Video block and avatar stage: when an avatar is configured the stage shows the avatar track full-well with the user PiP; when a `video` block is present in a `wide` layout the stage moves into the panel column.

## 3. On-hold WP-1..WP-12 — proceed unchanged vs change

| WP | Verdict | Delta |
|---|---|---|
| WP-1 Console shell | **Change** | Nav groups per §1; workspace switcher; `/login` redirect handling; Settings becomes tabbed (tabs themselves come from V2-14). |
| WP-2 Agents list | Unchanged | Add columns Connection and Mode (small). |
| WP-3 Editor shell | **Change** | Connection chip + mode chip in the header; new sections `recording`, `limits`, `flow` in the section nav (flow hidden unless mode=flow); versions link in the rail. |
| WP-4 Providers/credentials | **Change** | Build on the v2 registry types (`availability/verification`, `status` alias ok for now); credential sheet gains Test result + last tested; `/console/credentials` stays as the flat list. V2-13 extends the slot editor afterwards — WP-4 must keep `provider-slot-editor` composable (one slot = one component taking `kind`, `value`, `onChange`, `constraints`). |
| WP-5 Sections | **Change** | Panel tab is replaced by the V2-11 composer (WP-5 ships a stub that renders `PanelLayout` read-only); adds `/console/tools` shared list. Instructions/Tools/Knowledge unchanged. |
| WP-6 Knowledge | Unchanged | Upload cap error copy (25 MB). |
| WP-7 Sessions | **Change** | List filters add channel/connection; detail tabs extended by V2-14 — WP-7 must expose a tab registry so V2-14 adds tabs without editing WP-7 files. |
| WP-8 Session experience | Unchanged | Keep the `StageView` seam; V2-11/V2-18 plug the avatar/video and embed layouts into it. |
| WP-9 Panels tokens | Unchanged | Generic panel becomes the `status/notes/checklist/activity` blocks' visual reference. `handleRequest` for the notebook reads `payload.dialog` (R-V2-3b). |
| WP-10 Preview/capture | Unchanged | Add block scenes later (V2-11 owns `preview/**` after WP-10 lands). |
| WP-11 Home/not-found | Unchanged | Home links to `/login`. Footer "Architecture"/"Runbook" links are **removed** (R-V2-3a in PLAN §8): no docs are served from the web app; an optional external "Documentation" link lives in the console sidebar footer when `NEXT_PUBLIC_DOCS_URL` is set. |
| WP-12 Integration | Unchanged | Runs at the end of wave 2 instead of after WP-11 alone. |

## 4. Copy and vocabulary additions
- "Connection" (never "project" or "server" alone); "Pool" for the workers of a connection; "Supervised" / "External" / "Cloud-hosted".
- "Half-cascade: realtime model thinks, a separate voice speaks".
- Capability reasons are always sentences with a fix ("Not available on self-hosted connections — LiveKit Inference needs LiveKit Cloud. Use your own STT/LLM/TTS keys.").
- Verification chip help: "Verified: passed a live call on this platform. Available: installed and configurable, not yet live-tested."
