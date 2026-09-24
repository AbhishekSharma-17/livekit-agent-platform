# LKAP UI/UX specification — production-grade console and session experience

Status: **decided** (Fable 5.1, design lead). Implementers: Opus 5 / Sonnet 5 per §7. This document supersedes the "design direction" sentence in ARCHITECTURE §12 for the web app; it changes no API or protocol contract (CONTRACTS §7/§10/§11 stay as they are; the optional API asks are listed in §7.14 and are non-blocking).

Inputs reviewed: the 32 "before" screenshots (desktop 1440×900, mobile 390×844) under `scratchpad/ui-audit/before/`, every file under `web/src/components/session`, `web/src/panels`, `web/src/components/console`, the golden fixtures in `web/tests/fixtures`, `contracts/generated/providers.json`, and the design skills (design-engineering, emil-design-eng, frontend-design).

---

## Executive summary (the reply, kept here so it survives)

**Design direction (one paragraph).** LKAP's world is live audio: signal, state, the tally lamp that says "we're on". The system is built around one signature primitive, the **state meter** (a small bar meter that renders the agent's state — connecting, listening, thinking, speaking, failed — identically in the console, the session stage and the session history), one accent (a desaturated **signal green**, used only for "live", focus, selection and the session's *Start call*; primary buttons stay neutral ink), cool-tinted neutrals so the insurance notebook's cream paper reads warmer by contrast, Geist + Geist Mono (already loaded, zero bundle cost; mono is reserved for real data: model ids, slugs, ids, JSON), and Emil-style motion (≤240 ms, strong ease-out, nothing animated on keyboard-driven actions). The console defaults to **light** with a user-selectable dark mode; the session surface is **dark, fixed** (the phone-call metaphor: green start, red hang-up, deep stage). The notebook keeps its paper, ink, tape and stamp; only its "desk" cards and fonts are integrated into the new shell.

**Top 10 critique findings** (details and every screenshot in §1):
1. No identity: stock zero-chroma shadcn, black primary, every surface a bordered card, cards nested in cards (`desktop-editor-providers.png`, `desktop-editor-tools.png`).
2. Mobile console is broken: every table clips columns and hides row actions off-screen (`mobile-console-agents.png`, `mobile-console-sessions.png`, `mobile-console-knowledge.png`, `mobile-console-kb-detail.png`); editor tabs overflow and switches are clipped (`mobile-editor-tools.png`); the wordmark wraps to two lines (`mobile-console-agents.png`).
3. The editor is a 1,700 px single column of raw inputs; model ids (`deepgram/nova-3`) are free text with the human label as a caption *under* the id; voice is free text although the registry knows the voices; no Inference-vs-own-key framing; no per-slot credential status (`desktop-editor-providers.png`).
4. Two horizontal pill rows (global nav + editor tabs) and a header where the agent name is an invisible input and "Published" is a bare toggle beside "Save & validate" (`desktop-editor-*.png`).
5. No overview, no credentials page, no settings, no first-run guidance; empty states are a dashed box; onboarding does not exist (`desktop-home.png`, `desktop-console-agents.png`).
6. Session history is unusable for review: a truncated raw-JSON "Model Usage" stat, transcript and events in two independent scroll boxes, every event a monospace JSON dump, no duration, timestamps with seconds (`desktop-console-session-detail.png`); the list filters by *agent id* text and every row reads "ended" (`desktop-console-sessions.png`).
7. Pre-call is a dark card with a gray "Start call", no device check, no permission guidance, and builder jargon ("Test mode — draft agents allowed") shown to end users (`desktop-session-insurance-precall.png`, `desktop-session-testmode-precall.png`).
8. The autoplay fix ("Enable sound") is a small outline button in the transcript header; a blocked user hears nothing and is not told why (`session-room.tsx`).
9. In-call mobile layout: stage + transcript push the notebook below the fold, and the control bar scrolls with the page, so *End call* needs a scroll; no safe-area insets (`session-shell.tsx`).
10. Not-found and not-published share one dead-end card with no next step (`desktop-session-not-found.png`, `session-experience.tsx`); success/error feedback is inconsistent (validation errors as toasts, inline errors elsewhere).

**Information architecture (console).** Left sidebar (collapsible rail; sheet on mobile): Overview `/console` · Agents `/console/agents` · Credentials `/console/credentials` · Knowledge `/console/knowledge` · Sessions `/console/sessions` · Settings `/console/settings`, plus an unlisted QA route `/console/preview/panels`. The agent editor keeps its five sections as a vertical section nav with validation dots and gains a right-hand summary rail ("what this agent does", publish state, test call). The session surface keeps `/s/[slug]` (+ `?mode=test`), with pre-call → in-call → ended, and distinct not-found / not-published pages.

**Work packages** (owner, order; full detail in §7):
- WP-0 Foundation: tokens, theme, shared primitives, shadcn additions, new hooks, contrast script — **Opus, first, blocking**.
- WP-1 Console shell + IA: sidebar, mobile nav, overview, settings, route move — Sonnet, wave A.
- WP-2 Agents list + new-agent flow (pack templates) — Sonnet, wave A.
- WP-3 Agent editor shell: section nav, summary rail, publish flow, test-call affordance, validation mapping — Opus, wave A.
- WP-4 Providers, model picker, credentials (slot editor, credentials page, credential sheet) — Opus, wave A.
- WP-5 Instructions, panel, tools, knowledge sections — Sonnet, wave A.
- WP-6 Knowledge pages (list, detail, upload, search) — Sonnet, wave A.
- WP-7 Sessions list + detail (unified timeline, final panel state) — Opus, wave A.
- WP-8 Session experience: pre-call, in-call shell, states, end, unavailable pages, audio priming — Opus, wave A.
- WP-9 Panels: generic panel + insurance notebook integration, fixtures — Opus, wave A.
- WP-10 Preview route + capture script extension — Sonnet, wave A (finishes after WP-8/9 seams land).
- WP-11 Home page, console not-found, a11y utilities — Sonnet, wave A.
- WP-12 Integration, gates, after-screenshots review — Opus, last, sequential; Fable reviews the after set.

---

## Table of contents

1. Critique of the current UI, screen by screen
2. Design direction and system (tokens, type, spacing, radius, elevation, motion, icons, Tailwind/shadcn mapping)
3. Information architecture and navigation
4. Key flows, redesigned
5. Session experience redesign
6. States: loading, empty, error, success, validation
7. Work packages (owners, files, dependencies, acceptance, verification)
8. Out of scope / deferred

---

## 1. Critique of the current UI, screen by screen

Screenshot names refer to `scratchpad/ui-audit/before/`. Severity: **C** critical (blocks a real user), **H** high (visibly unprofessional or misleading), **M** medium, **L** low. The Next dev badge in the bottom-left of every screenshot is dev-only and ignored.

### 1.1 System-wide (every screenshot)

- **H — No identity.** The palette is shadcn's zero-chroma neutral set; primary is black; every surface is `rounded-xl border bg-card`. Nothing says what the product is. The two surfaces are inconsistent (light console, dark session) with no theme control anywhere.
- **H — Cards inside cards.** Editor sections are a card containing bordered row-cards containing switches (`desktop-editor-tools.png`, `desktop-editor-panel.png`, `desktop-editor-knowledge.png`). Three borders deep is visual noise and wastes width.
- **H — Mobile console clips.** Every `Table` overflows horizontally without a scroll affordance; the rightmost columns (Actions, Ended, Chunks) are unreachable (`mobile-console-agents.png`, `mobile-console-sessions.png`, `mobile-console-knowledge.png`, `mobile-console-kb-detail.png`).
- **M — Pills everywhere, all the same weight.** Pack chips, status badges, mode text, "no key needed", "ready" (black-filled, reads like a button in `desktop-console-kb-detail.png`), tool names in mono pills. Status and category are indistinguishable.
- **M — Timestamps.** `9/19/2026, 1:47:04 AM` with seconds in every list; no relative time; no durations anywhere.
- **M — Copy is inconsistent and leaky.** "Published" toggle vs "Live" chip vs "Draft"; "Save & validate"; "Matches a pack's `ui_panel_id`"; "New tools are attached to this agent when you click Save & validate"; "User-away timeout (seconds)"; "Auto-inject latest frame per turn"; "Top K"; "3 chunks · 1 documents".
- **M — Feedback channels are mixed.** Required-field errors arrive as toasts ("Label is required.") in the credential and create-agent dialogs; other forms show inline errors; validation results are a banner above the tabs that does not say which tab.
- **L — Icons.** Lucide at 2 px stroke in three sizes; `@phosphor-icons/react` installed and unused.
- **L — Motion.** No press feedback beyond `translate-y-px`; no reduced-motion handling outside the notebook; the control bar's chat reveal animates `height`.

### 1.2 Home (`desktop-home.png`, `mobile-home.png`)

- **H** The page is a scaffold: a badge that repeats the title, a title, one sentence, and two symmetric cards whose links both go to `/console`. No product identity, no environment/health, no footer. First thing anyone sees.
- **M** Underlined text links as the only actions; the second card's "Find your agent's slug in the console" is an instruction, not an action.

### 1.3 Console navigation (`desktop-console-*.png`, `mobile-console-*.png`)

- **H** Three text pills (Agents · Knowledge · Sessions) and a tiny "LKAP console" wordmark. There is no Overview, Credentials or Settings destination; credentials exist only as a "+ New" next to a select inside a slot editor.
- **H** On mobile the wordmark wraps to two lines ("LKAP / console") and the nav pills crowd the right edge (`mobile-console-agents.png`).
- **M** Active state is a gray pill, identical to hover; no current-page indication for detail routes beyond the parent.
- **M** With the editor open, the page stacks two horizontal pill rows (global nav + editor tabs), which is the single biggest reason the editor looks generic (`desktop-editor-providers.png`).

### 1.4 Agents list (`desktop-console-agents.png`, `mobile-console-agents.png`)

- **C (mobile)** Mode column cut mid-word, Published/Updated/Actions unreachable; "New agent" pushed under the description.
- **H** A `Switch` per row toggles publish with no confirmation — an accidental tap unpublishes a live agent. Ten identical "Live" toggles carry no information.
- **H** "Mode: cascaded" as gray text means nothing to a new user; nothing shows which providers or whether keys are configured.
- **M** Three row actions with three different button styles (ghost "Test call ↗", outline "Edit", ghost trash); no search, filter or sort; the name is the link but does not look like one.
- **M** Delete dialog copy contradicts the API (F-22).

### 1.5 Knowledge list and detail (`desktop-console-knowledge.png`, `mobile-console-knowledge.png`, `desktop-console-kb-detail.png`, `mobile-console-kb-detail.png`)

- **C (mobile)** Columns clipped (Documents cut; Chunks/Actions hidden; Size cut on detail).
- **H** Raw embedder id `fastembed-embedding` as a column value.
- **H** Upload is a small outline button; no drop zone, no accepted-types hint, no progress; "pending" state is a gray chip with no indication that indexing takes time (first upload downloads a ~130 MB model).
- **M** "Open" button duplicates the row link; the "ready" chip is black-filled.
- **M** "Test search" has no guidance and no empty-result state design; results (from code) are gray cards with `score 0.812`.

### 1.6 Sessions list (`desktop-console-sessions.png`, `mobile-console-sessions.png`)

- **C (mobile)** Created/Ended clipped; rows are not tappable as a whole.
- **H** Filter is a free-text "Filter by agent id" — nobody knows ids; the status select is the only other control.
- **H** 27 rows, all "ended" in identical gray chips; the one "failed" is the only signal; no duration, no turn count, no relative time, no pagination.
- **M** Agent name is a link styled as plain text.

### 1.7 Session detail (`desktop-console-session-detail.png`, `mobile-console-session-detail.png`)

- **C** "Usage → Model Usage" shows `[{"type":"llm_usage","provider":"liv…` — raw JSON truncated in a stat box.
- **H** Transcript and events are two independent `max-h-96` scroll boxes; a reviewer cannot correlate what was said with what ran.
- **H** Every event is a `<pre>` JSON dump; `agent_state listening` rows flood the list; tool start/end are separate rows.
- **M** Four stat boxes show full timestamps but no duration; the header "lkap-2e788a2a · config v1" is cryptic; transcript bubbles have no speaker label or time.
- **M** Mobile: extremely long page of JSON.

### 1.8 New agent dialog (`desktop-dialog-new-agent.png`, `mobile-dialog-new-agent.png`)

- **H** The pack — the most consequential choice — is a `<select>` reading "Insurance claim intake (insurance_claim)" with no description of what it gives you (pipeline, tools, panel, capabilities).
- **M** A modal for a multi-step decision; name placeholder is the only guidance; no preview of the result.

### 1.9 Agent editor header and tabs (`desktop-editor-*.png`, `mobile-editor-*.png`)

- **H** The agent name is an invisible `Input` styled as a heading — editable by accident, not discoverable as editable; "Description" sits as a bare label + input under it.
- **H** Toolbar: `Published` switch, "Test call ↗", trash icon, "Save & validate" in one row with no hierarchy; no unsaved indicator; no ⌘S; no "what does this agent do".
- **H (mobile)** Tabs overflow ("Knowledge" cut, `mobile-editor-providers.png`); the toolbar wraps to three lines.
- **M** Validation banner appears above the tabs and lists strings without saying which tab.

### 1.10 Providers (`desktop-editor-providers.png`, `mobile-editor-providers.png`)

- **H** A 1,700 px single column. Each slot: a `<select>` "LiveKit Inference (STT) — LiveKit" (label repeats vendor), then a free-text "Model" holding `deepgram/nova-3` with the human name "Deepgram Nova 3" as a caption *below* the id, then more free text (`Language: en`, `Temperature: 0.7`, `Voice: Ashley`).
- **H** No framing of the core decision (LiveKit Inference with no key vs your own key); no per-slot credential status; the only capability badge is "no key needed"; vision only appears as " · vision" inside the model caption.
- **M** Optional slots (Avatar, Image generation) look like required ones with "None" selected; required is a red asterisk; "Advanced: workflow LLM" is a native `<details>`.
- **M** Deferred providers are hidden entirely (F-25) so users cannot see what is coming.

### 1.11 Instructions & voice (`desktop-editor-instructions.png`, `mobile-editor-instructions.png`)

- **H** A monospace textarea grown to ~1,250 px containing the whole prompt: a wall of mono text with no counts, no "reset to pack default", no structure. On mobile the same wall is unreadable (`mobile-editor-instructions.png`).
- **M** Greeting mode "Say (TTS reads it verbatim)"; "User-away timeout (seconds)"; Timezone as free text `UTC`; "Allow interruptions" in a bordered box.

### 1.12 Panel (`desktop-editor-panel.png`, `mobile-editor-panel.png`)

- **H** Panel select shows raw ids (`insurance_notebook`) with the help "Matches a pack's ui_panel_id; unrecognised ids fall back to the generic panel"; no idea what a panel looks like.
- **M** Four capability toggles with no explanation of consequences; "Auto-inject latest frame per turn"; the page is mostly empty.

### 1.13 Tools (`desktop-editor-tools.png`, `mobile-editor-tools.png`)

- **H (mobile)** Switches are clipped at the card edge (rows use `flex justify-between` inside a padded grid that overflows).
- **M** Nine built-in toggles in a 2-column grid with truncated help; "HTTP request tool" as a special row; "Max tool steps per turn" bare number; empty HTTP/MCP sections read "No HTTP tools yet." with the leaky "attached when you click Save & validate".
- **L** Pack tools as mono pills with "not editable here".

### 1.14 Knowledge tab (`desktop-editor-knowledge.png`, `mobile-editor-knowledge.png`)

- **M** "3 chunks · 1 documents"; "Manage knowledge bases" with an external-link icon for an internal route; "Auto-inject on each turn" and "Top K" jargon; on mobile the title wraps against the link.

### 1.15 Pre-call (`desktop-session-insurance-precall.png`, `desktop-session-generic-precall.png`, `desktop-session-testmode-precall.png`, and the mobile trio)

- **H** A centered dark card with a gray "Start call" — the most important button on the product has no emphasis and no verb-specific icon; the page is 90 % empty on desktop.
- **H** No device check, no permission guidance, no "what will happen" (camera may be requested later, the agent will speak first); "Your microphone starts on" is the only hint.
- **H** "Test mode — draft agents allowed" is builder jargon shown on the end-user surface.
- **M** Capability icons (Microphone, Camera, Chat) are passive text; no agent description even though the API carries one; "Voice agent" badge repeats the obvious.

### 1.16 Unavailable (`desktop-session-not-found.png`, `mobile-session-not-found.png`)

- **H** One dead-end card for two different situations (no such agent vs not published — `describeConnectError` distinguishes them but `SessionExperience` only receives a string); no next step, no link, no branding.

### 1.17 In-call (from `session-room.tsx`, `session-shell.tsx`, `agent-stage.tsx`, `connection-banner.tsx`; not captured)

- **C (mobile)** `SessionShell` is a `h-dvh` flex column whose middle is a grid that stacks stage (min 208 px) → transcript (min 192 px) → panel (min 320 px / 448 px for the notebook) and puts the controls *after* them in normal flow: on a phone the notebook is below the fold and *End call* requires scrolling; no `env(safe-area-inset-bottom)`.
- **H** "Enable sound" (`StartAudioButton`) is a small outline button in the transcript header. A visitor whose browser blocked autoplay sees a "Listening" stage and hears nothing.
- **H** No elapsed timer; the stage caption is 12 px gray ("Stage9 Insurance · Listening"); connection states are a plain top banner.
- **M** The vendored control bar's screen-share "on" state is hard-coded `blue-500`; "END CALL" is mono uppercase in a soft red pill; the self-view is a 112 px tile.
- **M** `open_dialog`/`focus` UI requests are "politely declined" for every panel, including the notebook that has a dialog.

### 1.18 Panels (from `panels/generic/index.tsx`, `panels/insurance_notebook/*`, fixtures)

- **Notebook — keep:** the ruled paper, margin line, punch holes, handwritten lines with ink-in, red blanks, taped polaroids with tilt, sketch frame, rubber stamp with `mix-blend-mode: multiply`, the scribbling pen, "developing…" placeholder. This is the product's best moment.
- **M** The desk cards (Still needed, Claim team, Incident sketch, packet button) are generic dark cards with uppercase tracked labels and hard-coded emerald/amber/sky/red classes; the `%` ring's inner disc falls back to `#1e2126`.
- **M** `.field` uses a `border-left: 2px` status stripe (banned pattern); Google Fonts are `@import`ed at runtime from a `<style>` string (render-blocking, FOUT on the paper).
- **M** Generic panel: uppercase tracked headers, side rails on notes, same hard-coded palette; otherwise sound.

## 2. Design direction and system

### 2.1 Brand feel

- **Subject.** A control room for live voice/video agents. Two audiences on two surfaces: builders in a *studio* (console) and end users in a *call* (session). The insurance notebook is the flagship pack and must feel like a real object (paper, ink, tape) sitting on that desk.
- **Feel.** Calm, precise, quietly live. Neutral surfaces with a faint cool tint; one accent that means exactly one thing ("this is live / this is the thing that is on"); dense but breathable data; motion that confirms rather than decorates.
- **Signature element: the state meter.** A 4-bar (console) / 5-bar (session stage) meter, CSS-only, that encodes agent state: `idle` (flat, muted), `connecting` (slow sequential pulse), `listening` (gentle breathing on the outer bars), `thinking` (left-to-right sweep), `speaking` (level-driven on the stage, animated bounce elsewhere), `failed` (flat, danger tone), `ended` (flat, muted). It appears in: the sidebar wordmark (idle), the agents list ("Live" chip uses a 2-bar dot variant), the session stage (large, and the LiveKit bar visualizer takes over the same geometry once audio flows), the session-history status chip, the overview's "live now" row, and the pre-call button's icon. One vocabulary everywhere: **Connecting · Listening · Thinking · Speaking · Reconnecting · Ended**.
- **What we deliberately do not do.** No purple/blue gradients, no gradient text, no glass cards, no side-stripe borders (the notebook's `.field` stripe goes), no "hero metric" tiles on the overview, no sun/moon toggle, no exclamation marks, no "Oops".
- **The one aesthetic risk.** The session surface goes fully to the phone-call metaphor: a deep stage, a large green *Start call*, a solid red hang-up, an elapsed timer, and the meter as the "face" of the agent when there is no avatar. It is justified by who is on the other end: a stressed claimant on a phone who has never seen a "voice agent" but has answered a call.

### 2.2 Color tokens

All colors are OKLCH. Neutrals carry a faint cool tint (hue 250, chroma 0.003–0.015). The accent ("brand", signal green) is desaturated (chroma ≤ 0.15). Semantic tones each have three roles: `solid` (fills, dots), `soft` (tinted surface), `text` (readable on `background`).

**Contrast targets (WCAG 2.2 AA), verified by WP-0's `scripts/check-contrast.mjs`, not asserted here:** `foreground`/`background` ≥ 7:1; `muted-foreground`/`background` ≥ 4.5:1; every `*-text`/`background` and `*-text`/`*-soft` ≥ 4.5:1; every `*-foreground`/`*` solid pair ≥ 4.5:1; `border`/`background` ≥ 1.5:1 (decorative) and `input`/`background` ≥ 3:1 (field edge, WCAG 1.4.11); `ring` against both `background` and `card` ≥ 3:1. If a pair fails, adjust lightness of the token, never the target. The script must resolve `var()` alias chains (e.g. `--success` → `--brand`) and composite alpha tokens (e.g. dark `--brand-soft: oklch(… / 14%)`, `--border: oklch(1 0 0 / 10%)`) over `--background` *and* `--card` before computing ratios. Expected first-run adjustment: dark `--input: oklch(1 0 0 / 16%)` will likely miss 3:1 over `--card`; raise it (≈ 28–32 % alpha) rather than lowering the target.

```css
/* web/src/app/globals.css — WP-0 owns this file */
:root {
  /* surfaces */
  --background: oklch(0.985 0.003 250);
  --foreground: oklch(0.17 0.012 250);
  --card: oklch(0.995 0.002 250);
  --card-foreground: var(--foreground);
  --popover: oklch(0.995 0.002 250);
  --popover-foreground: var(--foreground);
  --muted: oklch(0.955 0.005 250);
  --muted-foreground: oklch(0.47 0.015 250);
  --accent: oklch(0.95 0.006 250);          /* shadcn "accent" = subtle hover surface; keep the name for agents-ui */
  --accent-foreground: var(--foreground);
  --secondary: oklch(0.94 0.006 250);
  --secondary-foreground: oklch(0.25 0.015 250);
  --primary: oklch(0.20 0.015 250);         /* neutral ink; primary buttons are NOT green */
  --primary-foreground: oklch(0.985 0.003 250);
  --border: oklch(0.90 0.008 250);
  --input: oklch(0.85 0.010 250);
  --ring: var(--brand);
  --destructive: oklch(0.55 0.20 27);
  --destructive-foreground: oklch(0.99 0.01 27);

  /* brand = signal green; the only accent */
  --brand: oklch(0.55 0.14 152);
  --brand-foreground: oklch(0.99 0.01 152);
  --brand-text: oklch(0.42 0.12 152);
  --brand-soft: oklch(0.94 0.04 152);
  --brand-line: oklch(0.78 0.10 152);

  /* semantic tones (solid / soft / text) */
  --info: oklch(0.55 0.12 250);       --info-soft: oklch(0.94 0.03 250);     --info-text: oklch(0.42 0.11 250);
  --success: var(--brand);            --success-soft: var(--brand-soft);     --success-text: var(--brand-text);
  --warning: oklch(0.72 0.15 75);     --warning-soft: oklch(0.96 0.06 85);   --warning-text: oklch(0.48 0.12 70);
  --danger: oklch(0.58 0.20 27);      --danger-soft: oklch(0.95 0.03 27);    --danger-text: oklch(0.47 0.18 27);

  /* sidebar (shadcn sidebar component reads these) */
  --sidebar: oklch(0.97 0.004 250);
  --sidebar-foreground: var(--foreground);
  --sidebar-primary: var(--primary);
  --sidebar-primary-foreground: var(--primary-foreground);
  --sidebar-accent: oklch(0.93 0.006 250);
  --sidebar-accent-foreground: var(--foreground);
  --sidebar-border: oklch(0.905 0.008 250);
  --sidebar-ring: var(--brand);

  /* session-only surfaces (defined in both themes; used under [data-surface=session]) */
  --stage: oklch(0.93 0.006 250);
  --stage-foreground: var(--foreground);

  /* elevation: shadows tinted with the surface hue */
  --shadow-color: oklch(0.20 0.02 250);
  --shadow-sm: 0 1px 2px color-mix(in oklch, var(--shadow-color) 6%, transparent);
  --shadow-md: 0 4px 12px -2px color-mix(in oklch, var(--shadow-color) 10%, transparent), 0 1px 2px color-mix(in oklch, var(--shadow-color) 6%, transparent);
  --shadow-lg: 0 12px 32px -8px color-mix(in oklch, var(--shadow-color) 18%, transparent), 0 2px 6px color-mix(in oklch, var(--shadow-color) 8%, transparent);

  /* radius scale (explicit; do not derive from one base) */
  --radius-xs: 4px;   /* chips, kbd */
  --radius-sm: 6px;   /* inputs, buttons, menu items */
  --radius-md: 8px;   /* rows, tiles */
  --radius-lg: 12px;  /* cards, sections */
  --radius-xl: 16px;  /* dialogs, sheets, session columns */
  --radius-2xl: 20px; /* pre-call / end-of-call card */
  --radius: var(--radius-sm); /* shadcn base; keep for vendored components */

  /* motion */
  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
  --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);
  --dur-1: 120ms; /* press feedback, tooltips */
  --dur-2: 180ms; /* hover, chips, menus */
  --dur-3: 240ms; /* dialogs, popovers, section switches */
  --dur-4: 320ms; /* sheets/drawers only */
}

.dark {
  --background: oklch(0.17 0.012 250);
  --foreground: oklch(0.96 0.005 250);
  --card: oklch(0.21 0.012 250);
  --card-foreground: var(--foreground);
  --popover: oklch(0.23 0.012 250);
  --popover-foreground: var(--foreground);
  --muted: oklch(0.26 0.012 250);
  --muted-foreground: oklch(0.72 0.012 250);
  --accent: oklch(0.27 0.012 250);
  --accent-foreground: var(--foreground);
  --secondary: oklch(0.28 0.012 250);
  --secondary-foreground: var(--foreground);
  --primary: oklch(0.94 0.005 250);
  --primary-foreground: oklch(0.20 0.012 250);
  --border: oklch(1 0 0 / 10%);
  --input: oklch(1 0 0 / 16%);
  --ring: var(--brand);
  --destructive: oklch(0.66 0.19 25);
  --destructive-foreground: oklch(0.15 0.03 25);

  --brand: oklch(0.74 0.15 152);
  --brand-foreground: oklch(0.18 0.03 152);
  --brand-text: oklch(0.80 0.14 152);
  --brand-soft: oklch(0.74 0.15 152 / 14%);
  --brand-line: oklch(0.74 0.15 152 / 40%);

  --info: oklch(0.72 0.11 250);       --info-soft: oklch(0.72 0.11 250 / 14%);     --info-text: oklch(0.80 0.10 250);
  --success: var(--brand);            --success-soft: var(--brand-soft);           --success-text: var(--brand-text);
  --warning: oklch(0.80 0.14 80);     --warning-soft: oklch(0.80 0.14 80 / 14%);   --warning-text: oklch(0.85 0.13 85);
  --danger: oklch(0.70 0.17 25);      --danger-soft: oklch(0.70 0.17 25 / 14%);    --danger-text: oklch(0.80 0.14 25);

  --sidebar: oklch(0.19 0.012 250);
  --sidebar-foreground: var(--foreground);
  --sidebar-primary: var(--primary);
  --sidebar-primary-foreground: var(--primary-foreground);
  --sidebar-accent: oklch(0.25 0.012 250);
  --sidebar-accent-foreground: var(--foreground);
  --sidebar-border: oklch(1 0 0 / 8%);
  --sidebar-ring: var(--brand);

  --stage: oklch(0.13 0.012 250);
  --stage-foreground: var(--foreground);

  --shadow-color: oklch(0.05 0.01 250);
  --shadow-sm: 0 1px 2px color-mix(in oklch, var(--shadow-color) 40%, transparent);
  --shadow-md: 0 4px 14px -2px color-mix(in oklch, var(--shadow-color) 50%, transparent), 0 1px 2px color-mix(in oklch, var(--shadow-color) 40%, transparent);
  --shadow-lg: 0 16px 40px -8px color-mix(in oklch, var(--shadow-color) 65%, transparent), 0 2px 6px color-mix(in oklch, var(--shadow-color) 40%, transparent);

  color-scheme: dark;
}
```

Rules:
- **Which theme where.** Console: light by default, user preference Light / Dark / System stored by `next-themes` (`storageKey="lkap-theme"`, `attribute="class"`, `defaultTheme="light"`, `enableSystem`), the provider mounted in `app/console/layout.tsx` only (the root layout carries a "nobody edits after W0-SCAFFOLD" rule and stays untouched; `sonner.tsx` already calls `useTheme()` and gains a provider by this). Session: dark, fixed, via the existing `.dark` wrapper in `app/(session)/layout.tsx` plus `data-surface="session"`; the tokens are complete in both themes so a light session is a one-line change later, and the preview route renders the session shell in both to prove it.
- **Hard-coded palette classes are banned** in new and touched code: no `emerald-*`, `sky-*`, `amber-*`, `red-*`, `blue-*`. Use `bg-brand`, `text-brand-text`, `bg-info-soft`, `text-warning-text`, etc. Migration targets (each owner fixes their files): `connection-banner.tsx`, `validation-banner.tsx`, `panel-tab.tsx` (amber hint), `pre-call-card.tsx` (`text-red-300`), `panels/generic/index.tsx` (`TONE_BADGE`, `TONE_RAIL`, `PHASE_DOT`), `panels/insurance_notebook/studio.tsx` (`ringTone`, `PHASE_DOT`, emerald/amber checks), `sketch-card.tsx`, `constants.ts` (`TONE_BADGE_CLASSES`), the notebook ring's `#1e2126` fallback → `var(--color-card)` with a `--card`-driven fallback.
- The vendored `agents-ui` control bar hard-codes `blue-500` for "screen share on"; it is restyled from the wrapper, not forked (see §5.4).

### 2.3 Typography

Faces: **Geist** (UI/body) and **Geist Mono** (data only: model ids, slugs, session/room ids, fingerprints, JSON, timestamps in timelines, keyboard hints). Both already load via `next/font` in the root layout; no third UI face. The notebook keeps **Caveat** (handwriting) and **Patrick Hand** (labels), loaded with `next/font/google` in the session layout and the preview layout and exposed as `--font-hand` / `--font-hand-label` (WP-8, WP-10); the notebook CSS references the variables with the current fallbacks and drops its runtime `@import url(fonts.googleapis.com…)`.

Scale (size/line-height, weight, tracking; Tailwind utilities in brackets):

| Role | Spec | Utilities | Used for |
|---|---|---|---|
| display | 28/34, 600, -0.02em | `text-[1.75rem] leading-[2.125rem] font-semibold tracking-[-0.02em]` | pre-call agent name, end-of-call title, overview greeting |
| h1 | 22/28, 600, -0.015em | `text-[1.375rem] leading-7 font-semibold tracking-[-0.015em]` | page titles |
| h2 | 17/24, 600, -0.01em | `text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em]` | section titles in the editor, dialog titles |
| h3 | 14/20, 600 | `text-sm font-semibold` | card titles |
| body | 14/21, 400 (console) · 16/24 (session) | `text-sm leading-[1.5]` · `text-base` | default text |
| small | 13/18, 400 | `text-[0.8125rem] leading-[1.125rem]` | table cells, hints |
| caption | 12/16, 500 | `text-xs font-medium` | labels, chip text |
| micro | 11/14, 500, +0.02em | `text-[0.6875rem] leading-[0.875rem] font-medium tracking-[0.02em]` | chips inside dense rows only |
| data | 13/18, mono, 400, tabular | `font-mono text-[0.8125rem] tabular-nums` | ids, models, JSON |

Rules: sentence case everywhere (no Title Case, no all-caps section labels in the console; the panels' existing uppercase tracked headers are replaced by `h3` caption style); `text-wrap: balance` on titles, `text-wrap: pretty` on descriptions; all numbers `tabular-nums`; paragraphs ≤ 65ch; labels are nouns, buttons are verbs, an action keeps its name through the flow (button "Publish" → toast "Published").

### 2.4 Spacing, layout, radius, elevation

- **Spacing** is a 4 px scale: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64. Page gutter 16 (mobile) / 24 (≥768) / 32 (≥1024). Card padding 16 (compact rows) / 20 (default). Gap between sections 24; between fields 16; between a label and its control 6.
- **Widths.** Console content max 1200 px; forms and reading columns max 720 px; the editor's summary rail 280 px; sidebar 232 px expanded, 56 px collapsed.
- **Surfaces.** Depth is expressed with *one* device per level: page = `background`; grouped content = `card` with a 1 px `border` (no shadow); floating = `popover` + `shadow-lg`; the session stage = `stage` (deeper than `background`) with no border. **Cards do not nest.** Inside a card, rows are separated by `divide-y` hairlines, not by inner bordered boxes (this removes the card-in-card pattern visible in every editor screenshot).
- **Radius** per §2.2 scale. Inner elements are never rounder than their container.
- **Focus** is a 2 px `ring` (brand) with 2 px offset on `background`, on every interactive element including table rows that act as links, custom toggles and the notebook's sketch button. Never remove outlines without replacing them.
- **Hit targets** ≥ 40×40 px on the session surface and in mobile console lists; ≥ 32×32 px on desktop console.

### 2.5 Motion

Follow the Emil rules exactly:
- Durations from the `--dur-*` tokens; UI never exceeds 320 ms (sheets). Enter = `--ease-out`; on-screen movement = `--ease-in-out`; hover/color = `ease`; progress/marquee = `linear`. Never `ease-in`, never `transition: all`.
- Animate only `transform`, `opacity`, `clip-path`, `filter`. Height changes use `grid-template-rows: 0fr → 1fr` (the transcript sheet, the chat input reveal) — the vendored control bar animates `height` with Motion; leave it.
- Enter from `scale(0.97)`/`translateY(4px)` + `opacity: 0`, never from `scale(0)`. Popovers scale from their trigger (`transform-origin: var(--radix-popover-content-transform-origin)`); dialogs from center.
- Press feedback: `active:scale-[0.98]` at `--dur-1` on every button and toggle (the shadcn button currently uses `translate-y-px`; WP-0 switches it to scale).
- No animation on keyboard-triggered navigation (section switch via keys, ⌘K palette open/close, tab switches). Tooltips: 400 ms delay on first, instant thereafter (Radix `TooltipProvider delayDuration={400} skipDelayDuration={300}`).
- Stagger only where a list *appears* (overview setup checklist on first load, transcript first paint): 30–50 ms per item, max 8 items.
- `prefers-reduced-motion: reduce`: remove transforms and the meter's motion (keep opacity/color); the notebook already does this.
- Signature motion budget: the state meter (CSS keyframes, GPU-only), the notebook's ink-in / pin-in / stamp-in (kept), the session's connect transition (stage fades up, controls slide in from the bottom once, 240 ms).

### 2.6 Iconography

**Lucide only**, via a wrapper `components/shared/icon.tsx` that sets `strokeWidth={1.75}`, `absoluteStrokeWidth`, `aria-hidden` by default and sizes `sm` 14 (inline in text), `md` 16 (buttons, rows), `lg` 20 (sidebar, section nav), `xl` 24 (session control bar and stage). `@phosphor-icons/react` is a dependency with zero imports; WP-0 removes it. The vendored `agents-ui` components keep their own Lucide imports (no fork), which is why we do not introduce a second set. Metaphors: microphone / video / monitor-up / message-square for the four capabilities everywhere (pre-call, panel section, control bar); `radio` for live; `key-round` for credentials; `book-open` for knowledge; `history` for sessions; `bot` for agents; `layout-dashboard` for overview; `settings-2`; no rocket, no shield, no sparkles.

### 2.7 Mapping onto Tailwind v4 / shadcn

Tailwind v4 has no config file; everything is in `globals.css`. WP-0 extends `@theme inline` with:

```css
@theme inline {
  /* keep the existing --color-* mappings for shadcn + agents-ui, then add: */
  --color-brand: var(--brand);
  --color-brand-foreground: var(--brand-foreground);
  --color-brand-text: var(--brand-text);
  --color-brand-soft: var(--brand-soft);
  --color-brand-line: var(--brand-line);
  --color-info: var(--info);           --color-info-soft: var(--info-soft);           --color-info-text: var(--info-text);
  --color-success: var(--success);     --color-success-soft: var(--success-soft);     --color-success-text: var(--success-text);
  --color-warning: var(--warning);     --color-warning-soft: var(--warning-soft);     --color-warning-text: var(--warning-text);
  --color-danger: var(--danger);       --color-danger-soft: var(--danger-soft);       --color-danger-text: var(--danger-text);
  --color-stage: var(--stage);         --color-stage-foreground: var(--stage-foreground);
  --radius-xs: var(--radius-xs); --radius-sm: var(--radius-sm); --radius-md: var(--radius-md);
  --radius-lg: var(--radius-lg); --radius-xl: var(--radius-xl); --radius-2xl: var(--radius-2xl);
  --shadow-sm: var(--shadow-sm); --shadow-md: var(--shadow-md); --shadow-lg: var(--shadow-lg);
  --ease-out: var(--ease-out); --ease-in-out: var(--ease-in-out); --ease-drawer: var(--ease-drawer);
  --font-hand: var(--font-hand); --font-hand-label: var(--font-hand-label);
}
```

So implementers write `bg-brand-soft text-brand-text`, `rounded-lg`, `shadow-md`, `ease-[var(--ease-out)]` / `duration-[var(--dur-2)]` (or the `transition-*` utilities with the tokens). shadcn components: **never run `shadcn init`** (the base is `radix-nova`; re-init breaks the build). Add components individually with `pnpm dlx shadcn@latest add <name>`; WP-0 adds: `sidebar`, `sheet`, `tooltip`, `dropdown-menu`, `popover`, `command`, `separator`, `progress`, `alert`, `label`, `checkbox`, `radio-group`, `scroll-area`, `collapsible`, `breadcrumb`, `kbd`, `input-group` (if present in the registry; otherwise skip). Each added file is reviewed for hard-coded colors and adjusted to tokens. A `[data-surface=session]` wrapper sets `color-scheme: dark` only. A wrapper `font-size` would be a no-op because components set rem-based `text-sm`/`text-xs` explicitly; the 16 px session body is achieved by session components using `text-base` deliberately (WP-8), and the console's 14 px body by console components using `text-sm`.

Shared primitives (WP-0, `web/src/components/shared/`), with the props other packages code against:

| Component | Props (contract) | Notes |
|---|---|---|
| `StateMeter` | `state: "idle"\|"connecting"\|"listening"\|"thinking"\|"speaking"\|"failed"\|"ended"`, `size: "xs"\|"sm"\|"md"\|"lg"`, `bars?: 4\|5`, `label?: boolean` | CSS-only; `xs` = 2-bar dot for chips; `lg` = stage; exposes `data-state`; `aria-label` from a `STATE_LABEL` map (the single vocabulary). |
| `StatusChip` | `tone: "neutral"\|"info"\|"success"\|"warning"\|"danger"\|"live"`, `dot?: boolean`, `size?: "sm"\|"md"`, `children` | Square-ish (`rounded-xs`), `*-soft` bg + `*-text`; `live` renders `StateMeter xs`. Replaces ad-hoc `Badge` for status. |
| `Icon` | `as: LucideIcon`, `size?: "sm"\|"md"\|"lg"\|"xl"`, `label?: string` | see §2.6 |
| `EmptyState` | `icon?`, `title`, `description?`, `action?`, `secondary?`, `compact?` | one primary action, optional link |
| `PageHeader` | `title`, `description?`, `eyebrow?`, `breadcrumbs?: {label, href?}[]`, `actions?`, `sticky?` | |
| `Section` | `title`, `description?`, `aside?`, `children`, `id` | the editor's card replacement: title row + `divide-y` body |
| `Field` | `label`, `htmlFor`, `hint?`, `error?`, `required?`, `optional?`, `children`, `inline?` | required marked with text "Required" in the hint slot, not a red asterisk |
| `DescriptionList` | `items: {term, detail, mono?}[]`, `columns?: 1\|2\|3` | replaces the `Stat` boxes |
| `CopyButton` | `value`, `label`, `size?` | copies, swaps icon → check for 1.2 s, announces via `aria-live` |
| `RelativeTime` | `iso`, `withExact?: boolean` | "4 min ago", `title` = exact; uses `lib/format.ts` |
| `VendorMark` | `vendor: string`, `size?` | two-letter monogram in a tinted square (no logos; see §8) |
| `CapabilityBadge` | `kind: "vision"\|"realtime"\|"tools"\|"silent-tools"\|"voices"\|"no-key"\|"key-required"\|"key-set"`, `count?` | icon + word, `micro` type |
| `Kbd` | children | shadcn `kbd` wrapper |
| `ResponsiveTable` | `columns`, `rows`, `renderCard`, `rowHref?` | renders a `Table` ≥ 768 px and a card list below; row focusable as a link |

`web/src/lib/format.ts` (WP-0): `formatDateTime(iso, {seconds?: false})`, `formatTime(iso)`, `formatDuration(ms)` → `1m 42s`, `formatBytes(n)`, `toMillis(ts: string | number)` (ISO string → `Date.parse`; number < 1e11 → seconds × 1000; else ms), `pluralize(n, one, many)`.

## 3. Information architecture and navigation (console)

### 3.1 Decision: left sidebar

A collapsible left sidebar (shadcn `sidebar`), not the current top nav. The discriminating fact: the agent editor already needs a horizontal section switcher, and a horizontal global nav above it produces two stacked rows of pills on every editor screen (`desktop-editor-*.png`). Moving global navigation to the left frees the top edge for the sticky page header (title, status, Save/Publish/Test call) and lets the editor use a vertical section nav that can carry validation dots.

- ≥ 1024 px: sidebar 232 px, collapsible to a 56 px icon rail (state persisted in a cookie by the shadcn component; tooltips when collapsed). Content max-width 1200 px with 32 px gutters.
- 768–1023 px: rail collapsed by default.
- < 768 px: no sidebar; a 56 px top bar with a menu button (opens the sidebar as a sheet from the left), the current page title, and the page's primary action; content gutter 16 px.
- Keyboard: `⌘K` command palette (P1) for routes and agents; `g` then `o/a/c/k/s/,` shortcuts (P1); Tab order: skip link → sidebar → top bar → main.

### 3.2 Destinations (in for this pass)

| Item | Route | Icon | Notes |
|---|---|---|---|
| Overview | `/console` | `layout-dashboard` | first-run checklist, live now, recent sessions (§4.1) |
| Agents | `/console/agents` | `bot` | list; `/new` guided flow; `/[id]?section=` editor |
| Credentials | `/console/credentials` | `key-round` | new page (§4.5) |
| Knowledge | `/console/knowledge` | `book-open` | list + `/[id]` |
| Sessions | `/console/sessions` | `history` | list + `/[id]` |
| Settings | `/console/settings` | `settings-2` | appearance, environment (§4.11) |
| (unlisted) Preview | `/console/preview/panels` | — | QA scenes; `robots: noindex`; not in nav |

Sidebar footer: theme menu (Light / Dark / System as radio items in a dropdown; the trigger shows the current choice as text + icon), version from `/v1/health`, "Docs" link. Wordmark at the top: the state-meter mark (idle) + "LKAP" in 600 weight + "Console" in muted 400 on one line; collapsed shows the mark only.

Breadcrumbs in the top bar for depth ≥ 2 (`Agents / Stage9 Insurance`, `Sessions / 19 Sep, 03:04`). The page's primary action lives in the `PageHeader` on desktop and in the top bar on mobile.

### 3.3 Out for this pass (see §8)

Multi-user/org switcher, auth/login, packs page (packs are visible through the new-agent flow and Settings › Environment), tools as a top-level page (tools remain agent-scoped; shared tools are attachable from the editor), analytics/usage dashboards, notifications.

### 3.4 Home page `/`

Not a marketing site. One screen: wordmark + one sentence ("Configure real-time voice and video agents on LiveKit Cloud, then hand out a link."), two actions (primary "Open console"; secondary "How session pages work" → an inline disclosure explaining `/s/<slug>`, test mode, and publishing), an environment line (version · packs loaded · API reachable, from the public health endpoint, rendered server-side), footer links (Architecture, Runbook). Renders light on hard load and tolerates `.dark` during client navigation (§7.12). Redirecting `/` to `/console` is rejected: the home page is where a curious visitor lands from a shared link with the wrong path.

### 3.5 URL conventions

- Editor sections are deep-linkable: `/console/agents/[id]?section=providers|instructions|panel|tools|knowledge` (default `providers`); validation issues link to `?section=…`.
- Lists keep filters in the query string (`?q=`, `?status=`, `?pack=`, `?agent=`, `?range=`) so links are shareable and the back button restores them.
- Session page: `/s/[slug]`, `/s/[slug]?mode=test` (unchanged contract, D-W2-1); `&embed=1` is reserved for the P1 iframe drawer (hides the test-mode bar and uses the compact layout).

## 4. Key flows, redesigned

### 4.1 First run, overview and empty states

`/console` (Overview) is the first screen. It has no hero metrics. Layout (desktop): a `PageHeader` "Overview" with a one-line status sentence in `muted-foreground` composed from live data — "3 agents · 2 live · 14 sessions in the last 7 days · 1 failed" (each number is a link to the filtered list) — then two columns: left (2/3) **Setup** and **Recent sessions**; right (1/3) **Live now** and **Quick actions**.

**Setup checklist** (`Section` "Set up LKAP"), each row with a check glyph, a title, one line of help and an action; rows complete themselves from data:
1. "API is reachable" — `useHealth().ok`; failing: "The web app can't reach the API at <origin>." + "Open Settings › Environment".
2. "LiveKit is configured" — `health.livekit_url` non-empty; failing: "Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` for the API and the worker (Runbook §1)."
3. "Create your first agent" — `agents.total > 0`; action "New agent" → `/console/agents/new`.
4. "Add a provider key (optional)" — `credentials.total > 0`; help "LiveKit Inference needs no key. Add one to use Gemini Live, OpenAI, Deepgram, ElevenLabs…"; action "Add credential".
5. "Make a test call" — `sessions.total > 0`; action "Test call" on the most recently updated agent.
6. "Publish an agent" — any `published`; action "Open agent".
When all six are done the section collapses to a single line "Setup complete" with a "Show" toggle; it never nags.

**Recent sessions**: 8 rows (agent, `StatusChip`, duration, `RelativeTime`), "View all". **Live now**: the published agents with their public link + copy button and a `StatusChip live`. **Quick actions**: New agent · Add credential · New knowledge base.

Empty states (all lists, §6): an `EmptyState` with an icon, a title stating what will appear here, one sentence of why it matters, and one primary action. Copy:
- Agents: "No agents yet" / "An agent is a voice or video assistant with its own providers, instructions and tools." / "New agent".
- Credentials: "No credentials yet" / "LiveKit Inference works without keys. Add a key to use other vendors." / "Add credential".
- Knowledge: "No knowledge bases yet" / "Upload documents the agent can search during a call." / "New knowledge base".
- Sessions: "No calls yet" / "Every test call and public call is recorded here with its transcript." / "Open an agent".

### 4.2 Creating an agent

**Superseded (v4).** The New agent flow is a `Dialog` (`size="xl"`) with a starter-template gallery, opened from every "New agent" trigger; `/console/agents/new` is a deep link that opens it over the agents list (`?template=<id>` preselects a starter). The design — gallery tiles, preview pane, the name step, the post-create toast and the editor's "Next steps" card — is in [`v4/TEMPLATES.md` §6](v4/TEMPLATES.md#6-console-the-new-agent-dialog); the ruling that withdraws "a page, not a modal" is R-V4-2 in [`v4/PLAN-V4.md` §5](v4/PLAN-V4.md#5-rulings).

### 4.3 The agent editor

Structure (desktop ≥ 1024): sticky header (in the console top bar region) → three columns: section nav 200 px · content max 720 px · summary rail 280 px.

**Header**: back link "Agents"; title = agent name (display as `h1`; a pencil `icon-sm` button turns it into an inline input with Save/Cancel, Enter/Esc); slug in mono with `CopyButton`; `StatusChip` Draft/Live; unsaved dot + "Unsaved changes" text when dirty; actions right: "Test call" (split button: primary opens a new tab; menu: "Save and test", "Copy test link"), "Publish" or "Unpublish" (popover, §4.9), "Save" (primary, disabled when clean, shows "Saving…"), overflow (Duplicate — P1, Delete).

**Section nav** (vertical list): Providers · Instructions & voice · Panel & capabilities · Tools · Knowledge; each with an icon and, after validation, a dot: `danger` for errors, `warning` for warnings; the active item uses `brand-soft` background. Keyboard: arrow keys move, Enter switches; switching is instant (no animation). Below 1024 px the nav is a horizontally scrollable segmented control pinned under the header with the same dots, and the rail becomes a "Summary" button opening a sheet.

**Summary rail** ("What this agent does"): pack name; pipeline as a vertical mini-flow (`VendorMark` + provider label + model label for STT → LLM → TTS, or the realtime model; optional avatar/image gen rows when set); capability icons with on/off; tools count ("4 built-in · 1 HTTP · 2 pack"); knowledge ("2 knowledge bases"); panel label; publish state with the public URL + copy when live; description (inline editable textarea, saves with the form); "Last saved 4 min ago". Every row links to its section.

**Save**: `⌘S`/`Ctrl+S` and the button. Save → PUT → validate → results distributed to sections (dots) and shown inside the active section as an "Issues" list at the top (`danger-soft`/`warning-soft` rows with the message and, where the heuristic finds a field name, a link that focuses the field). The validation banner above the tabs is removed. Unsaved-changes guard on navigation.

### 4.4 Choosing providers

The Providers section shows the pipeline as a **flow**, not a stack of forms:

1. **Mode** — two selectable cards (radio): "Cascaded" (icon: three linked dots) "Separate speech-to-text, language model and text-to-speech. Most flexible; works with LiveKit Inference without keys." · "Realtime" (icon: waveform) "One speech-to-speech model (Gemini Live, GPT Realtime). Lowest latency; can watch the camera live." Switching keeps the other mode's slots in form state (as today) and the payload nulls them on save (as today).
2. **Slot cards** in pipeline order with a thin connector between them: Speech-to-text → Language model → Text-to-speech (cascaded) or Realtime model. Each card, collapsed: `VendorMark`, provider label, model label (human) with the id in mono beneath at `caption` size, `CapabilityBadge`s (`vision` if the model `supports_video`; `realtime`; `tools` if `capabilities.tool_calling`; `silent-tools`; `voices n`), credential status (`no-key` "No key needed" for Inference; `key-set` "<label> · <fingerprint>" or `key-required` "Key required" in `warning` when own-key and none selected), and an "Edit" button. One card expands at a time.
3. **Expanded card**:
   - "Run it with": segmented choice **LiveKit Inference** (no key needed; the `livekit-inference-*` spec for the slot kind) vs **Your own key** (the vendor list). For the realtime slot there is no Inference option; the vendor list shows directly.
   - **Vendor** (own-key only): a list of vendor cards for `kind` from the registry, `status: "mvp"` selectable, `status: "deferred"` shown disabled with a "Coming soon" chip (resolves F-25); each shows label, `CapabilityBadge`s from `capabilities`, and links "Docs" / "Get a key" when `docs_url` / `get_key_url` exist.
   - **Model**: the combobox (search; rows = label, id mono, badges; "Use "<query>" as a custom id" row; default marked "Default"). Selected state shows label first.
   - **Credential** (own-key): `Select` of credentials for this provider (label · fingerprint) + "Add key" opening the credential sheet (§4.5); after a new key is saved it is selected and a "Test key" link appears.
   - **Options**: the registry fields via `RegistryForm` inside `Field`s (voice as a `Select` when the provider lists voices; language as a `Select` with a custom escape; temperature as a number with `inputMode="decimal"` and help). Fields are two columns on desktop.
4. **Optional**: Avatar (Beyond Presence / Tavus) and Image generation (Google / OpenAI) and the Workflow LLM appear as "Add avatar", "Add image generation", "Advanced: workflow model" buttons that create the slot card inline; a set optional slot shows a "Remove" action.
5. **Vision coherence**: when camera/screen share is on and the cascaded LLM is known text-only (`isKnownTextOnlyLlm`), the LLM card shows an inline `warning-soft` note "This model can't see images; pick one marked Vision to use the camera" with a link that opens the model combobox filtered to vision models.

Payloads are unchanged (`ProviderRef`). Vendor logos are not shipped (§8); `VendorMark` monograms carry the identity.

### 4.5 Credentials

`/console/credentials`: `PageHeader` "Credentials" + "Add credential". A list grouped by kind (Realtime, Speech-to-text, Language models, Text-to-speech, Avatars, Image generation, Embeddings, Tool secrets) using `ResponsiveTable`; row: `VendorMark` + label, provider label, fingerprint (mono, `CopyButton` — it is not a secret), "Used by n agents · m tools" (client-side join over `useAgents` `config.pipeline.*.credential_id` **and** `useTools()` definitions that bind an `http-tool-secret` credential; links to the agents list filtered), created `RelativeTime`, actions menu: **Test** (calls `POST /v1/credentials/{id}/test`; shows the `message` inline as a `StatusChip success/danger` for 10 s; avatar providers may return "not implemented" — show it as neutral), **Rotate** (opens the sheet in rotate mode: same fields, "Leave blank to keep" hints via `secretsMasked`; PUT with only the label when secrets are blank), **Rename**, **Delete** (confirm; on 409 show "Used by <agent names> — change those agents first" with links).

Credential sheet (right slide-over, 480 px; full-screen on mobile) used here and from the slot editor: provider (pre-filled and locked when opened from a slot; otherwise a vendor picker), label with a suggested value ("<Vendor> key · <month yyyy>"), the registry `secret_fields` as password inputs with show/hide, free-form NAME/value pairs for the secret bag; footer: "Save key" primary; help text "Encrypted at rest. Only a fingerprint is shown after saving." (already true). Validation inline, not as toasts.

### 4.6 Instructions and voice

Section "Instructions & voice", two `Section`s:

**Instructions**: a plain-text editor in Geist at 15/24, max 720 px wide, auto-growing to `60vh` then scrolling, with a footer bar: "n characters · ~n tokens", "Reset to pack default" (confirm dialog with a diff-free preview of the first lines), and — when `instructions_by_mode` exists in the pack manifest — a note "This pack has mode-specific instructions; the realtime variant is applied automatically". The `Field` hint: "Tell the agent who it is, how to speak and what it must never promise. Tools are described automatically."

**Voice**: Greeting (textarea, 2 rows, help "The first thing the agent says"); "How to greet": radio "Say it exactly" / "Let the model paraphrase" (with the auto-switch note for realtime models without TTS); Language (`Select`); Timezone (combobox over `Intl.supportedValuesOf("timeZone")`, default UTC, help "Used for the current-time tool and greetings"); "End the call when the caller is silent for" number + "seconds" suffix (`user_away_timeout_s`; "Never" when empty → null); "Let callers interrupt" switch with help "Turn off for read-aloud agents".

Copy table (old → new): "Greeting mode" → "How to greet"; "Say (TTS reads it verbatim)" → "Say it exactly"; "Generate (model paraphrases it)" → "Let the model paraphrase"; "User-away timeout (seconds)" → "End the call when the caller is silent for … seconds"; "Allow interruptions" → "Let callers interrupt".

### 4.7 Panel, tools and knowledge

**Panel & capabilities**: two panel cards (radio) from `PANEL_META` — "Session panel" (generic: "Status, notes, checklist, attachments and activity. Works with every pack.") and "Claim notebook" (insurance: "The adjuster's notebook: handwritten notes, taped photos, sketch and stamp. Needs the insurance pack's tools.") with the side/wide thumbnails; a "Custom panel id" collapsible for ids not in `KNOWN_PANEL_IDS`. Capabilities as a list (`divide-y`) with switch, title and consequence: "Camera — callers can turn on their camera; the agent sees frames when the model supports vision"; "Screen share — callers can share a window or tab"; "Typing — callers can type instead of speaking"; "Look at the latest frame every turn — sends the newest camera/screen frame with each reply (costs vision tokens)". The vision hint uses tokens and links to Providers.

**Tools**: `Section` "Built-in tools" as one list with switch + title + help, grouped: Conversation (End call, Current time), Knowledge (Search knowledge — disabled with "Attach a knowledge base first" when none), Vision (Describe current frame, Pin frame — disabled with "Turn on camera or screen share first"), Panel (Push note, Set status), Escalation (Escalate to human), Network (Make HTTP requests — with the help "Only to hosts allowed by the worker's `LKAP_HTTP_TOOL_ALLOWED_HOSTS`"). "Advanced" collapsible: "Tool steps per turn" with help. `Section` "HTTP tools" and "MCP servers": cards per tool (name mono, method + host, `StatusChip` for disabled/shared, attached switch labelled "Use in this agent", actions: Dry run, Edit, Remove) with proper empty states and "Add HTTP tool" / "Add MCP server" opening sheets. `Section` "Pack tools": read-only list, mono names, "Provided by the <pack> pack".

**Knowledge (section)**: `Section` "Attached knowledge bases": search box + list of all KBs with switch "Use in this agent", name, "n documents · n chunks", embedder label; empty state links to `/console/knowledge`. `Section` "Retrieval": "Add the best matches to every turn" switch; "Matches per turn" number (1–10) with help "Higher finds more but costs tokens".

**Knowledge pages**: list per §7.7; detail: header (name, description, embedder label, counts), drop zone ("Drop .md, .txt or .pdf files here, or browse"; per-file progress; "Indexing…" chips; failed rows with the error and "Try again"), "Try a question" search with highlighted results and an empty state "No matches — try different words or upload more documents".

### 4.8 Test call

- **P0 — new tab, done right.** Header split button "Test call": primary click opens `/s/<slug>?mode=test` in a new tab; when the form is dirty it instead shows a popover "You have unsaved changes" with "Save and test" (primary) and "Test the saved version". Menu items: "Copy test link", "Open public page" (published only). The session page in test mode shows the slim test bar with "Back to editor".
- **P1 — side-by-side drawer.** A right drawer (`Sheet`, 480 px, resizable to 50 %) embedding `<iframe src="/s/<slug>?mode=test&embed=1" allow="microphone; camera; display-capture; autoplay">`. `embed=1` hides the test bar and uses the mobile layout inside the iframe. Bundles stay separate (the iframe loads the session route). Drawer header: agent state (mirrored via `postMessage` from the iframe, optional), "Open in new tab", "End". Sessions created this way appear in Sessions as usual.

### 4.9 Publishing

The `Switch` is removed everywhere. Header button "Publish" opens a popover. If the form is dirty the popover first offers "Save and publish" (primary) and "Publish the saved version", because `/validate` checks the *saved* config. Then: "Publishing makes `/s/<slug>` answer calls from anyone with the link." with the validation result (runs `POST /validate` on open; errors block with "Fix n issues first" and a link to the first section; warnings list with "Publish anyway"), the public URL with `CopyButton`, a QR code (P1, console-only dependency), and "Publish" (`brand`). Once live the button reads "Unpublish" and its popover shows the URL, copy, "Open page", and "Unpublish" (destructive with confirm: "The public link stops answering immediately."). The agents list offers Publish/Unpublish in the row menu with the same popover content in a dialog. Toasts: "Published" / "Unpublished".

### 4.10 Session history and detail

List: filters as chips and selects (agent by name, status, range), columns per §7.8; failed rows swept by the stale sweep stay muted with the "never started" chip; rows link; 25 per page.

Detail: header (agent → link, status chip, "Started 19 Sep, 03:04 (4 min ago)", "Duration 1 m 57 s", mode chip, "Config v1", room id mono + copy); stats strip (`DescriptionList`: turns, tool calls, errors, and the usage dict rendered as label/value pairs — a nested list such as `model_usage` renders as a sub-list of provider/model rows, never raw JSON); tabs: **Timeline** (default) · **Transcript** · **Panel at end of call** · **Raw events** (JSON, for engineers).

Timeline: one column; rows sorted by `toMillis`; a minute marker row when the minute changes; turn rows (speaker chip "You"/"<Agent>", text, interrupted chip); tool rows (icon `wrench`, tool name mono, status chip, duration, "Details" disclosure with args/result preview); state track (collapsed by default into a thin row "Listening → Thinking → Speaking ×12"; expandable); escalation/error rows highlighted; filter chips. Times shown as `mm:ss` from session start (`started_at`, falling back to `created_at` when null, e.g. swept sessions), with the wall-clock time in the row `title`.

### 4.11 Settings

Appearance: theme radio (Light / Dark / System) with a live preview strip; density (P1). Environment (read-only from `/v1/health`): version, LiveKit URL host, packs loaded (chips), database status, API origin; "Copy diagnostics" button. About: links to Architecture, Contracts, Runbook.

## 5. Session experience redesign

The session surface is for end users on phones and laptops; nothing on it may assume builder knowledge. It is dark by default (`.dark` + `data-surface="session"`), 16 px body, 40 px+ hit targets, safe-area aware. The state vocabulary is `Connecting · Listening · Thinking · Speaking · Reconnecting · Ended` (`AGENT_STATE_LABEL` in `components/shared/agent-state.ts`, WP-0), and the `StateMeter` is the agent's face whenever there is no avatar video.

### 5.1 Pre-call

Layout (desktop): a two-column card 720 px wide, `radius-2xl`, `card` on `background` with the stage well (`stage`) as the left column and the form as the right; on mobile a single column, the stage well on top (160 px). No "Voice agent" badge.

Left (stage well): the `StateMeter lg` in `idle`, the agent name in display type, the agent description (if any; else nothing), and a three-item "What to expect" list derived from capabilities and pipeline mode: "<Agent> speaks first and listens while you talk" · "You can turn on your camera to show something" (if `camera`) · "You can share your screen" (if `screen_share`) · "You can also type" (if `chat_input`). Each with the capability icon at `md`.

Right (form):
1. **Your name** — input, default empty with placeholder "Guest"; the connect request still sends "Guest" when empty.
2. **Microphone** — a device check block: a `Select` of audio inputs (populated after permission), a live level meter (the `StateMeter` bars driven by an `AnalyserNode`, reusing the same geometry), and a status line: *idle* "We'll ask for your microphone when you start" with a secondary button "Check microphone"; *requesting* "Waiting for permission…" (button disabled, spinner); *granted* "Microphone works" + level meter; *denied* "Microphone is blocked. Allow it in your browser's site settings, then reload." with a per-browser hint (Chrome: lock icon → Site settings; Safari: Safari › Settings for this website; iOS: Settings › Safari › Microphone) and a "Reload" button; *unsupported* "This browser can't capture audio. Try Chrome, Safari or Firefox." Permission is never requested on page load; it is requested on "Check microphone" or on "Start call" (whichever comes first). The mic-check hook stops its tracks before the room starts publishing. The chosen input device must reach the room: write it to the same persisted user-choice the vendored control bar reads (implementer verifies the storage key/shape in `hooks/agents-ui/use-agent-control-bar.ts` / `@livekit/components-react` `usePersistentUserChoices`), or pass it as the room's audio capture default if `useSession`/`start()` options allow it — verify in `node_modules`, do not assert.
3. **Start call** — `Button variant="brand" size="xl"` full width, icon `phone` at `xl`, label "Start call"; disabled with "Waiting for permission…" while requesting; the click is the user gesture that also primes audio playback (§5.2).
4. Fine print (caption, muted): "Your microphone is on during the call. You can mute or hang up any time." A "Privacy" link if the deployment sets `NEXT_PUBLIC_LKAP_PRIVACY_URL` (optional).

Test mode: no badge in the card. A slim bar above the card (and above the in-call shell): `StateMeter xs` + "Test call · this agent is a draft" + "Back to editor" link (`/console/agents/<id>?section=providers` requires the id; `AgentPublicOut` carries `id`, so the link is `/console/agents/${agent.id}`). Errors from a previous attempt render inline above the Start button in `danger-soft`, not in red text.

### 5.2 Audio playback ("Enable sound")

Browsers block audio output until a user gesture. The Start click is that gesture, so the implementation must use it:
- **Preferred:** create an `AudioContext` in the Start click handler, `await ctx.resume()`, and hand it to the room so LiveKit's web-audio mix uses it (implementer checks the `useSession` / `Room` options in `node_modules/@livekit/components-react@2.9.24` and `livekit-client@2.22.3` for `roomOptions.webAudioMix.audioContext` or an equivalent; the API shape is not asserted here).
- **Fallback:** call `session.room.startAudio()` immediately after connect; if `room.canPlaybackAudio` remains false, the stage shows a full-well overlay: `StateMeter` muted + "Tap to hear <agent>" as a `brand` button (wired to `StartAudioButton`'s merged props). It is impossible to miss and disappears once audio plays. The transcript header no longer hosts the button.
- Also: the stage caption reads "Muted by your browser — tap to enable sound" while blocked, and the agent's transcript keeps flowing so the user sees it is alive.

### 5.3 In-call layout

Shared elements:
- **Top strip** (40 px, transparent on `background`): left — agent name + `StatusChip live` when connected; center — elapsed timer (mono, tabular) once connected; right — connection state chip only when not "connected" (Connecting… / Reconnecting…). Test-mode bar sits above it.
- **Stage** (`stage` surface, `radius-xl`): avatar video (`object-cover`, letterboxed on `stage`) or `StateMeter lg` + agent name + state label in caption; self-view PiP bottom-right (`radius-md`, 1 px `border`, `shadow-md`, "You"/"Screen" chip), 144 px wide on desktop, 96 px on mobile, tappable to enlarge to 50 % width; the audio-blocked overlay (§5.2); an error overlay when the agent failed to join (§5.4).
- **Transcript**: LiveKit's `AgentChatTranscript` (vendored) inside our container; header "Transcript" + count; empty state "Say hello — the transcript appears here"; chat input is the control bar's built-in reveal.
- **Panel**: the pack panel in its own column with a header (panel title from `PanelDefinition.title`, plus for the notebook the claim status chip mirrored from `state.status`).
- **Control bar**: LiveKit's `AgentControlBar variant="livekit"` (pill), restyled from the wrapper (§5.4), always visible.

Desktop (≥ 1024 px), `layout: "side"` (generic): grid `minmax(0,1fr) 400px`; left column = stage (flex-1, min 320 px) above transcript (min 200 px, flex-1); right = panel; control bar centered under the left column, `max-w-2xl`.

Desktop, `layout: "wide"` (notebook): grid `340px minmax(0,1fr)`; left rail = compact stage (200 px tall, avatar or meter) above the transcript (flex-1); right = the panel filling the column with its own scroll; control bar centered under the *left rail* (it belongs with the call, not the paper). The notebook's own two-column desk (paper + cards) keeps its container queries.

Tablet (768–1023 px): both layouts become a single column with the panel first for `wide` (the notebook is the work surface) and the stage first for `side`; the transcript is a bottom sheet; controls sticky.

Mobile (< 768 px), both layouts:
- Body is `min-h-[100dvh]` with `padding-bottom: calc(88px + env(safe-area-inset-bottom))` reserved for the control bar.
- `wide`: top strip → a compact stage strip 72 px tall (meter or a 16:9 avatar thumbnail that expands to full width on tap, with the self-view thumbnail beside it when the camera is on) → the panel as the main scroll region → sticky control bar (`position: fixed; bottom: env(safe-area-inset-bottom)`) → transcript as a bottom sheet (Vaul-style drag handle, 60 vh) opened by the chat toggle or by tapping the "n messages" chip in the strip.
- `side`: top strip → stage 45 vh (avatar/meter + PiP) → panel below in the scroll region → same sticky controls and transcript sheet.
- When the camera or screen share is on, the self-view is promoted into the stage strip as a 96 px thumbnail; tapping it swaps stage and self-view (so the claimant can see what the agent sees).
- The chat input reveal in the control bar pushes content up rather than covering the panel (the bar is `fixed`; the page gets extra bottom padding while chat is open).

### 5.4 Connection and agent states

State model (in `session-room.tsx` → `StageView.agentState`): `connecting` (room connecting, or connected but agent not yet `listening`/`speaking`), `listening`, `thinking`, `speaking`, `reconnecting` (room `reconnecting`), `failed` (agent `failed`, or connect error), `ended` (room `disconnected` after having connected).

| State | Stage | Top strip | Banner | Controls |
|---|---|---|---|---|
| connecting | meter `connecting` (sequential pulse), caption "Connecting…" | "Connecting…" chip | none | mic enabled, others disabled |
| listening | meter `listening` (or LiveKit bars on audio), caption "Listening" | live chip + timer | none | all |
| thinking | meter `thinking` (sweep), caption "Thinking…" | same | none | all |
| speaking | LiveKit bar visualizer driven by the audio track (same geometry as the meter), caption "Speaking" | same | none | all |
| reconnecting | stage media (video, meter) dims to 60 %, meter `connecting`, caption "Reconnecting…" at full contrast (R-V2-19: WCAG 1.4.3; the agent name, caption and elapsed chip are never dimmed) | "Reconnecting…" chip (warning) | slim warning banner "Connection lost — reconnecting" (no button) | mic stays; others disabled |
| failed | overlay in `danger-soft`: "<Agent> couldn't join the call" + reason if any (from `failureReasons`) + "Try again" (`brand`) and "Leave" (secondary) | "Failed" chip | none (overlay is the message) | only Leave |
| ended | transition to the end-of-call card | — | — | — |

Device errors (`onDeviceError`): toast top-center "Couldn't start your camera — check permissions" *and* the corresponding control shows a danger dot until the next successful toggle. Screen share on a phone: the control is hidden (unsupported), not disabled.

Control bar restyle (no fork): pass `className` to `AgentControlBar` with descendant arbitrary variants, e.g. `[&_button[data-state=on]]:bg-brand-soft! [&_button[data-state=on]]:text-brand-text! [&_button[data-state=on]]:border-brand-line!` to replace the hard-coded blue; keep the pill radius; the "END CALL" button gets `[&_[data-slot=disconnect]]`-style overrides only if its contrast fails the 4.5:1 check (measure first). Icons inside remain Lucide.

`open_dialog` and `focus` UI requests: the session room forwards them to the panel through a `panelRequestRef` that a panel may register via a new optional `PanelDefinition.handleRequest?: (req) => UiRequestResult` (registry file owned by WP-9; the notebook registers `open_dialog` → opens the packet dialog; the exact `payload` shape must be verified against the insurance pack's `ui.request` emitter in `packs/insurance_claim`, it is not asserted here). This adds an optional field to `PanelDefinition`; CONTRACTS §11 documents that interface, so it is a docs-only amendment listed in §7.14 (contracts owner). WP-9 lands the one-line type on day one so WP-8 can code against it; until a panel registers a handler the room keeps declining politely.

### 5.5 Panels inside the new shell

- Generic panel: sections (Status, Notes, Still needed, Attachments, Activity, Pack data) with `h3` caption titles and `divide-y`; status as `StatusChip`; progress as a 4 px `brand` bar; activity rows with `StateMeter xs` for running items; assets in a 2-col grid with `radius-md`.
- Notebook: unchanged paper. Desk cards adopt the shell's card look (`bg-card border rounded-lg`, `h3` caption title, token colors). The `%` ring keeps its conic gradient with `--ring-tone` from tokens (`success`/`warning`/`danger`) and `var(--color-card)` inner disc. "Still needed" items use the shared checkbox glyph. "Claim team" rows show `StateMeter xs` while running. The sketch confirmation is a `brand` button. The packet is a dialog on desktop and a full-height sheet on mobile.
- Mobile notebook: paper padding 16 px, notes 24/31 px, stamp scaled and placed so it never covers the last line (bottom padding = stamp height), polaroids full width.

### 5.6 End of call

Card (`radius-2xl`, centered, max 480 px): `StateMeter` in `ended`, "Call ended" (display), "You talked with <agent> for 4 min 12 s." (duration from the timer), then actions: "Start another call" (`brand`) and, in test mode, "Back to editor" (secondary). If the pack exposed a handoff (`custom.handoff` non-empty in the last `UiState`), a single line "An adjuster will be in touch" is *not* invented here — the end card shows nothing pack-specific this pass (deferred: pack-provided end-of-call summary). Optional: "How did that go?" thumbs up/down that only logs client-side is **out** (no endpoint).

### 5.7 Unavailable pages

Same card shell, three variants keyed by `classifyConnectError`:
- `not_found`: title "There's no agent at this address", body "Check the link you were given, or ask whoever sent it for a new one.", no actions (a "Home" link in the footer).
- `not_published`: title "This agent isn't live yet", body "It exists, but it hasn't been published. If you're setting it up, open it in test mode from the console.", actions: "Open in console" → `/console/agents` (we only know the slug; the list page's search pre-fills `?q=<slug>`).
- `unreachable`: title "We couldn't reach the service", body "Try again in a moment.", action "Try again" (reloads).

Console `not-found`: inside the shell, "That page doesn't exist" + links to Overview/Agents/Sessions.

## 6. States: loading, empty, error, success, validation

- **Loading.** Skeletons that match the layout they replace (rows for lists, a header + three blocks for detail pages, the pipeline flow for Providers) using `Skeleton` on `muted`; never a centered spinner for page loads. Inline spinners only inside buttons ("Saving…") and status chips ("Indexing…"). Perceived speed: skeletons appear immediately; data replaces them without layout shift (fixed row heights).
- **Empty.** `EmptyState` per §4.1 — icon, title (what appears here), one sentence (why), one primary action; a secondary link at most. In-section empties (no HTTP tools, no notes yet) use the compact variant: one line + inline action.
- **Errors.** Three channels, chosen by scope: (1) **Field**: inline under the control in `danger-text` with the control in `aria-invalid`; never a toast. (2) **Section/page**: an `Alert` in `danger-soft` at the top of the affected region with a retry when the action is retryable (`ErrorBanner` restyled); network failures on lists render here. (3) **Transient action failures** (save failed, delete failed): a toast with the API message and, when the error has `details`, a "Details" action opening a dialog. Copy: "Couldn't save — <api message>" — direct, no apology, no exclamation.
- **Success.** Toast, bottom-right (console) / top-center (session), 4 s, same verb as the action ("Saved", "Published", "Credential added", "Uploaded 3 files"). Destructive confirmations succeed with a toast and, where sensible, an "Undo" only if the API supports it (it does not; so no Undo).
- **Validation.** Client-side (zod) errors are field-level and appear on blur or on submit; the first invalid field receives focus. Server validation (`ValidationResult`) is distributed to sections (dots) and listed inside the section; the `ok && no warnings` case shows a quiet `success-soft` line "Configuration looks good" under the header for 6 s, not a permanent banner.
- **Confirmations.** Destructive actions use `ConfirmDialog` with the object's name in the title and the consequence in the body; the confirm button is `destructive` and says the verb ("Delete agent", "Unpublish").
- **Long operations.** KB indexing polls (as today) with "Indexing…" chips and a progress bar per file while uploading; credential test shows an inline spinner in the row for up to 10 s then "Timed out — try again".
- **Offline / API down.** The console's query provider surfaces a single top `Alert` "Can't reach the API" with retry when three consecutive queries fail; individual sections do not each show their own banner in that case (dedupe by a `useApiHealthBanner` in the shell, WP-1).
- **Live regions.** Toasts (`sonner` handles), the session stage caption (`aria-live="polite"`), the pre-call device status, the notebook stamp (already).

## 7. Work packages

### 7.0 Rules for every package

- **Gates** (run from `web/`): `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`. All green before hand-off. Test count today: 181 across 13 files; every package that deletes or rewrites a test replaces it with at least as many assertions of equal or better value, and adds tests for what it builds. Coverage must not drop.
- **Never run `shadcn init`.** Only `pnpm dlx shadcn@latest add <component>` (WP-0 only; other packages ask WP-0's owner or the integrator for additions).
- **Do not edit** `web/src/app/layout.tsx` (W0 rule), `web/src/components/agents-ui/**` and `web/src/hooks/agents-ui/**` (vendored; restyle from wrappers), `web/src/contracts/**` (generated).
- **File ownership is exclusive.** The table in §7.13 assigns every existing file and every new directory to exactly one package. If you need a change in a file you do not own, write the request in your hand-off note; WP-12 (integrator) applies it.
- **Screenshots.** Every package runs the capture script (extended by WP-10; until it lands, the original `scratchpad/ui-audit/capture.mjs` + a per-package ad-hoc list) at desktop 1440×900 and mobile 390×844 for the routes it owns and saves them to `scratchpad/ui-audit/after/<wp>/`. Fable reviews the after set; findings come back as a short punch list.
- **Bundle budget.** `pnpm build` prints the route table. `/s/[slug]` First Load JS ≤ 400 kB (today 383 kB); console routes report their size in the hand-off note; the session bundle must not import anything from `components/console/**` or `panels/**` beyond what it imports today.
- **Accessibility floor.** Every new interactive element: keyboard reachable, visible focus ring (§2.4), name/role/value via native elements or ARIA, labels bound with `htmlFor`/`aria-labelledby`, `aria-live` for async status, `prefers-reduced-motion` honoured. Touch targets per §2.4.
- **Copy.** Sentence case, plain verbs, no exclamation marks, no "Oops", no leaked implementation ("Matches a pack's ui_panel_id"). Errors say what happened and what to do.
- **Hand-off note** (in the PR/commit message or `scratchpad/ui-audit/after/<wp>/NOTES.md`): what changed, gate output summary, bundle sizes, screenshots list, requests for files you do not own, anything deviating from this spec and why.

### 7.1 WP-0 — Foundation: tokens, theme, shared primitives (Opus; first; blocking)

**Scope.** Everything in §2, delivered as code that others build on.
1. `globals.css`: the token set of §2.2 (both themes), the `@theme inline` mapping of §2.7, base layer rules (`body` background/foreground, `[data-surface=session] { color-scheme: dark }`, focus-visible defaults, `text-wrap` defaults for headings, reduced-motion global rule for `.motion-safe` helpers).
2. Theme: `components/console/shell/theme-provider.tsx` (a thin `next-themes` provider with the settings of §2.2; exported for `console/layout.tsx` which WP-1 owns) and `lib/theme.ts` (`THEME_OPTIONS`, `useThemePreference`).
3. shadcn additions listed in §2.7; review each generated file for hard-coded colors; switch `button.tsx` press feedback to `active:scale-[0.98]` with `--dur-1`; add `size="xl"` (h-11, session Start call) and a `variant="brand"` (brand bg, brand-foreground) to `button.tsx`; add `tone` variants to `badge.tsx` mapped to tokens (keep existing variants for agents-ui).
4. Shared primitives in `components/shared/` with the props of §2.7 (`StateMeter`, `StatusChip`, `Icon`, `EmptyState`, `PageHeader`, `Section`, `Field`, `DescriptionList`, `CopyButton`, `RelativeTime`, `VendorMark`, `CapabilityBadge`, `Kbd`, `ResponsiveTable`) plus `components/shared/agent-state.ts` exporting `AgentUiState` and `AGENT_STATE_LABEL` (the single state vocabulary, importable from both surfaces). Restyle the existing `components/console/shared/*` (`error-banner`, `validation-banner`, `confirm-dialog`, and re-export `empty-state`/`page-header` from the shared ones so existing imports keep working).
5. `lib/format.ts` per §2.7.
6. `components/console/lib/api-hooks.ts`: add `useHealth()` (`api.get<HealthResponse>("health")`, `staleTime 30 s`), `useCredentials()` unchanged plus `useUpdateCredential()`, `useTestCredential()` (`POST credentials/{id}/test`), `useSessions` gains an optional `limit` applied client-side (slice) so the overview can ask for 8. Nothing else changes signature.
7. `components/console/lib/constants.ts`: replace `TONE_BADGE_CLASSES` with token classes; add `PANEL_META: Record<string, {label, description, layout}>` for `generic` and `insurance_notebook`; add `CAPABILITY_META` (label, description, icon name) for camera / screen_share / chat_input / vision_inject_per_turn; (`AGENT_STATE_LABEL` does **not** live here — see `components/shared/agent-state.ts` below, because the session bundle must never import `components/console/**`).
8. `package.json`: remove `@phosphor-icons/react`; add `culori` (dev) for the contrast script; `pnpm install`.
9. `scripts/check-contrast.mjs`: parses `globals.css` token values for both themes, resolves `var()` chains, composites alpha over `--background` and `--card`, computes WCAG ratios for the pairs listed in §2.2, exits non-zero on failure; wire as `pnpm check:contrast` and run it in `pnpm lint` (chain) or document it as a gate in the README.
10. Tests: `tests/shared-primitives.test.tsx` (StateMeter states/labels, StatusChip tones, Field error wiring, CopyButton, ResponsiveTable switches at width), `tests/format.test.ts` (every helper, including `toMillis` for ISO / seconds / ms).

**Files owned.** `web/src/app/globals.css`, `web/package.json`, `web/pnpm-lock.yaml`, `web/components.json` (read-only unless a registry alias is needed), `web/src/components/ui/**`, `web/src/components/shared/**` (new), `web/src/components/console/shared/**`, `web/src/components/console/shell/theme-provider.tsx` (new), `web/src/components/console/lib/api-hooks.ts`, `web/src/components/console/lib/constants.ts`, `web/src/lib/theme.ts` (new), `web/src/lib/format.ts` (new), `web/src/lib/utils.ts`, `web/scripts/check-contrast.mjs` (new), `web/tests/shared-primitives.test.tsx` (new), `web/tests/format.test.ts` (new).

**Acceptance.** Gates green; `pnpm check:contrast` passes for every pair in §2.2 in both themes; `@phosphor-icons/react` gone; a storybook-free demo is not required, but `/console/preview/panels` (WP-10) will render every primitive in every state, so export them cleanly; existing tests untouched and green; hand-off note lists the exact exports and any prop deviations from §2.7.

**Verification.** Gates + contrast script; screenshots of the existing console (no visual redesign yet beyond tokens) to confirm nothing regressed; bundle sizes unchanged within 2 %.

### 7.2 WP-1 — Console shell, IA, overview, settings (Sonnet; wave A)

**Scope.** §3 and §4.1.
1. `app/console/layout.tsx`: mount `ThemeProvider` (from WP-0), `SidebarProvider` + `AppSidebar`, a top bar (breadcrumbs, page actions slot, theme menu, "Open session page" link when on an agent), `Toaster` bottom-right, `metadata` with title template `%s · LKAP console`. Skip-to-content link as the first focusable element.
2. `components/console/shell/`: `app-sidebar.tsx` (wordmark with idle `StateMeter`, nav items with icons, active state = brand-soft background + brand-text icon, collapsed rail with tooltips, keyboard shortcuts `g a` / `g s` … optional), `mobile-nav.tsx` (shadcn sidebar's sheet mode at < 1024 px; a top bar with menu button + breadcrumb), `breadcrumbs.tsx`, `theme-menu.tsx` (dropdown with Light / Dark / System radio items; no sun/moon toggle), `command-palette.tsx` (P1: ⌘K with routes + agents by name; no open/close animation).
3. Route move: `app/console/page.tsx` becomes the Overview; `app/console/agents/page.tsx` (new) renders `AgentsTable` from WP-2 with `PageHeader` and the "New agent" link to `/console/agents/new`. Update every hard-coded `/console` reference that meant "agents list": `nav.tsx` (deleted), `app/page.tsx` (WP-11 owns; request), `agent-editor.tsx` `router.push("/console")` → `/console/agents` (WP-3 owns; request), the capture script (WP-10 owns; request). Add a `redirect` is **not** needed (`/console` still exists as overview).
4. Overview (`components/console/overview/*`): the §4.1 layout — status line, setup checklist driven by `useHealth`, `useAgents`, `useCredentials`, `useSessions`; recent sessions (8, `RelativeTime`, `StatusChip`); "live now" row (agents with `published`); quick actions. No hero metric tiles.
5. Settings (`app/console/settings/page.tsx`, `components/console/settings/*`): appearance (theme radio group), environment (from `useHealth`: version, LiveKit URL host, packs loaded, db status), "About" (links to docs/RUNBOOK). Read-only except theme.
6. `app/console/not-found.tsx` is WP-11's; WP-1 ensures the layout renders it inside the shell.
7. Tests: `tests/console-shell.test.tsx` (nav renders 6 items, active state by pathname, theme menu changes `next-themes` value, mobile sheet opens/closes, skip link exists), `tests/console-overview.test.tsx` (checklist states from mocked hooks; empty vs populated).

**Files owned.** `web/src/app/console/layout.tsx`, `web/src/app/console/page.tsx`, `web/src/app/console/agents/page.tsx` (new), `web/src/app/console/settings/**` (new), `web/src/components/console/nav.tsx` (delete), `web/src/components/console/shell/**` except `theme-provider.tsx`, `web/src/components/console/overview/**` (new), `web/src/components/console/settings/**` (new), `web/src/components/console/lib/query-provider.tsx`, `web/tests/console-shell.test.tsx`, `web/tests/console-overview.test.tsx`.

**Depends on.** WP-0 (tokens, `ThemeProvider`, `StateMeter`, `StatusChip`, `useHealth`, sidebar component). Renders WP-2's `AgentsTable` (existing export name kept).

**Acceptance.** Sidebar per §3 at ≥ 1024 px with collapse; sheet nav below; no horizontal scroll at 390 px on any console route; theme persists across reload; overview checklist reflects real API state; `/console/agents` lists agents; keyboard: Tab order sidebar → topbar → main; skip link works.

**Verification.** Gates; screenshots `desktop/mobile-console-overview`, `-settings`, `-agents` (light and dark); axe (via the capture script's axe pass, WP-10) shows no serious/critical violations.

### 7.3 WP-2 — Agents list and new-agent flow (Sonnet; wave A)

**Scope.** §4.2 and the agents list of §3.
1. `agents-table.tsx` → keeps the `AgentsTable` export; uses `ResponsiveTable`; columns: Agent (name, slug mono, pack chip), Pipeline (mode + provider vendors as `VendorMark`s), Status (`StatusChip` Live/Draft; no toggle in the table), Last updated (`RelativeTime`), row actions in a `DropdownMenu` (Open, Test call ↗, Copy public link (published only), Publish/Unpublish with confirm, Delete with confirm). Search box (name/slug), filter chips (All · Live · Draft · by pack), sort by updated. Row click opens the editor; the whole row is a link target with focus ring.
2. Delete copy: "Agents that have sessions can't be deleted — sessions are kept for the audit trail. Unpublish it instead." (F-22).
3. `app/console/agents/new/page.tsx` + `components/console/agents/create/*`: the §4.2 guided flow (pack template cards from `usePacks`, name + description, then create with `config: null` and route to the editor's Providers section with a "Created from <pack>" toast).
4. Remove `create-agent-dialog.tsx` (replace usages with links to `/console/agents/new`; WP-1's page and the overview link there).
5. Tests: `tests/console-agents-list.test.tsx` (renders rows, filters, actions menu, mobile card mode), `tests/console-create-agent.test.tsx` (pack cards from mocked packs, validation of name, submit payload).

**Files owned.** `web/src/components/console/agents/agents-table.tsx`, `web/src/components/console/agents/create-agent-dialog.tsx` (delete), `web/src/components/console/agents/create/**` (new), `web/src/app/console/agents/new/**` (new), `web/tests/console-agents-list.test.tsx`, `web/tests/console-create-agent.test.tsx`.

**Depends on.** WP-0. WP-1 renders the list page.

**Acceptance.** List usable at 390 px (cards, actions reachable); search/filter client-side; create flow lands in the editor; no `Switch` inside table rows; empty state per §6.

**Verification.** Gates; screenshots `console-agents` (populated + empty), `console-agents-new` (desktop/mobile).

### 7.4 WP-3 — Agent editor shell, summary rail, publish, test call (Opus; wave A)

**Scope.** §4.3, §4.8, §4.9 (publishing), §6 (validation display).
1. `agent-editor.tsx` + `components/console/agents/editor/*`: `editor-shell.tsx` (sticky header: back link, name as a real title with an "Edit name" pencil that turns it into an input, slug + copy, `StatusChip` Draft/Live, unsaved indicator, actions: Test call (menu), Publish/Unpublish (popover), Save (primary, ⌘S), overflow menu with Delete), `section-nav.tsx` (vertical on ≥ 1024 px, horizontal scrollable segmented control below; each item shows a validation dot: none / warning / error; deep-linkable via `?section=`), `summary-rail.tsx` (§4.3), `publish-popover.tsx` (§4.9), `test-call-menu.tsx` (§4.8; P1 `test-call-drawer.tsx` iframe), `validation-map.ts` (heuristic mapping of `ValidationResult.errors/warnings` strings to sections + a `useSectionIssues(section)` context consumed by WP-4/WP-5 tabs to render an inline issue list at the top of the section), `unsaved-guard.tsx` (`beforeunload` + in-app navigation confirm when `formState.isDirty`).
2. Save flow: "Save" saves, then validates; the banner is replaced by per-section dots + an issue list inside the section; the toast says "Saved" (or "Saved with 2 warnings"). Name/description editing moves into the header (name) and the summary rail (description, inline editable).
3. Sections keep their existing component files and `useFormContext<AgentEditorForm>()` (WP-4/WP-5 own those files); the shell imports them from a `sections.ts` registry: `[{id:"providers", label:"Providers", Component: ProvidersTab, icon}, …]`.
4. `schemas.ts`, `defaults.ts`, `form-errors.ts`, `zod-resolver.ts` stay functionally the same; only messages are reviewed for tone.
5. Tests: keep `console-zod-resolver.test.ts`; add `tests/console-editor-shell.test.tsx` (header actions, dirty state, section switching via nav and `?section=`, unsaved guard), `tests/validation-map.test.ts` (string → section mapping incl. unknown → general).

**Files owned.** `web/src/components/console/agents/agent-editor.tsx`, `web/src/components/console/agents/editor/**` (new), `web/src/components/console/agents/defaults.ts`, `web/src/components/console/lib/schemas.ts`, `web/src/components/console/lib/form-errors.ts`, `web/src/components/console/lib/zod-resolver.ts`, `web/src/app/console/agents/[id]/page.tsx`, `web/tests/console-zod-resolver.test.ts`, `web/tests/console-editor-shell.test.tsx`, `web/tests/validation-map.test.ts`.

**Depends on.** WP-0. Provides `useSectionIssues` to WP-4/WP-5 (they may call it behind an optional-context guard so their tests do not need the shell).

**Acceptance.** No two horizontal pill rows; header sticky; ⌘S saves; publish requires a passing validation (errors block, warnings allowed with an explicit "Publish anyway"); public link copyable; test call opens `/s/<slug>?mode=test` in a new tab and, when dirty, offers "Save and test"; at 390 px the section nav is a scrollable segmented control and the summary rail is a "Summary" sheet.

**Verification.** Gates; screenshots of each section at both sizes with the new shell (the section *content* is WP-4/WP-5's; if they are not merged yet, screenshot with the old content and say so).

### 7.5 WP-4 — Providers, models, credentials (Opus; wave A)

**Scope.** §4.4 and §4.5.
1. `providers-tab.tsx`: the pipeline as a flow (§4.4): mode choice as two descriptive cards; slot cards in pipeline order with a `VendorMark`, the chosen provider/model, `CapabilityBadge`s, credential status, and an "Edit" that expands the slot inline (one open at a time, `Collapsible`). Optional slots (Avatar, Image generation, Workflow LLM) grouped under "Optional" with "Add" buttons instead of "None" selects.
2. `provider-slot-editor.tsx`: step 1 "How to run it": *LiveKit Inference (no key needed)* vs *Your own key*; step 2 vendor list (cards with `VendorMark`, label, capability badges; `status === "deferred"` entries shown disabled with "Coming soon" — resolves F-25); step 3 model picker; step 4 credential picker (own-key only); step 5 registry fields (`RegistryForm`).
3. `model-combobox.tsx`: shadcn `Command` inside a `Popover`: search, grouped suggestions with label, id (mono) and badges (`vision` from `supports_video`, note), "Use custom id" row when the query matches nothing (free text must always work — CONTRACTS §6 makes unknown models a warning, not an error), selected model shows label first, id second.
4. `registry-form.tsx`: `Field` wrapper; a `voice` field renders a `Select` when the selected provider's `capabilities.voices` is non-empty (with a "Custom…" escape); `language` renders a `Select` of the common BCP-47 codes with free text escape; `number` fields get `inputMode="decimal"`; `json` gets a mono textarea with a "Format" button.
5. `credential-picker.tsx` + `create-credential-dialog.tsx` → a `Sheet` (slide-over) `credential-sheet.tsx` used from the slot editor and from the credentials page; keeps posting `CredentialCreate` only; after save, offers "Test key" (`useTestCredential`) with the result inline.
6. Credentials page: `app/console/credentials/page.tsx`, `components/console/credentials/*` (§4.5): grouped by provider kind, each row: label, `VendorMark`, fingerprint (mono), created, "Used by n agents" (client-side join over `useAgents`), actions: Test, Rotate (PUT with new secrets), Rename, Delete (409 → explain which agents use it).
7. Tests: keep and update `console-credential-dialog.test.tsx` (now the sheet), `console-registry-form.test.tsx`; add `tests/console-provider-slot.test.tsx` (Inference vs own key switch, deferred providers disabled, model combobox custom id, voice select from capabilities), `tests/console-credentials-page.test.tsx`.

**Files owned.** `web/src/components/console/registry/**`, `web/src/components/console/agents/tabs/providers-tab.tsx`, `web/src/components/console/credentials/**` (new), `web/src/app/console/credentials/**` (new), `web/tests/console-credential-dialog.test.tsx`, `web/tests/console-registry-form.test.tsx`, `web/tests/console-provider-slot.test.tsx`, `web/tests/console-credentials-page.test.tsx`.

**Depends on.** WP-0 (`Command`, `Popover`, `Sheet`, `Collapsible`, `VendorMark`, `CapabilityBadge`, `Field`, `useTestCredential`, `useUpdateCredential`). Optional: WP-3's `useSectionIssues`.

**Acceptance.** A new user can see at a glance what runs where and whether a key is missing; model ids are never the primary label; deferred providers visible but disabled; the credential sheet never displays a secret after save; the page works at 390 px; `ProviderRef` payloads unchanged (contract intact).

**Verification.** Gates; screenshots `editor-providers` (cascaded + realtime, one slot expanded), `console-credentials` (populated + empty), credential sheet open, model combobox open (desktop), all at both sizes.

### 7.6 WP-5 — Instructions, panel, tools, knowledge sections (Sonnet; wave A)

**Scope.** §4.6, §4.7.
1. `instructions-tab.tsx`: proportional font editor (not mono) with line-height 1.6, max-width 720 px, auto-growing to a 60 vh cap then scroll, character count + rough token estimate, "Reset to pack default" (from `usePacks` → `manifest.default_instructions`, confirm), "Instructions by mode" note when `instructions_by_mode` exists; greeting with a "Say verbatim / Let the model paraphrase" radio; language `Select`; timezone `Combobox` over `Intl.supportedValuesOf("timeZone")` with free-text escape; "End the call when the caller is silent for" with a number + "seconds" suffix; "Let callers interrupt" switch with one-line help.
2. `panel-tab.tsx`: panel choice as two cards using `PANEL_META` (label, description, layout tag, a tiny static thumbnail drawn in CSS: side vs wide), "Custom id" collapsible for unknown ids; capabilities as a list with `CAPABILITY_META` descriptions and consequences ("Callers can turn on their camera; the agent sees frames when the model supports vision"); the vision hint uses `warning-soft`/`warning-text` tokens and links to the Providers section.
3. `tools-tab.tsx`: built-ins as a single list (`divide-y`) with name, help, switch; a "Vision" group (describe frame, pin frame) disabled with a hint when no camera/screen capability; "Max tool steps per turn" moved to an "Advanced" collapsible with help; HTTP tools and MCP servers as cards with a proper empty state ("No HTTP tools yet — connect an API the agent can call") and the note "Saved automatically to this agent" replacing the leaky "attached when you click Save & validate" (keep the behaviour: attach on save; the copy just stops describing the mechanism); pack tools as a read-only list with a one-line explanation per tool if `PackManifest` has none, just the names in mono.
4. `components/console/tools/*`: editor dialogs become `Sheet`s with sections (Basics, Request, Auth, Response, Safety) and inline validation (JSON schema parse errors shown next to the field); dry-run result panel with status, duration, body; `tool-row.tsx` uses `StatusChip` for disabled/shared.
5. `knowledge-tab.tsx`: attached list with search; per KB: name, documents/chunks with correct plurals, status; "Manage" is an internal link (no external icon); "Auto-inject on each turn" → "Add the best matches to every turn" with help; "Top K" → "Matches per turn".
6. Tests: keep/update `console-http-tool-editor.test.tsx`; add `tests/console-tools-tab.test.tsx`, `tests/console-panel-tab.test.tsx` (vision hint, capability descriptions), `tests/console-instructions-tab.test.tsx` (reset to default, counters).

**Files owned.** `web/src/components/console/agents/tabs/instructions-tab.tsx`, `…/panel-tab.tsx`, `…/tools-tab.tsx`, `…/knowledge-tab.tsx`, `web/src/components/console/tools/**`, `web/tests/console-http-tool-editor.test.tsx`, `web/tests/console-tools-tab.test.tsx`, `web/tests/console-panel-tab.test.tsx`, `web/tests/console-instructions-tab.test.tsx`.

**Depends on.** WP-0. Optional WP-3 `useSectionIssues`.

**Acceptance.** No jargon labels (see the copy table in §4.6/§4.7); mobile: switches never clipped (row layout `grid-cols-[1fr_auto]`); instructions readable at 390 px; sheets scroll internally with a sticky footer.

**Verification.** Gates; screenshots of the four sections + one open tool sheet, both sizes.

### 7.7 WP-6 — Knowledge pages (Sonnet; wave A)

**Scope.** §4.7 (knowledge pages).
1. `kb-list.tsx`: `ResponsiveTable`; columns Name (+ description), Documents, Chunks, Embedder (human label: "Local (fastembed)" / "OpenAI"), Updated; row link; actions menu; empty state.
2. `kb-detail.tsx` + `kb-documents.tsx`: header with counts; a drop zone (drag-and-drop + click; accepted types listed; per-file progress from `upload.ts`, which gains an `onProgress` via XHR or keeps fetch with an indeterminate bar); document rows with `StatusChip` (pending = "Indexing…" with a spinner, ready, failed with the error inline), chunks, size (`formatBytes`), delete; polling stays.
3. `kb-search-panel.tsx`: "Try a question" with results as cards showing file, score as a 0–100 bar, text with the query terms highlighted (`<mark>`), and an empty result state with a hint.
4. `create-kb-dialog.tsx`: stays a dialog (2 fields) with embedder choice as radio cards.
5. Tests: `tests/console-kb-documents.test.tsx` (drop zone, status chips, polling stops), `tests/console-kb-search.test.tsx`.

**Files owned.** `web/src/components/console/knowledge/**`, `web/src/app/console/knowledge/**`, `web/src/components/console/lib/upload.ts`, `web/tests/console-kb-documents.test.tsx`, `web/tests/console-kb-search.test.tsx`.

**Depends on.** WP-0.

**Acceptance.** Upload works by drag or click; 390 px usable; failed documents explain and offer retry (re-upload).

**Verification.** Gates; screenshots `console-knowledge`, `console-kb-detail` (with pending + ready + failed rows from mocked data in the preview route, and live), both sizes.

### 7.8 WP-7 — Sessions list and detail (Opus; wave A)

**Scope.** §4.10.
1. `sessions-table.tsx`: filters — agent `Select` (by name, from `useAgents`), status chips, date range (today / 7 d / 30 d / all, client-side); columns Agent, Status (`StatusChip`; swept failures muted as today), Duration (`ended_at − started_at`, `formatDuration`; "—" if never started), Started (`RelativeTime`), Mode (chip), Turns (from `usage` if present else "—"); client-side pagination 25/page; row link; empty state.
2. `session-detail-view.tsx`: header (agent name → link to editor, status chip, started `RelativeTime` with exact, duration, mode, config version, room id mono + copy); a stats strip (`DescriptionList`: turns, tool calls, errors, and the usage keys rendered as label/value — never raw JSON); a **unified timeline** (`session-timeline.tsx`): transcript turns and events merged and sorted by `toMillis`, grouped by minute; turn rows show speaker (You / Agent), text, `(interrupted)` chip; tool events pair `tool_call_started`/`tool_call_ended` by `call_id` into one row with duration and status; `agent_state` events collapse into a thin state track (small `StateMeter xs` + label) rather than rows; `error`/`escalation` rows in danger/warning soft; payload JSON behind a "Details" disclosure; filter chips (Turns · Tools · State · Errors · Other); a sticky mini-index by minute.
3. "Panel at end of call" tab: renders `resolvePanel(agent.ui_panel_id)` with `final_ui_state` (normalised through `normalizeUiState`), empty assets map, no-op `perform`, `connectionState: "disconnected"`, read-only banner. Bundle: this pulls `streamdown` and the panels into the console bundle — acceptable; report the size.
4. Tests: update `console-sessions-table.test.tsx`, `console-session-detail-view.test.ts` (keep `formatUsage*` tests); add `tests/session-timeline.test.tsx` (merge order across ISO and epoch-seconds inputs, tool pairing, filters).

**Files owned.** `web/src/components/console/sessions/**`, `web/src/app/console/sessions/**`, `web/tests/console-sessions-table.test.tsx`, `web/tests/console-session-detail-view.test.ts`, `web/tests/session-timeline.test.tsx`.

**Depends on.** WP-0 (`format.ts`, `StatusChip`, `StateMeter`, `DescriptionList`), WP-9 for the panel render (import via `panels/registry`; if WP-9 is not merged, the old panels still render).

**Acceptance.** A reviewer can read a call top to bottom in one column, see what tools ran and how long, and see the final notebook; no raw JSON visible without opening "Details"; 390 px usable.

**Verification.** Gates; screenshots `console-sessions`, `console-session-detail` (a session with tools + errors), both sizes; bundle report.

### 7.9 WP-8 — Session experience (Opus; wave A)

**Scope.** All of §5.
1. Pure/presentational seam (required by WP-10): `stage-view.tsx` exporting `StageView` with props `{ agentState: AgentUiState; agentName: string; videoTrack?: TrackReference; audioTrack?: TrackReference; localTrack?: TrackReference; localLabel?: "You" | "Screen"; compact?: boolean; elapsedMs?: number; audioBlocked?: boolean; onEnableAudio?: () => void }` where `AgentUiState = "connecting" | "listening" | "thinking" | "speaking" | "reconnecting" | "failed" | "ended"`; `agent-stage.tsx` becomes the hook-wired container mapping `useVoiceAssistant().state` + connection state to `AgentUiState`. `pre-call-card.tsx` takes an injectable `devices` object (`{ status: "idle"|"requesting"|"granted"|"denied"|"unsupported"; level: number (0–1); inputs: MediaDeviceInfo[]; selectedId?: string }`) and callbacks, with a `useMicCheck()` hook in `use-mic-check.ts` providing it at runtime. `end-of-call-card.tsx` (`{ agentName, durationMs, onRestart, testMode, backHref? }`), `session-unavailable.tsx` (`{ kind: "not_found"|"not_published"|"unreachable", slug, testMode }`).
2. `lib/livekit.ts`: add `classifyConnectError(error): "not_found" | "not_published" | "unreachable" | "other"`; `page.tsx` passes `loadErrorKind` to `SessionExperience`.
3. `session-shell.tsx`: the §5.3 layouts (desktop side/wide, mobile stack with sticky controls and safe-area insets, transcript as a bottom sheet on mobile).
4. `session-room.tsx`: audio priming per §5.2 (implementer checks `useSession` options in `node_modules/@livekit/components-react` for a way to pass an `AudioContext`/`roomOptions`; if present, create + resume the context in the Start click and pass it; otherwise call `session.room.startAudio()` right after connect and, if `canPlaybackAudio` stays false, `StageView` shows the "Tap to hear <agent>" overlay wired to `StartAudioButton`); the `StartAudioButton` leaves the transcript header; elapsed timer; connection states per §5.4; the vendored control bar restyled from the wrapper (§5.4); toasts for device errors keep `sonner` but use the session toaster position top-center.
5. `(session)/layout.tsx`: `data-surface="session"`, `next/font/google` for Caveat + Patrick Hand exposing `--font-hand` / `--font-hand-label` on the wrapper.
6. Test-mode chrome (§5.6): a slim top bar "Test call · draft agent · Back to editor" only when `testMode`; never the "draft agents allowed" badge for end users.
7. Tests: update `livekit-precall-card.test.tsx` (device states, disabled Start while requesting, denied guidance), keep `livekit-connect.test.ts` (+ `classifyConnectError`), `ui-state.test.ts`, `use-byte-stream.test.tsx`; add `tests/session-shell.test.tsx` (side/wide/mobile DOM order, controls sticky container present), `tests/stage-view.test.tsx` (every `AgentUiState` renders the right label + `data-state`; audio-blocked overlay), `tests/session-unavailable.test.tsx` (three kinds, builder link only in not_published).

**Files owned.** `web/src/components/session/**`, `web/src/app/(session)/**`, `web/src/lib/livekit.ts`, `web/src/lib/livekit-server.ts`, `web/src/hooks/useAgentRpc.ts`, `web/src/hooks/useByteStream.ts`, `web/src/hooks/useUiRequests.ts`, `web/src/hooks/useUiState.ts`, `web/src/lib/ui-state.ts`, `web/tests/livekit-precall-card.test.tsx`, `web/tests/livekit-connect.test.ts`, `web/tests/ui-state.test.ts`, `web/tests/use-byte-stream.test.tsx`, `web/tests/session-shell.test.tsx`, `web/tests/stage-view.test.tsx`, `web/tests/session-unavailable.test.tsx`.

**Depends on.** WP-0 (`StateMeter lg`, `StatusChip`, button `xl`/`brand`, tokens). WP-9 for panel visuals (independent files).

**Acceptance.** Pre-call: mic permission requested on "Check microphone" or automatically after the first interaction (never on page load), live level meter, denied state explains how to fix per browser; Start is a `brand` `xl` button; in-call at 390 px: controls always visible without scrolling, notebook reachable, self-view visible when the camera is on; end-of-call shows duration; not-found vs not-published differ; session bundle ≤ 400 kB; reduced motion honoured; all states enumerable through `StageView` props.

**Verification.** Gates; screenshots via the preview route (WP-10) for every state at both sizes; plus one real call on LiveKit Cloud (test mode) with a manual checklist: hear the agent, camera PiP, screen share, chat, reconnect (toggle Wi-Fi), end.

### 7.10 WP-9 — Panels: generic + insurance notebook integration (Opus; wave A)

**Scope.** §5.5.
1. `panels/generic/index.tsx`: tokens instead of palette classes; section headers as `h3` caption style (no uppercase tracking); notes without the side rail (use a leading dot in tone color); checklist with the shared checkbox look; assets grid; activity rows with `StateMeter xs` for `running`; the "Pack data" raw view stays behind a disclosure.
2. `panels/insurance_notebook/*`: keep the paper, ink, tape, stamp, pen and all three animations; changes: `notebook-styles.ts` drops the runtime `@import` and reads `var(--font-hand, "Caveat", cursive)` / `var(--font-hand-label, "Patrick Hand", cursive)`; `.field` loses its `border-left` stripe (use a small status dot before the label and `data-status` colors on the value); the ring's inner fallback becomes `var(--color-card)`; `studio.tsx` desk cards use the shared `Section`-like look (`bg-card border rounded-lg`, `h3` caption title) and token colors; `TeamFeed` uses `StateMeter xs` for running; `SketchCard` button becomes `variant="brand"` size `sm` with the confirmed state as a `StatusChip success`; `PacketDialog` keeps `Dialog` (large content) with the description list on tokens; container queries stay; mobile: paper padding and note sizes as today, plus the stamp scaled to fit 358 px content width without overlapping the last line (add bottom padding equal to stamp height on narrow containers).
3. Fixtures: add `tests/fixtures/generic_ui_state.json` (status, progress, 3 notes with tones, 4 checklist items incl. blocking + done, 2 assets (one image mime, one without bytes), 5 activity events across phases, a `custom` object) for the preview route and generic tests.
4. Tests: update `generic-panel.test.tsx`, `insurance-notebook.test.tsx` (font var usage, no `border-left` on `.field`, stamp present), `registry.test.tsx`.

**Files owned.** `web/src/panels/**`, `web/tests/fixtures/**`, `web/tests/generic-panel.test.tsx`, `web/tests/insurance-notebook.test.tsx`, `web/tests/registry.test.tsx`.

**Depends on.** WP-0 tokens. Font variables come from WP-8 (session) and WP-10 (preview); fallbacks keep it working before they land.

**Acceptance.** Notebook character unchanged to the eye (paper, handwriting, tape, stamp, pen); no runtime Google Fonts `@import`; no hard-coded palette classes; generic panel reads as part of the system; both panels render from fixtures without a room.

**Verification.** Gates; screenshots from the preview route: `panel-generic` (fixture), `panel-notebook-blank/auto/flood`, both sizes, dark (and light for the tokens proof).

### 7.11 WP-10 — Preview route, capture script, visual QA harness (Sonnet; wave A, completes after WP-8/WP-9 seams)

**Scope.**
1. `app/console/preview/layout.tsx`: loads Caveat + Patrick Hand via `next/font/google` and sets `--font-hand`/`--font-hand-label`; `metadata.robots = { index: false }`; no sidebar (a minimal top bar with a state selector).
2. `app/console/preview/panels/page.tsx`: query-driven scenes, e.g. `?scene=notebook&fixture=flood&surface=dark`, `?scene=generic`, `?scene=session&layout=wide&state=listening&camera=1`, `?scene=precall&devices=denied`, `?scene=ended`, `?scene=unavailable&kind=not_published`, `?scene=primitives` (every WP-0 primitive in every state). Panels render inside the real `SessionShell` with `StageView` (WP-8 seam), the `ConnectionBanner`, and a static controls placeholder that mirrors the control bar's size (the real control bar needs a room; a `ControlBarPlaceholder` with the same dimensions is acceptable for screenshots). Fixtures loaded from `tests/fixtures/*.json` at build time (static import).
3. Capture script: extend `scratchpad/ui-audit/capture.mjs` **and** commit a copy as `web/scripts/ui-capture.mjs` (usage: `node scripts/ui-capture.mjs <outDir> [--only=<name>] [--theme=light|dark]`), routes: home, console overview, agents (list, new), credentials, knowledge (list, detail), sessions (list, detail), settings, editor sections via `?section=`, preview scenes (all of the above × side/wide × states × precall device states × unavailable kinds), console not-found, session not-found/not-published (real routes). Both viewports; light and dark for console routes. Add an axe pass (`@axe-core/playwright`, dev dep) that writes `axe.json` per route and fails on `serious`/`critical`.
4. `e2e/preview.spec.ts` (optional smoke): the preview scenes render without console errors.
5. Tests: `tests/preview-scenes.test.tsx` (each scene renders the expected `data-testid`s).

**Files owned.** `web/src/app/console/preview/**` (new), `web/scripts/ui-capture.mjs` (new), `scratchpad/ui-audit/capture.mjs`, `web/e2e/**`, `web/playwright.config.ts`, `web/tests/preview-scenes.test.tsx`, dev dependency additions for axe (coordinate with WP-0 for `package.json`: request, or land after WP-0 with the integrator's OK).

**Depends on.** WP-0; WP-8 exports (`StageView`, `SessionShell`, `PreCallCard` with injected devices, `EndOfCallCard`, `SessionUnavailable`, `ConnectionBanner`); WP-9 fixtures. Can start immediately with placeholders and finish when seams land.

**Acceptance.** Every in-call and panel state is screenshot-able without a live call; the script runs in one command and writes `after/<viewport>-<name>.png` plus `axe.json`; the route is excluded from the sidebar and from indexing.

**Verification.** Run the script end-to-end; attach the output tree listing.

### 7.12 WP-11 — Home page, console not-found, a11y utilities (Sonnet; wave A)

**Scope.** §3.4 (home), §5.7 (console not-found), global a11y helpers.
1. `app/page.tsx`: the §3.4 home — wordmark + one sentence, two paths (Open console / How session pages work), an environment line from `/v1/health` (server-side fetch, no admin token: version, packs), footer with docs links. It sits outside the console's theme provider, so on a hard load it renders **light**; during client navigation from a dark console the `html.dark` class may still be present, so its tokens must look right in both (they will, by construction). No theme control on the home page.
2. `app/not-found.tsx` (root) and `app/console/not-found.tsx`: branded, with a way back.
3. `public/`: replace the Next/Vercel sample SVGs with a favicon set and an `og` image (static PNG 1200×630 generated from an HTML template; optional).
4. Tests: `tests/home.test.tsx` (links, health line states).

**Files owned.** `web/src/app/page.tsx`, `web/src/app/not-found.tsx` (new), `web/src/app/console/not-found.tsx` (new), `web/public/**`, `web/src/app/favicon.ico`, `web/tests/home.test.tsx`.

**Depends on.** WP-0.

**Acceptance.** Home no longer repeats its title; links go to `/console` and an anchor explaining `/s/<slug>`; 404s have a way back; favicon shows the meter mark.

### 7.13 WP-12 — Integration, gates, after-screenshots review (Opus; last; sequential)

1. Merge order: WP-0 → (WP-1…WP-11 in any order; resolve the cross-package requests from hand-off notes) → this package.
2. Apply the file-change requests recorded in hand-off notes (e.g. WP-1's `/console/agents` link in `app/page.tsx`, WP-3's `router.push`).
3. Run all gates, `pnpm check:contrast`, the capture script for everything, the axe pass; fix regressions; verify the bundle budget; verify no `emerald-|sky-|amber-|red-|blue-` classes remain under `components/console`, `components/session`, `components/shared`, `panels` (`grep`), no `@phosphor-icons`, no runtime Google Fonts `@import`.
4. Produce `scratchpad/ui-audit/after/INDEX.md` listing every screenshot with its before counterpart; Fable reviews and returns a punch list; WP-12 applies it.

**Exclusive ownership summary (existing files → owner).**

| Path | Owner |
|---|---|
| `app/layout.tsx` | nobody (W0 rule) |
| `app/globals.css`, `components/ui/**`, `components/shared/**`, `components/console/shared/**`, `components/console/lib/api-hooks.ts`, `components/console/lib/constants.ts`, `lib/format.ts`, `lib/theme.ts`, `lib/utils.ts`, `scripts/check-contrast.mjs`, `package.json` | WP-0 |
| `app/console/layout.tsx`, `app/console/page.tsx`, `app/console/agents/page.tsx`, `app/console/settings/**`, `components/console/nav.tsx` (delete), `components/console/shell/**` (except theme-provider), `components/console/overview/**`, `components/console/settings/**`, `components/console/lib/query-provider.tsx` | WP-1 |
| `components/console/agents/agents-table.tsx`, `components/console/agents/create-agent-dialog.tsx` (delete), `components/console/agents/create/**`, `app/console/agents/new/**` | WP-2 |
| `components/console/agents/agent-editor.tsx`, `components/console/agents/editor/**`, `components/console/agents/defaults.ts`, `components/console/lib/{schemas,form-errors,zod-resolver}.ts`, `app/console/agents/[id]/page.tsx` | WP-3 |
| `components/console/registry/**`, `components/console/agents/tabs/providers-tab.tsx`, `components/console/credentials/**`, `app/console/credentials/**` | WP-4 |
| `components/console/agents/tabs/{instructions,panel,tools,knowledge}-tab.tsx`, `components/console/tools/**` | WP-5 |
| `components/console/knowledge/**`, `app/console/knowledge/**`, `components/console/lib/upload.ts` | WP-6 |
| `components/console/sessions/**`, `app/console/sessions/**` | WP-7 |
| `components/session/**`, `app/(session)/**`, `lib/livekit.ts`, `lib/livekit-server.ts`, `lib/ui-state.ts`, `hooks/use*.ts` | WP-8 |
| `panels/**`, `tests/fixtures/**` | WP-9 |
| `app/console/preview/**`, `scripts/ui-capture.mjs`, `e2e/**`, `playwright.config.ts`, scratchpad `capture.mjs` | WP-10 |
| `app/page.tsx`, `app/not-found.tsx`, `app/console/not-found.tsx`, `public/**`, `app/favicon.ico` | WP-11 |
| `components/agents-ui/**`, `hooks/agents-ui/**`, `contracts/**` | nobody (vendored / generated) |

`tests/README.md` → WP-12. Existing tests → owner: `console-session-detail-view.test.ts`, `console-sessions-table.test.tsx` → WP-7; `livekit-connect.test.ts`, `livekit-precall-card.test.tsx`, `ui-state.test.ts`, `use-byte-stream.test.tsx` → WP-8; `console-zod-resolver.test.ts` → WP-3; `console-registry-form.test.tsx`, `console-credential-dialog.test.tsx` → WP-4; `console-http-tool-editor.test.tsx` → WP-5; `generic-panel.test.tsx`, `insurance-notebook.test.tsx`, `registry.test.tsx` → WP-9.

### 7.14 API asks (non-blocking; api owner)

1. `ValidationResult.issues?: {path: string; message: string; severity: "error"|"warning"}[]` alongside the existing `errors`/`warnings` — lets the editor map issues to sections and fields exactly. Until then the UI uses the keyword heuristic in WP-3 (`stt|llm|tts|realtime|provider|credential|model|avatar|image` → Providers; `instruction|greeting|language|timezone|interrupt` → Instructions; `tool|http|mcp` → Tools; `knowledge|kb|top_k` → Knowledge; `panel|camera|screen|vision|chat` → Panel; else → General).
2. `GET /v1/sessions?limit=&offset=` (and `total` already exists) — the list is client-side paginated until then.
3. Optional: `AgentOut.session_count` and `AgentOut.last_session_at` — the list joins client-side until then.
4. Docs-only: CONTRACTS §11 `PanelDefinition` gains `handleRequest?: (req: UiRequest) => UiRequestResult | Promise<UiRequestResult>` (optional; the room falls back to declining). No runtime contract change.

## 8. Out of scope / deferred

- Vendor logos (licensing; `VendorMark` monograms instead).
- Authentication, users, organisations, roles; anything beyond the single admin token (D14).
- Packs page and pack settings UI (`pack_settings` / `settings_schema` exists in contracts but has no UI; noted in REVIEW-FINAL §6).
- Tools as a top-level page; shared-tool library UX beyond "attach a shared tool".
- Session deletion / agent archive (needs an API; F-22).
- Analytics/usage dashboards, cost estimates, charts.
- Light theme for the session surface as a user-facing option (tokens support it; not exposed).
- Pack-provided end-of-call summary on the end card; in-call feedback (thumbs) — no endpoint.
- Command palette, keyboard shortcut scheme, density setting, QR code in the publish popover, the iframe test drawer, agent duplication: all marked P1 inside their packages; ship P0 first.
- Realtime waveform recording/playback of calls; transcript export (CSV/JSON) — trivial later, not now.
- Internationalisation of the UI (copy is English; structure is i18n-friendly: no concatenated sentences in components).
- Forking `agents-ui` components; a custom control bar. If the wrapper-override approach in §5.4 proves insufficient, escalate rather than fork.
- Notebook redesign. Its paper/ink character is intentional and kept; only integration changes ship.
- E2E Playwright coverage of a live LiveKit call (remains a manual checklist in WP-8; the preview route covers visual states).
