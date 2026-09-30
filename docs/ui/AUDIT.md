# LKAP web console — design-system audit and implementation plan

Audit of `web/` against `docs/ui/DESIGN-SYSTEM.md` (the spec), done read-only on 2026-10-01. Paths below are
relative to `web/src/` unless they start with `web/` or `docs/`. Counts come from greps over `components/`, `app/`
and `panels/`. The generated `contracts/lkap-contracts.d.ts` is excluded.

**Headline.** The console is already token-driven. The existing tokens pass the current contrast gate (65 of 65
pairs), and there are almost no raw hex or palette classes in console code. The real gap is drift from the spec in
four areas:
- the spec's scale (type, radius, duration, control heights);
- its primitives (primary button, one status pill, alertdialog, toast behaviour, select);
- its state model (raw errors, disabled-with-tooltip permissions, missing search, offline state);
- its shell (inset panel, 248 px sidebar, phone tab bar).

`pnpm exec tsc --noEmit` is clean at baseline.

---

## 1. How the app is built

| Area | Today | Evidence |
|---|---|---|
| Framework | Next.js 15.5.26 (App Router, Turbopack dev), React 19.1.1, TypeScript 5.9 | `web/package.json` |
| Styling | Tailwind v4 via `@tailwindcss/postcss`; no `tailwind.config`; one stylesheet `app/globals.css` (405 lines) with `@theme inline`, `:root`, `.dark` and state-meter CSS; imports `tw-animate-css` and `shadcn/tailwind.css` (custom variants `data-open` etc.) | `app/globals.css:1-3`, `web/postcss.config.mjs` |
| Component library | shadcn "radix-nova" style, vendored into `components/ui/*` (35 files) on the unified `radix-ui` package; `cmdk` for command; `sonner` for toasts; `class-variance-authority`; `cn` package and `@/lib/utils` `cn` both used (33 files import from `"cn"`) | `web/components.json`, `components/ui/button.tsx:3-4` |
| Headless primitives present | Dialog, Popover, DropdownMenu, Select, Switch, Tabs, Tooltip, Checkbox, RadioGroup, Slider, Toggle, Collapsible, ScrollArea, Progress; `ui/searchable-select.tsx` (cmdk-based combobox) | `components/ui/` |
| Sheet / drawer | None: `ui/sheet.tsx` was deleted, and ESLint `no-restricted-imports` blocks `ui/sheet`, `ui/drawer` and `vaul` | `web/eslint.config.mjs:16-45` |
| Icons | `lucide-react` only (173 imports). The wrapper `shared/icon.tsx` sets `strokeWidth={1.75} absoluteStrokeWidth` and sizes sm 14 / md 16 / lg 20 / xl 24 | `shared/icon.tsx:10,29` |
| Fonts | Geist + Geist Mono via `next/font/google` in the root layout (display defaults to `swap`). Caveat + Patrick Hand (`display: "swap"`) in the session and preview layouts for the insurance notebook | `app/layout.tsx:2-12`, `app/(session)/layout.tsx:20-32` |
| Theme | `next-themes`, `attribute="class"`, default **light**, `enableSystem`, storage key `lkap-theme`. The provider is mounted **only** in `app/console/layout.tsx`, so `/`, `/login` and `not-found` render outside it. `/s/[slug]` is fixed dark (`.dark` + `data-surface="session"`) | `lib/theme.ts:15-16`, `console/shell/theme-provider.tsx:18-24`, `app/(session)/layout.tsx:40-42` |
| Flash on load | Console: no flash (the next-themes inline script runs before paint). Public pages: the `app/page.tsx:23-28` comment admits a stale `html.dark` can leak in after client navigation | — |
| Shell | shadcn `Sidebar` (232 px, collapsible to a 56 px icon rail) + `SidebarInset` + sticky `TopBar` (`h-14 lg:h-12`) + `ApiHealthBanner`; content `max-w-[1200px]`; skip link to `#console-main`. On phones (< 768 px) the nav opens as a full-screen **Dialog**, not a sheet | `console/shell/console-shell.tsx:34-66`, `ui/sidebar.tsx:28-30,180-203`, `console/shell/top-bar.tsx:34` |
| Tests | Vitest + jsdom (165 files in `web/tests/`), Playwright e2e (5 specs incl. axe a11y), `pnpm check:contrast` (`web/scripts/check-contrast.mjs`, covered by `tests/contrast-script.test.ts`), `web/scripts/ui-capture.mjs` for screenshots | `web/vitest.config.ts`, `web/e2e/` |
| Existing UI docs | `docs/UI_UX_SPEC.md` (953 lines, v1 spec: signal-green accent, Geist, no hero metrics, state meter as signature); `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md` §5 "No side drawers" and §6 dialog/copy rules | — |

### Shared layouts
- **Root** `app/layout.tsx`: fonts and `globals.css`. Its comment says "Nobody edits this file after W0". UI-1 must
  override that explicitly.
- **Console** `app/console/layout.tsx`: `ThemeProvider` → `ConsoleShell` (query provider, breadcrumb context,
  tooltip provider, sidebar, top bar, health banner, `Toaster position="bottom-right"`).
- **Session** `app/(session)/layout.tsx`: fixed dark, `text-base` (16 px body), handwriting fonts,
  `Toaster theme="dark" position="top-center" richColors`.
- **Embed** `app/(session)/s/[slug]/embed-layout.tsx`: client-only dynamic import of
  `session/embed/embed-session.tsx`. Its loading placeholder is an empty `h-dvh` div.
- **Preview** `app/(preview)/layout.tsx`: shell-less, noindex route group serving `/console/preview/panels`. This
  is the natural home for the styleguide page.
- **Login** `app/login/page.tsx`: centred single card (`login-form.tsx`), with sign-in and invite-accept modes.

## 2. Route inventory (every screen)

| Route | Page file | Main component(s) | Archetype (spec §7.4) |
|---|---|---|---|
| `/` | `app/page.tsx` | inline | Public landing (not in the spec; keep minimal) |
| `/login`, `/login?invite=` | `app/login/page.tsx` | `app/login/login-form.tsx` | Sign-in |
| not-found (global) | `app/not-found.tsx` | inline | Empty state |
| `/console` | `app/console/page.tsx` | `console/overview/overview.tsx` (+ `setup-checklist`, `recent-sessions`, `live-now`, `quick-actions`) | Overview |
| `/console/agents` | `app/console/agents/page.tsx` | `console/agents/agents-table.tsx`, `create/new-agent-button.tsx` | List |
| `/console/agents/new` | `app/console/agents/new/page.tsx` | agents list + `create/create-agent-deep-link.tsx` (opens the create dialog) | List + form dialog |
| `/console/agents/[id]` | `app/console/agents/[id]/page.tsx` | `console/agents/agent-editor.tsx` → `editor/editor-shell.tsx`, `editor/sections/*`, `tabs/*`, `flow/*`, `test-chat/*` | Detail / record (wide) |
| `/console/knowledge` | `app/console/knowledge/page.tsx` | `knowledge/kb-list.tsx`, `create-kb-dialog.tsx` | List |
| `/console/knowledge/[id]` | `app/console/knowledge/[id]/page.tsx` | `knowledge/kb-detail.tsx`, `kb-documents`, `kb-search-panel`, `kb-evals-card` | Detail |
| `/console/tools` | `app/console/tools/page.tsx` | `ToolsPageTabs` → `tools/tools-list.tsx`, `tools/kits/*`, `tools/apps/*` | List (tabbed) |
| `/console/datasets` | `app/console/datasets/page.tsx` | `datasets/dataset-list.tsx` | List |
| `/console/datasets/[id]` | `app/console/datasets/[id]/page.tsx` | `datasets/dataset-detail.tsx` | Detail |
| `/console/connections` | `app/console/connections/page.tsx` | `connections/connections-table.tsx` | List |
| `/console/connections/new` | `app/console/connections/new/page.tsx` | `connections/connection-create-form.tsx` | Form |
| `/console/connections/[id]` | `app/console/connections/[id]/page.tsx` | `connections/connection-detail.tsx`, `-tabs`, `fleet-card`, `deploy-panel`, `storage-tab` | Detail |
| `/console/providers` | `app/console/providers/page.tsx` | `providers/providers-catalog.tsx`, `catalog-dialog.tsx`, `settings/workspace-prices-dialog.tsx` | List (grouped) |
| `/console/keys` | `app/console/keys/page.tsx` | `registry/credential-list.tsx`, `credential-dialog.tsx` | List |
| `/console/telephony` | `app/console/telephony/page.tsx` | `telephony/telephony-page.tsx` (trunks, numbers, rules, calls, tools, dialing policy) | Settings-like multi-section |
| `/console/sessions` | `app/console/sessions/page.tsx` | `sessions/sessions-table.tsx` | List |
| `/console/sessions/[id]` | `app/console/sessions/[id]/page.tsx` | `sessions/session-detail-view.tsx`, `sessions/detail/*`, `live/*`, `sessions-v2/*` tabs | Detail (wide) |
| `/console/analytics` | `app/console/analytics/page.tsx` | `analytics/analytics-view.tsx` | Analytics |
| `/console/settings?tab=` | `app/console/settings/page.tsx` | `settings/settings-tabs.tsx`, 11 tabs (workspace, appearance, team, api-keys, ai-agents, webhooks, compliance, knowledge-connections, storage, environment, danger) | Settings |
| console not-found | `app/console/not-found.tsx` | `EmptyState` | Empty state |
| `/console/preview/panels?scene=` | `app/(preview)/console/preview/panels/page.tsx` | `components/preview/*` | Dev harness |
| `/s/[slug]` (+ `?mode=test`) | `app/(session)/s/[slug]/page.tsx` | `session/session-experience.tsx` → `pre-call-card`, `session-room`, `stage-view`, `end-of-call-card`, `session-unavailable` | Caller-facing |
| `/s/[slug]?embed=1` | same page → `embed-layout.tsx` | `session/embed/*` (text or voice widget) | Caller-facing embed |
| API proxy | `app/api/console/[...path]/route.ts` | — | n/a |

## 3. Findings

Severity: **H** blocks the spec or causes user harm; **M** is visible drift; **L** is polish.

### 3.1 Colours

| # | Sev | Finding | Evidence |
|---|---|---|---|
| C1 | M | **Accent and success are the same colour.** `--success: var(--brand)`, `--success-soft: var(--brand-soft)`, `--success-text: var(--brand-text)` in both themes. The spec requires status to be a separate system. The accent is signal green (hue 152), not teal. | `app/globals.css:117-119,176-178` |
| C2 | M | **Two parallel "danger" systems**: `--destructive*` (169 class uses) and `--danger*` (`text-danger-text` 115, `bg-danger-soft` 19, plus solids). | greps |
| C3 | M | **Primary is neutral ink, not the accent.** `--primary: oklch(.2 …)`, and `Button` `default` = `bg-primary`. The accent `brand` variant has 10 uses, 7 of them on the session surface. | `globals.css:92`, `ui/button.tsx:11,21` |
| C4 | M | **224 opacity-modified token classes** derive colours outside the token file: `bg-muted/50` 25, `bg-destructive/90` 24, `bg-muted/40` 14, `ring-destructive/40` 13, `ring-destructive/20` 13, `bg-destructive/10` 13, `bg-destructive/20` 12, `bg-input/30` 11, `bg-foreground/10` 10, … Plus 12 arbitrary colour classes (`bg-[color-mix(…)]`, `bg-[oklch(from …)]`), e.g. `ui/button.tsx:15,22`, `ui/dialog.tsx:42`, `ui/bubble.tsx:28`. | greps |
| C5 | L | **Hairlines are not the border token in primitives.** `Card` and `Dialog` use `ring-1 ring-foreground/10` (6 uses), not `--border`. | `ui/card.tsx:14`, `ui/dialog.tsx:115` |
| C6 | L | **Hex/rgb literals outside the token file are confined to leave-alone code**: insurance notebook paper theme (`panels/insurance_notebook/notebook-styles.ts:28-36,175-210`, `panels/blocks/notebook/paper-theme.ts:20-54`), canvas/signature ink (`panels/blocks/canvas/*`, `signature.tsx:52,150`), vendored visualizers (`agents-ui/agent-audio-visualizer-wave.tsx:12`, `-aura.tsx:24`). One computed `oklch()` in `shared/vendor-mark.tsx:51` (per-vendor tint, a legitimate exception). **Console components: zero.** | greps |
| C7 | L | **Palette utility classes: 13 total, none in console code.** `agents-ui/agent-control-bar.tsx:39-42` (8× blue), `agents-ui/.../tile-view.tsx:172`, `agents-ui/.../trigger.tsx:78` (`text-white`), `session/stage-view.tsx:260` (`bg-black`, video letterbox), `panels/blocks/link.tsx:144` (`bg-white` QR quiet zone), `panels/blocks/signature.tsx:268`. | greps |
| C8 | L | No `themeColor` metadata and no web manifest (`web/public/` has only `icon.png`, `og.png`, `widget.js`). | `app/layout.tsx:14-18` |

### 3.2 Typography, radius, shadow, duration

| # | Sev | Finding | Evidence |
|---|---|---|---|
| T1 | M | **Font is Geist / Geist Mono, not Inter**; no `cv11`/`ss01` features; body letter-spacing not set. | `app/layout.tsx:2-12` |
| T2 | M | **Size usage.** `text-xs` 476, `text-sm` 470, `text-[0.8125rem]` (13 px) 384, `text-[0.6875rem]` (11 px) 47, `text-base` 18, `text-lg` 14, `text-[1.0625rem]` 6, `text-[1.375rem]` 4, `text-[0.625rem]` (10 px, **below the scale**) 4, `text-[1.75rem]` 3, `text-2xl`/`text-xl`/`text-4xl` 2 each, `text-[0.8rem]` 2 (`ui/button.tsx:28`). The spec needs 13.5 px for buttons, nav and menu items (none today), 15 px for card/section titles (today 14 px `text-sm font-semibold`), 17 px dialog titles, 24 px stat values (today `text-lg` in `kb-evals-card.tsx:56-70`, `sessions-v2/cost-tab.tsx:52-55`, `connections/fleet-card.tsx:91`, `agents/editor/cost-estimate-dialog.tsx:87`; `text-2xl` at `analytics/analytics-view.tsx:157`). 10 px text at `shared/vendor-mark.tsx:40`, `analytics-view.tsx:222`, `knowledge/kb-search-panel.tsx:231`, `flow/flow-canvas.tsx:198`. | greps |
| T3 | M | **Primitives render 16 px text**: `ui/input.tsx:10`, `ui/textarea.tsx:9`, `ui/button.tsx:36` (`xl`), `ui/card.tsx:40`, `ui/dialog.tsx:216`. | — |
| T4 | L | **Uppercase: 5 hits, all eyebrow-shaped** (`agents/create/create-agent-dialog.tsx:352`, `create/template-preview.tsx:38,98`, `flow/flow-canvas.tsx:190`, `flow/node-form.tsx:594`). Four use 11 px / 600 / +.06em instead of the spec eyebrow (12 px / 500 / +.04em). `node-form.tsx:594` is a slot label, not an eyebrow, so it should go sentence case. | — |
| T5 | M | **Radius scale mismatch.** `--radius` = 6 px, so buttons and inputs are `rounded-sm` = 6 px (spec: 8). Usage: `rounded-md` 154, `rounded-xs` 77, `rounded-lg` 77, `rounded-full` 66, `rounded-sm` 45, bare `rounded` 18, `rounded-xl` 10, `rounded-2xl` 1, `rounded-4xl` 1 (`ui/badge.tsx:7`). Dialogs are 16 px (spec 14). `StatusChip` is `rounded-xs` (4 px) where the spec wants a 999 px pill. | `globals.css:151-157`, `shared/status-chip.tsx:44` |
| T6 | M | **Shadows.** `shadow` bare 16, `shadow-md` 10, `shadow-sm` 8, `shadow-lg` 4, `shadow-xs` 2, `shadow-xl` 1, `shadow-2xl` 1. Cards that carry a shadow against the spec's "cards have no shadow": `app/login/login-form.tsx:90,182` (`shadow-sm`), `session/session-card.tsx:29` (`shadow-md`), `agents/create/template-tile.tsx:120-123`. The dialog has **no** modal shadow (ring only). Popover/select/menu use `shadow-md`/`shadow-lg` (these map to `--elevation-overlay`). | greps |
| T7 | M | **Durations.** Tokens `--dur-1..4` are 120/180/240/320 ms (spec 100/150/220; the 320 ms sheet tier has no spec equivalent). Raw `duration-100` 6, `-200` 5, `-300` 3, `-250` 2, `-150` 1, `-400` 1, `-2000` 1, alongside `duration-(--dur-*)` 44. | `globals.css:163-166` |
| T8 | M | **Reduced motion is not global.** `globals.css:249-255` only targets `.motion-safe-transform` and the state meter, plus 16 scattered `motion-reduce:` classes. The spec requires one global rule (1 ms durations, 1 iteration). | — |
| T9 | L | **Control heights.** Input `h-8` (32 px, spec 36), Button default `h-8` (32, spec 34), `sm` `h-7` (28 ✓), `lg` `h-9` (36, spec 40), icon `size-8` (32 ✓ for icon buttons). Top bar is `lg:h-12` (48) on desktop (spec 56 everywhere). | `ui/input.tsx:10`, `ui/button.tsx:25-36`, `top-bar.tsx:34` |

### 3.3 Native controls and browser dialogs

| # | Sev | Finding | Evidence |
|---|---|---|---|
| N1 | H | **Native `<select>`: `telephony/native-select.tsx` used 13× across 6 files**: `trunks-section.tsx:308,318,334`, `rules-section.tsx:233,242`, `call-number.tsx:159`, `tools-section.tsx:138,248`, `numbers-section.tsx:274,390,538,548`. | greps |
| N2 | L | Native `<select>` in the dev-only preview harness (`preview/preview-shell.tsx:73,88`). Low priority; switch it when the preview is touched. | — |
| N3 | — | Native `<select>` in `panels/blocks/form.tsx:224` is **caller-facing panel content**. Flag it, but leave it to the panels owner (see §8). | — |
| N4 | ✓ | `window.confirm` / `alert(` / `prompt(`: **zero**. The `confirm()` hits are local functions. | greps |
| N5 | M | **No `alertdialog` anywhere (0 hits).** 19 `ConfirmDialog` uses render a plain `role="dialog"`. Only one typed confirmation exists (`settings/danger-tab.tsx:42-97`). The busy label is the generic "Working…" (`console/shared/confirm-dialog.tsx:66`). The danger button is hand-coloured per call site (`confirm-dialog.tsx:57`, plus 24× `bg-destructive/90`). | — |

### 3.4 States

| # | Sev | Finding | Evidence |
|---|---|---|---|
| S1 | H | **Raw server and vendor text reaches users.** `errorMessage()` returns `error.message` verbatim (`console/shared/error-banner.tsx`). `lib/api.ts:82-90` builds `ApiError` from the server's `payload.error.message`, or `response.statusText`, so network failures surface "Failed to fetch" and 5xx surface "Internal Server Error". 137 call sites in 77 files; 57 are concatenated as `Couldn't load X — ${errorMessage(error)}`. `datasets/dataset-detail.tsx:68` renders the API's `dataset.error`. `login-form.tsx:64-65` shows `ApiError.message`. | greps |
| S2 | H | **Permission-denied is disabled-plus-tooltip.** `disabled={!canWrite}` with a `title=` reason: 71 hits in 27 files. `GatedButton` (4 uses) wraps a disabled button in a tooltip. Both break spec §8.5 ("don't render controls the person can't use") and §6.4 ("never essential information only in a tooltip"). `shared/require-write.tsx:29` returns `null` while loading (a blank area). | greps |
| S3 | H | **Blank loading.** 6 `<Suspense>` with no fallback (`app/login/page.tsx:18`, `console/tools/page.tsx:25`, `providers/page.tsx:32`, `agents/[id]/page.tsx:15`, `connections/[id]/page.tsx:12`, `analytics/page.tsx:14`). The embed placeholder is an empty div (`embed-layout.tsx:11-14`). | — |
| S4 | M | **Text-only loading** in dialogs: `settings/knowledge-connection-dialog.tsx:402`, `tools/apps/connection-row.tsx:293`, `tools/apps/connect-app-dialog.tsx:260`. Spinner-in-page-header at `datasets/dataset-detail.tsx:66`. Spinners inside text buttons at `app/login/login-form.tsx:133,206` and `agents/editor/publish-popover.tsx`. 10 spinner icon variants (`Loader2Icon` 7 files, `LoaderCircleIcon` 2, `LoaderIcon` 1). | — |
| S5 | M | **Per-screen gaps** (heuristic matrix of skeleton / empty / error / search / no-matches / permission per top-level component): `overview/recent-sessions.tsx` and `live-now.tsx` have no error state; `overview/setup-checklist.tsx` has no loading or error; `connections/fleet-card.tsx` has no loading; `settings/workspace-tab.tsx` has no error; `settings/storage-tab.tsx` has no loading or error; `telephony/telephony-page.tsx` has no page-level state (each section owns its own). | grep matrix |
| S6 | M | **Offline / unavailable state: zero** (`navigator.onLine` never read). The API health banner (`console/shell/api-health-banner.tsx`) covers "API unreachable" only. | — |
| S7 | M | **Search is missing on most lists.** Present on 3 lists only: `agents/agents-table.tsx`, `providers/providers-catalog.tsx`, `tools/apps/app-gallery.tsx`. Missing on knowledge, tools (HTTP/MCP), kits, datasets, connections, credentials, telephony numbers/trunks/rules/calls, team, API keys, agent keys, webhooks, knowledge connections. Accent-insensitive matching exists only in `flow/flow-model.ts:177`. Filters are not remembered (localStorage appears only in `console/lib/cost-hooks.ts`). "No matches" copy exists in 10 files; `SearchXIcon` in 1. | greps |
| S8 | M | **Toasts.** Sonner defaults (about 4 s for every type), so **errors auto-dismiss** (spec: errors stay until dismissed; success/info 6 s). There is no `role="alert"` vs `role="status"` split, the error icon is `OctagonXIcon` (spec `CircleAlert`), the session toaster uses `richColors` (a second colour system), and toasts are top-centre on the session surface. | `ui/sonner.tsx`, `app/(session)/layout.tsx:45` |
| S9 | L | **Success feedback** is consistently toasts (211 `toast(` calls in 61 files). That matches the spec; only durations and roles change. | — |

### 3.5 Primary actions

| # | Sev | Finding | Evidence |
|---|---|---|---|
| P1 | M | **No primary action**: Sessions (`app/console/sessions/page.tsx`), Analytics, Settings, Telephony (page level), Overview (spec wants "New …"), Providers (two outline buttons only), Connection detail (header shows only a status chip, `connections/connection-detail.tsx:33-40`), KB detail (`knowledge/kb-detail.tsx:55-58`). Detail pages lack the spec's refresh + danger-outline + primary trio. | — |
| P2 | M | **Destructive action as the only header action**: `datasets/dataset-detail.tsx:73-80` (outline "Delete"; the spec wants danger-outline, ideally inside a Danger zone card). | — |
| P3 | M | **Codemod risk.** Of about 238 variant-less `<Button>` tags (overcounted, since the grep sees a tag before a wrapped `variant=`), all become the accent `primary` once `default` maps to brand. Views with several default buttons would then show several accent primaries. Screen packages must demote per view (`outline` → `secondary`, `ghost` → `ghost`), not codemod blindly. Variant tallies: `outline` 207, `ghost` 96, `destructive` 22, `link` 7, `secondary` 6. | greps |
| P4 | L | The two not-found pages have one `size="lg"` default primary and outline secondaries. Their order is primary-first (spec: primary last). | `app/not-found.tsx`, `app/console/not-found.tsx` |

### 3.6 Contrast

The existing tokens pass all 65 checked pairs (`node web/scripts/check-contrast.mjs`). The checker does **not** test
tertiary text, text on the sidebar or its hover fill, status solids against their foregrounds, or the spec's new
tokens.

I computed the **spec's proposed tokens** with culori (OKLCH → gamut-mapped sRGB → WCAG). These need correcting
in UI-1; they are not product decisions.

| Theme | Pair | Ratio | Target | Fix |
|---|---|---|---|---|
| Light | `--input` on `--background` | **2.99** | 3.0 | Lower `--input` L from 66% to about 65% (3.11 on card today) |
| Light | `--text-tertiary` on `--sidebar-hover` | **4.28** | 4.5 | Never put tertiary text on the nav hover fill, or drop tertiary to about 51% L |
| Light | `--text-tertiary` on `--muted-strong` | **4.31** | 4.5 | Same (skeleton/avatar fills carry no text; keep it that way) |
| Light | `--text-tertiary` on `--muted` / `--sidebar` | 4.68 / 4.71 | 4.5 | Passes, with little margin |
| Light | warning solid on card (UI) | 3.20 | 3.0 | Passes, with little margin |
| Dark | white on success solid | **2.17** | 4.5 | Dark solids need a near-black foreground (like `--brand-foreground`) |
| Dark | white on info solid | **2.47** | 4.5 | Same |
| Dark | white on destructive solid | **3.66** | 4.5 | Same, which affects the spec's dark "danger" button (white text) |
| Dark | white on `--destructive-hover` | **4.49** | 4.5 | Same |
| Both | `--text-disabled` on card | 2.48 / 2.51 | — | Exempt (disabled, WCAG 1.4.3), but it must never carry enabled text |

Everything else in spec §11's list passes: foreground, secondary and tertiary text on card, background and popover;
brand on its foreground in both themes; every status text on card and on its subtle fill; ring and border-strong on
card.

**Recommendation:** add a `--{tone}-foreground` token per status scale (white in light, near-black in dark) rather
than hard-coding "white". The spec's own `--brand-foreground` recipe already does this.

### 3.7 Mobile overflow at 390 px

| # | Sev | Finding | Evidence |
|---|---|---|---|
| M1 | M | **Unconditional 2-column grids with form fields**: `settings/webhooks-tab.tsx:196`, `settings/api-keys-tab.tsx:278`, `tools/mcp-tool-editor-dialog.tsx:912`. `telephony/call-controls.tsx:192` is a 3-column keypad (fine at 390). | — |
| M2 | M | **`min-w-[640px]` table in a dialog** (`settings/workspace-prices-dialog.tsx:224`). It scrolls, but it is a whole price editor behind a sideways scroll. | — |
| M3 | M | **Editable 4-column tables in dialogs**: `tools/bindings-editor.tsx:150`, `tools/pinned-arguments-editor.tsx:108`. The `ui/table` container scrolls (`ui/table.tsx:34`), but editing selects inside a sideways scroller at 390 is poor. The spec's option is stacked rows on phones. | — |
| M4 | ✓ | Lists use `ResponsiveTable` (20 uses), which swaps to cards below `md`. Other tables sit in `overflow-x-auto` wrappers. | `shared/responsive-table.tsx` |
| M5 | L | **Viewport height uses `vh`**: `min-h-screen` ×4 (`app/page.tsx:87`, `app/not-found.tsx:15`, `app/login/page.tsx:17`, `app/console/layout.tsx:30`) and `100vh` in `agents/editor/editor-shell.tsx:277`. The spec wants `dvh`. `env(safe-area-*)` is used in 7 places (session controls). | — |
| M6 | L | The breakpoint is 768 (`hooks/use-mobile.ts:3`, `md:`). The spec pivots at 820 / 640 / 900 / 1080. | — |

### 3.8 Icons

| # | Sev | Finding | Evidence |
|---|---|---|---|
| I1 | M | **Two stroke widths.** 231 `<Icon as=…>` uses get 1.75 absolute, while 104 direct `<XIcon>` JSX uses get Lucide's default 2. The wrapper itself is the per-icon `strokeWidth` the spec forbids. It should become a global `.lucide { stroke-width: 1.75px }` rule. Two hand-drawn SVGs set their own stroke (`agents/providers-section/providers-section.tsx:501`, `agents/tabs/providers-tab.tsx:381`). | greps |
| I2 | M | **Sparkle icon** at `agents/editor/summary-rail.tsx:205` (`SparklesIcon`). | — |
| I3 | L | **Intent-map drift**: `TrashIcon` ×3 files (`agents/agents-table.tsx`, `registry/credential-dialog.tsx`, `registry/credential-list.tsx`; spec `Trash2`); `PencilLineIcon` ×3 (spec `Pencil`); `AlertCircleIcon` ×3 (`datasets/dataset-list.tsx`, `knowledge/kb-documents.tsx`, `flow/flow-canvas.tsx`; spec `CircleAlert`); `CircleXIcon` in `console/shared/validation-banner.tsx`; `CheckCircle2Icon` in `overview/setup-checklist.tsx`; `RotateCwIcon`/`RotateCcwIcon` ×6 (spec `RefreshCw`); `MoreVerticalIcon` in `knowledge/kb-list.tsx` (spec `MoreHorizontal`); `ChevronLeftIcon` as back ×4 (spec `ArrowLeft`); `OctagonXIcon` in `ui/sonner.tsx`. | — |
| I4 | L | **Icon sizes** are 14/16/20/24. The spec needs 12/14/15/16/17/18/20; sidebar nav is 20 px (`app-sidebar.tsx:75`), spec 17. | — |
| I5 | ✓ | No non-Lucide icon libraries. | — |

### 3.9 Patterns the spec rules out

| # | Sev | Finding | Evidence |
|---|---|---|---|
| X1 | ✓ | Gradients on controls: none. The only gradients are decorative fades and masks in vendored `agents-ui` (`agent-session-block.tsx:119-121`, `agent-audio-visualizer-wave.tsx:369`, `tile-view.tsx:152-157`, `trigger.tsx:69`). | — |
| X2 | L | Uniform card shadows: none in console cards. Cards are ring-only (see C5 and T6 for the exceptions). | — |
| X3 | M | Spinner-only page loads: none at page level. The blank Suspense boundaries (S3) are the worse problem. | — |
| X4 | M | **Tabs vs segmented control.** `ui/tabs.tsx` `default` is a muted pill track, used as a segmented control **and** for sections of one object. Only one `variant="line"` use exists, and its underline is foreground, not accent. Settings uses 11 wrapping pill tabs (`settings/settings-tabs.tsx:42-95`). | — |
| X5 | M | **Two badge primitives**: `ui/badge.tsx` (`h-5`, 20 px, `rounded-4xl`, tone variant) and `shared/status-chip.tsx` (`h-5`/`h-6`, `rounded-xs`, optional dot; `live` uses the state meter). The spec wants one 22 px pill with a mandatory 6 px dot for status, plus a separate squarer Tag. | — |

### 3.10 Shell (spec §7.1–7.2)

- The sidebar is 232 px (spec 248), with no nav count badges and no live dot. The active state is a `brand-soft`
  fill with brand text; the spec wants a `--sidebar-active` pill with hairline, raised shadow, foreground text and an
  accent icon.
- The theme menu is a separate sidebar footer control plus a top-bar copy on mobile. The spec wants a 3-button
  switcher inside the account popover.
- There is no inset main panel (8 px inset, radius 12, hairline, raised shadow) and no frosted top bar
  (`--scrim` + blur).
- There is no phone bottom tab bar.
- The mobile nav is already a full-screen **Dialog** (`ui/sidebar.tsx:180-203`), which matches the standing
  no-drawer rule.
- The skip link targets `#console-main`, not `#main-content`, and the content wrapper is a `div` inside shadcn's
  `<main>`. It works, but the id differs from the spec.
- `app-sidebar.tsx:42` sets `role="navigation"` on the sidebar root. That is fine.

### 3.11 Sheets in the spec vs the standing rule

The user's standing rule: **no side drawers or sheets; every drawer becomes a Dialog**. It is enforced by ESLint
and recorded in `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md` §5. The spec asks for sheets in three places. All three are
product decisions, and all three already have dialog equivalents:

| Spec asks for | Where | Dialog-based equivalent (recommended) |
|---|---|---|
| **Side sheet** for in-context item editing (§6.4, §5 motion "side sheet slides 24 px") | Tools, credentials, rules, cases, webhooks | Keep the existing `DialogContent size="md|lg|xl"` panel dialogs with sticky header and footer. Do **not** build a Sheet primitive or its motion token. The spec's `--elevation-modal` and 220 ms dialog motion apply. |
| **Mobile navigation sheet** from the left (§6.4, §7.2) | Console nav ≤ 820 px, and "More" in the phone tab bar | Keep the full-screen "Menu" Dialog (`ui/sidebar.tsx:180-203`), restyled to the spec (sidebar colour, 248 px content column, same nav items, account block at the foot). "More" opens the same dialog. |
| **Sheet** as a floating layer receiving `--elevation-modal` (§1, §2.4) | Tokens | Map it to Dialog only. Leave "sheet" out of the design lint's allowed vocabulary. |

Not a side drawer (already exempted by the amendments): the `/s/[slug]` mobile transcript **bottom** sheet
(`session/session-layout.ts:45-51`, `session/session-shell.tsx:48`). Keep it unless decision D3 says otherwise.

## 4. Token mapping (existing → spec)

**Strategy: one source of values, legacy names as aliases, then retire the aliases.**
- **UI-1** writes the spec tokens as the **only** literal values in `:root` and `.dark`, and adds a Tailwind
  `@theme inline` bridge for each. Every legacy name that vendored shadcn / agents-ui components (and 800+ class
  uses) still read becomes a `var()` alias of a spec token, defined in the same file. There are no parallel values.
- **Screen packages** rename utilities to spec names in the files they own.
- **The verification package** flips the design lint to forbid legacy names outside `components/ui/` and
  `components/agents-ui/`, then deletes any alias no longer referenced.

A one-shot codemod of 863 `muted-foreground` uses in UI-1 would instead collide with every parallel screen
package.

| Existing token (uses) | Spec token | Notes |
|---|---|---|
| `--background` | `--background` | New value; working panel. Today it is also the app background, which becomes `--sidebar`. |
| `--foreground`, `--card-foreground`, `--popover-foreground` | `--foreground` | The two `*-foreground` names become aliases. |
| `--card` | `--card` | New value (pure white light). |
| `--popover` | `--popover` | — |
| `--muted` | `--muted` | — |
| — | `--muted-strong` | New: switch track, avatar, skeleton sweep. |
| `--muted-foreground` (863 class uses) | `--text-secondary` | Alias. Where it is used for labels, meta or placeholders, screen packages switch to `--text-tertiary`. |
| — | `--text-tertiary`, `--text-disabled` | New. |
| `--accent` (50), `--accent-foreground` | `--muted` / `--foreground` | shadcn "accent" = hover fill. Alias only. |
| `--secondary` (15), `--secondary-foreground` | `--muted-strong` / `--foreground` | Alias; the spec's secondary *button* is card + border + raised shadow. |
| `--primary` (39), `--primary-foreground` | `--brand` / `--brand-foreground` | **Behaviour change:** primary becomes accent (see P3). |
| `--border` | `--border` | New value. The spec's hairline (92.4% L) is lighter than today's 0.85 L, so today's "border ≥ 1.5:1" contrast pair will fail. Drop that pair from the checker (hairlines are decorative) and rely on `--input` / `--border-strong` at 3:1 for control edges. |
| — | `--border-strong` | New: input hover. |
| `--input` | `--input` | Light value adjusted to about 65% L (see §3.6). |
| `--ring` (= `--brand`) | `--ring` | Its own value now (`56% .09 190` / `72% .1 190`). |
| `--brand` | `--brand` | Hue 152 → 190 pending D1. |
| — | `--brand-hover`, `--brand-active` | New; replace the `color-mix(…brand, foreground 12%)` hacks in `ui/button.tsx:22`. |
| `--brand-foreground` | `--brand-foreground` | — |
| `--brand-soft` (42) | `--brand-subtle` | Alias. |
| `--brand-line` (34) | `--brand-border` | Alias. |
| `--brand-text` (29) | `--brand` (links, active icon) | The spec has no separate brand-text; its brand passes 4.5:1 on card in both themes. Alias to `--brand`. |
| `--success`, `--success-soft`, `--success-text` (alias of brand) | `--success-solid`, `--success-subtle`, `--success-text`, `--success-border` | **Break the alias to brand** (C1). |
| `--info`, `--info-soft`, `--info-text` | `--info-solid`, `--info-subtle`, `--info-text`, `--info-border` | — |
| `--warning`, `--warning-soft`, `--warning-text` | `--warning-solid`, `--warning-subtle`, `--warning-text`, `--warning-border` | — |
| `--danger`, `--danger-soft` (19), `--danger-text` (115) | `--destructive-solid`, `--destructive-subtle`, `--destructive-text`, `--destructive-border` | Collapse the two systems (C2). |
| `--destructive` (169), `--destructive-foreground` | `--destructive` (= solid), `--destructive-hover`, new `--destructive-foreground` | Dark foreground must be near-black (§3.6). |
| — | `--{success,info,warning,destructive}-foreground` | **Proposed addition** (a correction to the spec for dark mode). |
| `--sidebar` | `--sidebar` | Also the app background now. |
| `--sidebar-accent` | `--sidebar-hover` | Alias. |
| — | `--sidebar-active` | New active pill. |
| `--sidebar-foreground`, `--sidebar-primary*`, `--sidebar-accent-foreground`, `--sidebar-ring` | `--foreground` / `--brand*` / `--ring` | Aliases for `ui/sidebar.tsx`. |
| `--sidebar-border` | `--border` | Alias. |
| — | `--overlay`, `--scrim` | New. Replaces the `color-mix(shadow-color 24%)` backdrop in `ui/dialog.tsx:42`. |
| `--stage`, `--stage-foreground` | *(session-only survivor)* | Keep. Not in the spec; the dark call stage. |
| — | `--chart-1…8` | New. `panels/blocks/chart.tsx` has its own `seriesColor`; leave it (panel). Analytics uses them. |
| `--shadow-sm` | `--elevation-raised` | Alias. |
| `--shadow-md`, `--shadow-lg` | `--elevation-overlay` | Alias both. |
| — | `--elevation-modal` | New (dialogs). |
| `--shadow-color` | *(folded into the elevation values)* | Remove once the elevation tokens are literal. |
| `--radius-xs` (4 px, 77 uses) | `--radius-sm` (6 px) or pill (999 px) | Chips and badges → pill; kbd → 6 px. |
| `--radius-sm` (6 px) | `--radius-sm` (6 px) | Small buttons, tags, menu items. |
| `--radius-md` (8 px), `--radius` (6 px today) | `--radius` (8 px) | Buttons and inputs move to 8 px. |
| `--radius-lg` (12 px) | `--radius-lg` (12 px) | — |
| `--radius-xl` (16 px) | `--radius-dialog` (14 px) | — |
| `--radius-2xl` (20 px) | *(session-only)* | Pre-call / end-of-call card. Decide with D3. |
| `--dur-1` (120) | `--duration-fast` (100) | — |
| `--dur-2` (180) | `--duration-base` (150) | — |
| `--dur-3` (240), `--dur-4` (320) | `--duration-slow` (220) | `--dur-4` existed for sheets. The session bottom sheet keeps its own if D3 keeps it. |
| `--ease-out` | entrance easing `cubic-bezier(.16,1,.3,1)` | Value changes. `--ease-in-out` stays for the state meter; `--ease-drawer` stays session-only. |
| — | `--focus-shadow` | New (inputs, open triggers). |
| `--font-sans` (Geist) | Inter stack | D4. |
| `--font-mono` (Geist Mono) | `ui-monospace, "SF Mono", Menlo, Consolas` | D4. |
| `--font-hand`, `--font-hand-label` | *(panel-only survivors)* | Keep. |
| State-meter vars (`--meter-*`) | *(signature primitive, keep)* | Its colours move to `--brand` / `--destructive-solid` / `--text-tertiary`. |

## 5. Product decisions needed

| # | Decision | Options and evidence | Recommendation |
|---|---|---|---|
| D1 | **Accent hue** | Keep signal green (hue 152, `success === brand` today) or adopt the spec's teal (hue 190). The spec says "keep the recipe, change the hue" for an existing brand, but it also makes status a separate system. A green accent would collide with success green. | Teal 190 as specified, or another non-green hue with the same recipe. |
| D2 | **Theme default** | `lib/theme.ts:16` defaults to light; the spec defaults to system. | System. |
| D3 | **Caller page `/s/[slug]` and embed** | The spec's class theming and 14 px body, or keep the current exceptions: fixed dark "phone call" surface, 16 px body, top-centre toasts, mobile transcript **bottom** sheet, handwriting fonts for the notebook. The v2 amendments §5 exempt the bottom sheet. | Keep it as a documented caller-facing exception: tokens, icons, primitives, states and copy per the spec; fixed dark, 16 px body and the bottom sheet stay. Say so in the styleguide. |
| D4 | **Font** | Geist / Geist Mono (the v1 spec §2.3 chose them deliberately) or Inter + `ui-monospace` (this spec). | Inter, per the spec. |
| D5 | **Settings layout** | Today: 11 `?tab=` pill tabs. Spec: sticky anchor nav with scroll-spy over one column of cards. Scroll-spy over 11 sections would mount every tab's queries at once. | A vertical left nav that keeps `?tab=` routing (one section mounted), wide container, read-only card variants for non-admins, save per card. |
| D6 | **Overview stat grid** | Spec §7.4 wants 3–6 stat cards and a "New …" primary. The v1 spec §4.1 said "no hero metrics"; today's overview uses a sentence status line instead. | Adopt the spec: a small stat grid (agents, live now, sessions 7 d, failed 7 d) plus "New agent". |
| D7 | **Sheets** | In-context edit sheet and mobile nav sheet (§3.11). | Keep the rule: dialogs only, no Sheet primitive. Nothing needs converting. |
| D8 | **Phone bottom tab bar** (≤ 640 px) | A new surface. Which 3–5 destinations, per role? | Overview, Agents, Sessions, Knowledge + More (builder/admin); Overview, Sessions, Analytics + More (viewer). |
| D9 | **Forgot password** | Spec §7.4 wants a forgot-password flow with a constant "Check your email" screen. No route or API exists today. | Skip until the API has a reset endpoint (spec states don't apply to a missing flow). |
| D10 | **Sign-in showcase** | Spec: two columns with a showcase panel. The v2 amendments §1: "console-styled auth card; no marketing". | Adopt the spec's layout with a quiet showcase (dotted grid plus a small product-outcome illustration), no marketing copy. |
| D11 | **Input text size on phones** | Spec body 14 px, but iOS zooms on focus under 16 px (the reason `ui/input.tsx` is `text-base`). | `text-base` below `md`, 13.5–14 px from `md`. |
| D12 | **Permission model for controls** | Spec §8.5 says don't render unusable controls; today they are disabled with a tooltip (71 sites). Hiding the "New …" button changes discoverability for viewers. | Hide row and edit actions; replace page-level primaries with a read-only note ("Ask an admin to add connections") in the header or empty state. |

## 6. Implementation plan

Order follows the spec: tokens → primitives → shell → screens → verification. Each package owns the files listed
and touches nothing else. Anything outside its list is a hand-off note to the owning package.

### UI-1 — Tokens and theming (sequential; blocks everything)
- **Scope.**
  - Spec §2 tokens in `:root` and `.dark`, with an `@theme inline` bridge for every token (colour, radius, elevation,
    duration, easing), plus the §3.6 corrections and per-status `*-foreground` tokens.
  - Legacy names become `var()` aliases (§4 table). Break `--success → --brand`; merge `--danger*` into
    `--destructive-*`.
  - Inter with `display: "swap"` and `font-feature-settings: "cv11","ss01"`; body 14 px / 1.5 / −.003em; mono stack.
  - Theme: class-based, default `system`. Move `ThemeProvider` to the root layout so `/`, `/login` and `not-found`
    get the no-flash script too. Keep `/s/[slug]` forced dark per D3 using next-themes' `forcedTheme` or its
    wrapper class.
  - `themeColor` metadata (light/dark literal values) and a minimal `app/manifest.ts` with literal colours.
  - Global `@media (prefers-reduced-motion: reduce)` rule (1 ms, 1 iteration, meter keyframes off).
  - Global `.lucide { width:16px; height:16px; stroke-width:1.75px; flex:none }`.
  - Global `:focus-visible` outline using `--ring`.
  - Extend `web/scripts/check-contrast.mjs` with the spec §11 pairs: tertiary text on every surface, sidebar
    hover/active, status solids vs their foregrounds, border-strong, ring, and `--brand` as link text on card,
    background and muted at 4.5:1 (checked only at 3:1 in this audit). Keep the `extractBlock`, `resolveVar` and
    `checkContrast` exports that `tests/contrast-script.test.ts` imports.
  - New `web/scripts/design-lint.mjs`. It fails on:
    - hex, `rgb()` or `oklch()` colours outside `globals.css`;
    - raw px `box-shadow` or `shadow-[…]`;
    - `<select` and `window.confirm` / `alert(`;
    - `strokeWidth=` on Lucide components;
    - `Sparkle*` / `Wand*` icons;
    - palette utility classes.

    The allowlist covers `panels/**`, `components/agents-ui/**`, `contracts/**`, `shared/vendor-mark.tsx`, the
    notebook and paper theme files, and, until their packages land, `telephony/native-select.tsx` and
    `preview/preview-shell.tsx`. The allowlist shrinks package by package.
  - CSS safety check: balanced `/*` and `*/`, and no `*/` inside a comment body, in `globals.css` and any `.css`.
  - Wire both scripts into `pnpm test` (a vitest test that runs them) and add a `lint:design` script.
- **Exclusive files.** `web/src/app/globals.css`, `web/src/app/layout.tsx`, new `web/src/app/manifest.ts`,
  `web/src/lib/theme.ts`, `web/src/components/console/shell/theme-provider.tsx`, `web/src/app/console/layout.tsx`
  (provider move only), `web/scripts/check-contrast.mjs`, new `web/scripts/design-lint.mjs`,
  `web/tests/contrast-script.test.ts`, new `web/tests/design-lint.test.ts`, `web/package.json`,
  `web/tests/theme-provider.test.tsx`.
- **Acceptance.**
  - `pnpm check:contrast` passes all spec §11 pairs in both themes.
  - `pnpm lint:design` passes with the documented allowlist.
  - No `var(--x)` in the bridge points at an undefined token (a check in the lint).
  - No flash on hard load of `/console`, `/login` and `/` in dark system mode.
  - `/s/x` stays dark.
  - tsc, eslint and vitest are green.

### UI-2 — Primitives and styleguide (sequential after UI-1)
- **Scope.** Restyle spec §6 primitives **on the Radix primitives already installed**. Do not run `shadcn init`, add
  a UI kit or add Base UI.
  - **Button**: variants `primary` (brand), `secondary`, `ghost`, `danger`, `danger-outline`, `link` (text button,
    plus destructive and neutral variants); sizes sm 28 / default 34 / lg 40 / icon 34 and 28 / block.
    `scale(.98)` press. Keep `default`, `outline` and `destructive` as deprecated aliases so screens keep compiling:
    `default` → `primary`, `outline` → `secondary`, `destructive` → `danger-outline`.
  - **Form controls**: input and textarea at 36 px with `--focus-shadow`; field grid; checkbox and radio with
    `accent-color`; option card; switch 34×20; input-with-icon; search field (Escape clears, trailing X); password
    show/hide.
  - **Select** trigger and popup per §6.3. `SearchableSelect` gains groups, a row cap and a free-text option.
  - **Dialog**: 14 px radius, modal shadow, `--overlay`, sticky muted footer, phones `100vw−16px` (with D11 in
    mind). New `ConfirmDialog` with `role="alertdialog"`, a danger button and gerund busy labels, plus a
    `TypedConfirmDialog`.
  - **Popover and menu**: menu destructive item last, after a separator.
  - **Toast**: success/info 6 s, error/warning persistent, `role="alert"` for errors, `CircleAlert` icon, no
    `richColors`.
  - **Alert**: six tones.
  - **Empty state**: the dashed card, plus a **no-matches** variant (`SearchX`, "Clear filters").
  - **Skeleton**: sweep and ragged lines.
  - **Loading row** and **Progress** with steps.
  - **One status pill**: merge `ui/badge.tsx` tones and `shared/status-chip.tsx` into a 22 px pill with a
    mandatory dot, a pulsing `live`, and one shared lifecycle → tone and label map. Add a **Tag**.
  - **Data display**: Card (hairline, no shadow), list card, table (36 px muted header), meta list, stat card,
    avatar, segmented control, and tabs with the accent underline.
  - **Theme switcher and keyboard hint.**
  - **Icon**: sizes per spec §5; drop the per-icon `strokeWidth` (the global rule covers it); add 15/17/18 sizes.
  - **Friendly errors**: a mapper in `console/lib/` (status / code / network → plain sentence plus next step). It
    keeps server detail only for validation codes the API marks user-safe. `errorMessage()` routes through it.
    `ErrorBanner` gains Retry and "Ask an admin".
  - **Permissions**: replace `GatedButton` with a `PermissionNote` / read-only pattern. Keep `GatedButton` exported
    as a thin shim until the screens move.
  - **Styleguide page** at `/console/preview/styleguide` inside the shell-less `(preview)` route group: tokens,
    type, buttons, statuses, fields, segmented control, alerts, stats, a card and an empty state, in both themes.
- **Exclusive files.** `web/src/components/ui/*` **except** `ui/sidebar.tsx`; `web/src/components/shared/*`;
  `web/src/components/console/shared/*`; `web/src/components/console/lib/**` except `cost-hooks.ts` (S5 owns that one), including
  `form-errors.ts`, `roles.ts` (the D12 hinge), `api-hooks.ts`, `constants.ts` and a new `friendly-error.ts`. These
  are read-only for screen packages, which send hand-off notes to UI-2; `web/src/lib/api.ts` (error shape only);
  `web/src/components/preview/*` and a new `web/src/app/(preview)/console/preview/styleguide/page.tsx`;
  `web/tests/shared-primitives.test.tsx`, `web/tests/ui-dialog.test.tsx`, `web/tests/searchable-select.test.tsx`,
  `web/tests/preview-scenes.test.tsx`.
- **Acceptance.**
  - The styleguide renders every primitive in light and dark at 390, 768 and 1440 px.
  - Primitive tests are updated without weakened assertions.
  - The deprecated variant aliases compile everywhere.
  - Design lint and contrast are green.

### UI-3 — App shell (§7.1–7.2) (after UI-2; can overlap the first screen wave only if screens don't touch the shell)
- **Scope.**
  - Sidebar at 248 px on the app background, grouped nav with labels (Build, Connect, Observe, Settings), 34 px
    links, 17 px icons, the `--sidebar-active` pill with an accent icon and `aria-current`, optional counts or a
    live dot.
  - Account menu at the foot: workspace switcher, profile, 3-button theme switcher, sign out. This replaces the
    separate `ThemeMenu`.
  - Inset main panel: 8 px inset, radius 12, hairline, raised shadow.
  - 56 px frosted sticky top bar: breadcrumb left, API status and a bell placeholder only if notifications exist.
  - Skip link to `#main-content`.
  - At ≤ 820 px: full-bleed panel and a hamburger that opens the restyled full-screen **Menu dialog** (D7).
  - At ≤ 640 px: the phone bottom tab bar (D8), with 48 px targets, a "9+" badge cap, page padding and toasts
    lifted above it, and the bar hidden on full-screen task pages.
  - `use-mobile` breakpoint to 820.
  - Page container widths (default 1200, wide 1440, narrow 880) and responsive padding as layout utilities for
    screens to use.
- **Exclusive files.** `web/src/components/console/shell/*` (except `theme-provider.tsx`, owned by UI-1),
  `web/src/components/ui/sidebar.tsx`, `web/src/hooks/use-mobile.ts`, `web/src/hooks/use-media-query.ts`,
  `web/tests/console-shell.test.tsx`, `web/tests/console-account-menu.test.tsx`.
- **Acceptance.**
  - Shell renders at 390, 768, 820, 1440 px in both themes with no horizontal scroll.
  - The active location is visible in the sidebar, breadcrumb and tab bar.
  - Axe a11y e2e (`web/e2e/a11y.spec.ts`) is green.
  - Focus returns to the hamburger when the menu closes.

### Screen packages (after UI-3)

Each screen package:
- adopts the page template (§7.3): one-sentence intro, **exactly one primary, placed last**, demoting other
  `default` buttons;
- designs all eight states (§8), using the friendly-error mapper and removing `— ${errorMessage}` concatenation;
- replaces disabled-plus-tooltip with the permission pattern (D12);
- adds search to lists with ≥ 6 items (accent-insensitive, highlight, Escape, filters remembered and validated in
  localStorage) and a distinct no-matches state;
- replaces off-scale type, radius, shadow and duration classes and opacity-modified tokens;
- renames legacy token utilities in its own files;
- fixes intent-map icons;
- fixes 390 px overflow;
- shrinks the design-lint allowlist for its files.

Packages own `components/console/<area>/**` plus the matching `app/console/<area>/**` pages. They may **compose**
another area's components but never edit them.

| Package | Exclusive files | Notable work |
|---|---|---|
| **S1 Agents** | `components/console/agents/**`, `components/console/flow/**`, `app/console/agents/**` | Agents list (search exists; add the status segmented filter). Editor as the detail archetype: back link, status badge, refresh / danger-outline / primary. `SparklesIcon` out (`editor/summary-rail.tsx:205`). Publish popover busy label, not a spinner. Flow canvas uppercase label (`flow/node-form.tsx:594`) and 10 px text (`flow-canvas.tsx:198`). Blank `<Suspense>` in `app/console/agents/[id]/page.tsx`. `TrashIcon`, `PencilLineIcon`, `AlertCircleIcon`, `RotateCcwIcon`, `ChevronLeftIcon`. Stat-size text in `editor/cost-estimate-dialog.tsx:87`, `create/template-preview.tsx:102`. `template-tile.tsx` shadow. |
| **S2 Connect** | `components/console/connections/**`, `components/console/providers/**`, `components/console/registry/**`, `app/console/{connections,providers,keys}/**` | Connection detail gets actions and a Danger zone. `fleet-card.tsx` loading state and `text-lg` stat. Credentials search and `TrashIcon` → `Trash2`. Providers primary and blank `<Suspense>`. The `min-w-[640px]` prices table lives in `settings/workspace-prices-dialog.tsx` (owned by S6); S2 only composes it. |
| **S3 Telephony** | `components/console/telephony/**`, `app/console/telephony/**` | **Delete `native-select.tsx`**; the 13 sites move to the custom Select. Page-level primary and states. Search on numbers and calls. `call-controls.tsx:189,198` `text-lg`. |
| **S4 Build library** | `components/console/tools/**`, `components/console/knowledge/**`, `components/console/datasets/**`, `app/console/{tools,knowledge,datasets}/**` | Search on tools, kits, KB list, datasets. KB detail primary. `dataset-detail.tsx:66-80`: spinner in the header, raw `dataset.error`, Delete as the only action → Danger zone. 2-column grid in `mcp-tool-editor-dialog.tsx:912`. Editable tables in `bindings-editor.tsx:150` and `pinned-arguments-editor.tsx:108` stack on phones. Text-only loading in `tools/apps/*`. `kb-evals-card.tsx:56-70` stats. `MoreVerticalIcon`, `AlertCircleIcon`, `Loader2Icon`. Blank `<Suspense>` in `app/console/tools/page.tsx`. |
| **S5 Observe** | `components/console/overview/**`, `components/console/sessions/**`, `components/console/sessions-v2/**`, `components/console/analytics/**`, `app/console/{page.tsx,sessions,analytics}/**` | Overview archetype per D6 (stat grid, "New agent", recent list with "See all", aside); error states on `recent-sessions`/`live-now`, loading/error on `setup-checklist`. Sessions list (filters exist; add search and remember filters). Session detail (wide). Analytics: date range + Refresh in the header, tabs, chart tokens, `text-2xl` → stat card, 10 px text at `analytics-view.tsx:222`, blank `<Suspense>`. `cost-tab.tsx:52-55` stats. |
| **S6 Settings** | `components/console/settings/**`, `app/console/settings/**` | Settings archetype per D5. Read-only cards for non-admins, save per card. Search on team, API keys, agent keys, webhooks, knowledge connections. Unconditional grids at `webhooks-tab.tsx:196` and `api-keys-tab.tsx:278`. `workspace-prices-dialog.tsx:224` table on phones. Danger tab keeps the typed confirmation. Storage and workspace tab states. Appearance tab uses the shared theme switcher. |
| **S7 Caller-facing** | `components/session/**` (incl. `embed/`), `app/(session)/**` | Scoped by D3: tokens, icons, primitives, states and copy per the spec, keeping fixed-dark, 16 px body and the bottom sheet if D3 keeps them. Remove `richColors`. `session-card.tsx:29` shadow. The embed loading placeholder becomes a skeleton. `RotateCcwIcon` in `stage-view.tsx`. `Loader2Icon` in `connection-banner.tsx`. Keep `stage-view.tsx:260` `bg-black` (video letterbox; allowlist it). |
| **S8 Sign-in and public** | `app/login/**`, `app/page.tsx`, `app/not-found.tsx`, `app/console/not-found.tsx` | Sign-in archetype per D10 (380 px card, 26 px title, showcase hidden ≤ 1080 px). Password show/hide. Busy label instead of a spinner (`login-form.tsx:133,206`). Friendly errors (`login-form.tsx:64-65`). No card shadow. Blank `<Suspense>` (`app/login/page.tsx:18`). `min-h-screen` → `dvh`. Primary-last order on both not-found pages. |

**Parallel waves (no shared files within a wave).**
- Wave A: S1, S4, S6.
- Wave B: S2, S3, S5.
- Wave C: S7, S8.

S2, S3 and S5 are independent of wave A at file level. Visual coupling exists (the agent editor composes
`registry/*` and `tools/*` dialogs), so review A before B for consistency.

### V — Verification (last; sequential)
- **Scope.**
  - Contrast: the full spec §11 set in both themes.
  - Design lint with the allowlist reduced to leave-alone code only. Forbid legacy token names outside
    `components/ui/**` and `agents-ui/**`, and delete unused aliases.
  - CSS comment check.
  - Render check: every route in §2, as viewer, builder and admin, light and dark, at 390, 768 and 1440 px, failing
    on console errors and on horizontal page scroll. Reuse `web/scripts/ui-capture.mjs` and `web/e2e/preview.spec.ts`
    against the running app. Include each list's empty, no-matches, error and busy states through the preview
    scenes.
  - Axe e2e (`web/e2e/a11y.spec.ts`, `a11y-v6-19-dialogs.spec.ts`).
  - tsc, eslint and vitest green, with no weakened assertions.
  - A changelog of what changed and what was left alone.
- **Exclusive files.** `web/e2e/*`, final edits to the scripts UI-1 created (`web/scripts/check-contrast.mjs`,
  `web/scripts/design-lint.mjs` and its allowlist), `web/scripts/ui-capture.mjs`, `web/tests/design-lint.test.ts`.

### Status (2026-10-01, after V)

Every package is merged. Details, open decisions and what was left alone: `docs/ui/REPORT.md`.

| Package | Status |
|---|---|
| UI-1 Tokens | Done. Tokens, bridge, Inter, system default, reduced motion, icon rule, contrast gate and design lint landed. The two dark destructive deviations stay (V re-measured them; `docs/ui/TOKENS.md`). |
| UI-2 Primitives | Done. Styleguide at `/console/preview/styleguide`; V added the caller-page (D3) specimen. `GatedButton` survives as a shim with one caller (`registry/model-test-panel.tsx`). |
| UI-3 Shell | Done. V added the app-wide offline notice under the top bar. |
| S1 Agents | Done. V fixed two sideways scrolls on the editor found by the render check: the phone section bar's escaping `sr-only` label (786 px at 390) and the sticky header's bleed past the 20 px gutter (4 px at 768). |
| S2 Connect | Done, apart from the `GatedButton` above (D12 says hide it for viewers; its test asserts the disabled button). |
| S3 Telephony | Done. The numbers section's width cap went once `Section`'s action slot could wrap (V). |
| S4 Build library | Done. Its shared helpers moved to `components/shared` / `components/console/shared`; its list search folded into the shared one; its status overrides are now map states (V). |
| S5 Observe | Done. |
| S6 Settings | Done. |
| S7 Caller-facing | Done. `caller-error.ts` now borrows the busy and server sentences from `src/lib/friendly-error.ts`; the in-call banner also covers "offline" (V). |
| S8 Sign-in and public | Done. D9 (forgot password) still waits for an api endpoint. |
| V Verification | Done with limits: the render check ran against the dev server on :3000, which serves the main checkout (not the V branch) with the admin bypass on, so it saw one role (owner) and main's code. Legacy token aliases are **not** deleted: `panels/**` (210 uses) and `agents-ui/**` (56) still read them, and 50 uses remain in 24 other files (follow-up in REPORT). |

## 7. Tests that will need selector or expectation updates

Status after V: every row is done. The telephony tests drive the custom Select (no native selects left);
`shared-primitives` asserts the new classes (V added the `Section` action slot); `theme-provider` asserts the
system default; `ui-dialog` covers `alertdialog`; `login-page` asserts the plain "That email and password don't
match" copy and that the raw message is absent; the stage tests still find `bg-stage` and keep captions out of
`opacity-60`; `contrast-script` pins the new pairs (V added the search highlight). The insurance notebook snapshot
is unchanged since the baseline. V also updated `ui-data-display` (segmented names are now "Failed (2)"),
`console-settings-search` (new `lkap:list:` key plus a migration case), `console-list-search` and
`agents-list-search` (the shared helper), and made `panel-blocks`' composite order test deterministic.

| Test | Why | Package |
|---|---|---|
| `web/tests/console-telephony.test.tsx` (24 `fireEvent.change`, several on native selects, e.g. `:206,295`) | Native select → Radix Select: use `userEvent` on the combobox and option roles | S3 |
| `web/tests/console-telephony-numbers.test.tsx` (`:218,378,379`) | Same | S3 |
| `web/tests/console-telephony.test.tsx:619-620` | Asserts the `text-warning-text` class name | S3 |
| `web/tests/shared-primitives.test.tsx:115-117,192,256-258,279,345,360,375-377` | Asserts `rounded-xs` on the status chip, and grid, sticky and `divide-y` class names on PageHeader, Field, ResponsiveTable, Section and DescriptionList | UI-2 |
| `web/tests/theme-provider.test.tsx:80-98` | Asserts the light default | UI-1 |
| `web/tests/ui-dialog.test.tsx` | Dialog layout, sizes and roles change (`alertdialog`) | UI-2 |
| `web/tests/searchable-select.test.tsx` | Groups, row cap, free-text option | UI-2 |
| `web/tests/console-shell.test.tsx`, `console-account-menu.test.tsx` | Theme menu moves into the account popover; skip-link id | UI-3 |
| Tests querying `data-variant` / `data-tone` (e.g. `console-create-agent.test.tsx` 6, `console-sessions-table.test.tsx` 4, `console-editor-shell.test.tsx` 4, `console-agents-list.test.tsx` 3, `console-provider-slot.test.tsx` 3, `console-pipeline-parts.test.tsx` 3, `console-model-test.test.tsx` 3) | If Button variant or badge tone names change, keep `data-variant` values stable through the aliases, or update the assertions | UI-2 / owning screen |
| Tests asserting copy such as "Working…" or "Try again" | Busy and retry labels change to spec copy | UI-2 |
| `web/tests/login-page.test.tsx:78-85` | Asserts the raw server message "invalid email or password" is shown. It will change once the friendly-error mapper lands; keep a plain equivalent ("That email and password don't match") | S8 |
| `web/tests/home.test.tsx` | Queries links by name only; should survive a reorder. Re-run it | S8 |
| `web/tests/stage-view.test.tsx:113-114,150`, `stage-view-framing.test.tsx:102-103` | Class assertions on the stage (`bg-stage`, `opacity-60`) | S7 (keep the stage token so they stay valid) |
| `web/tests/contrast-script.test.ts` | New pairs and token names | UI-1 |
| `web/tests/__snapshots__/insurance-notebook.test.tsx.snap` | **Must not change** (panels are left alone) | — |

## 8. Leave alone

- `web/src/components/agents-ui/**`: vendored LiveKit Agents UI (blue control-bar toggles, visualizer hex colours,
  fades). Theme it only through tokens it already reads.
- `web/src/panels/**`: panels render **caller content** and agent-driven UI. The insurance notebook paper theme
  (`notebook-styles.ts`, `blocks/notebook/paper-theme.ts`), canvas and signature ink colours, the chart's series
  colours, the `form.tsx:224` native select and the handwriting fonts belong to the panels owner. Only flag them.
- `web/src/contracts/lkap-contracts.d.ts` (generated), `web/public/widget.js` (embed loader), `web/tests/fixtures`.
- The `shadcn/tailwind.css` import (custom `data-open` / `data-closed` variants the primitives depend on) and
  `tw-animate-css`.
- The state meter CSS and component (signature element). Its tokens are remapped; its geometry and keyframes are
  unchanged apart from the global reduced-motion rule.
- `shared/vendor-mark.tsx:51` per-vendor computed tint (a legitimate, allowlisted exception).
- `app/api/console/[...path]/route.ts`, `middleware.ts`: no UI.

---

## Appendix — reproduce the counts

Run from `web/src`.

- Palette classes: `grep -rnoE '\b(bg|text|border|ring|fill|stroke|from|to|via|outline|divide|shadow|decoration|accent|caret|placeholder)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|black|white)(-[0-9]{2,3})?(/[0-9]+)?\b' --include='*.ts*' .`
- Opacity-modified tokens: `grep -rhoE '\b(bg|text|border|ring|fill|stroke|outline|divide)-(foreground|background|muted|muted-foreground|primary|secondary|accent|destructive|brand|card|popover|input|border|info|success|warning|danger|sidebar[a-z-]*)/[0-9]+\b' --include='*.tsx' components app`
- Permission tooltips: `grep -rnE 'disabled=\{!canWrite' --include='*.tsx' components`
- Raw error concatenation: `grep -rnE '— \$\{errorMessage|: \$\{errorMessage' --include='*.tsx' components`
- Spec-token contrast: the culori script used for §3.6 is reproducible from the token tables in
  `docs/ui/DESIGN-SYSTEM.md` §2.1–2.3.
