# LKAP web redesign — report

The console, the sign-in and public pages, and the caller page (`web/`) now follow `docs/ui/DESIGN-SYSTEM.md`. Packages UI-1, UI-2, UI-3, S1–S8 and V are complete. Per-package status is in `docs/ui/AUDIT.md` (sections 6 and 7). Tokens are in `docs/ui/TOKENS.md`, primitives in `web/src/components/shared/README.md`.

## 1. What changed

### Foundation (UI-1, UI-2, UI-3)

**Tokens**
- All tokens live in `web/src/app/globals.css`, with a Tailwind bridge.
- Two themes, class-based; the default follows the system.
- The accent is teal (hue 188–190), separate from the status colours.
- Inter at 14 px / 1.5, with one type, radius, shadow and duration scale.
- Reduced motion is honoured everywhere; one global icon rule sets size and stroke.

**Guard rails**
- `pnpm check:contrast` checks 212 pairs across both themes.
- `pnpm lint:design` forbids colour literals, raw shadows, palette and opacity-colour utilities, native selects, browser dialogs, per-icon stroke widths, sparkle icons and CSS comment hazards.

**Primitives**
- Buttons: one primary per view, placed last, with gerund busy labels.
- Fields, one custom Select and a searchable Combobox.
- Dialogs: `alertdialog` for destructive steps, typed confirmation for irreversible ones.
- A toast policy, alerts in six tones, empty and no-matches states, skeletons and progress steps.
- One status pill fed by one lifecycle map; tags, data display, segmented controls and tabs.
- The page template and the permission pattern.
- `friendlyError`, so people see plain copy.
- A styleguide at `/console/preview/styleguide`.

**Shell**
- A 248 px grouped sidebar, an inset main panel and a 56 px top bar.
- A skip link.
- A full-screen Menu dialog at 820 px and below.
- A phone tab bar at 640 px and below.

### Screens (S1–S8)

**Every screen**
- Follows the page template.
- Has all eight states.
- Shows plain errors with a next step.
- Has search on lists of 6 or more, and the permission pattern.
- Has no sideways scroll at 390 px.

**Area by area**
- **Agents:** list archetype; the editor is the detail archetype; flow canvas polish.
- **Connect:** connections, providers and credentials, with a Danger zone.
- **Telephony:** no native selects; a page-level primary; search on numbers and calls.
- **Build library:** search and filters; a Danger zone on detail pages; editable tables stack on phones.
- **Observe:**
  - the overview gets a stat grid (D6);
  - the sessions list gets search and remembered filters;
  - session detail is a wide page;
  - analytics gets range and Refresh in the header, tabs and chart tokens.
- **Settings:** vertical navigation that keeps `?tab=` (D5); each card saves on its own; read-only versions for non-admins.
- **Caller page:** follows the system, with the D3 exceptions; the embed loads behind a skeleton.
- **Sign-in and public pages:**
  - sign-in has a quiet showcase (D10);
  - the home and not-found pages put their primary action last.

### Verification and consolidation (V)

**One list search** (`components/shared/list-search.tsx`)
- Brand-subtle highlight.
- Saved under `lkap:list:<id>`; old keys move over once on read.

**Shared homes for S4's helpers**
- `write-gate` and `danger-zone-card` → `components/console/shared`.
- `stacked-table` and `status-error` → `components/shared`.

**Status map**
- Adds `enabled` and `importing`; `disabled` now reads "Disabled".
- `WORKING_STATES`, plus an optional `LifecycleBadge progress`.
- Screen-level tone and label overrides are gone.

**friendlyError**
- Moved to `src/lib/friendly-error.ts` (pure, no console code).
- The caller page borrows the busy and server sentences via `codeMessage` and `statusMessage`.

**Offline**
- One notice in the console shell; the API-down alert hides while it shows.
- The caller page's call banner also covers going offline.

**Accessibility and layout**
- Segmented control items are named "Live (1)", not "Live1".
- `Section` actions wrap; the telephony cap is removed.
- Two sideways scrolls fixed on the agent editor: the phone section bar's escaping hidden label, and the header bleeding past the 20 px gutter. A sticky `PageHeader` gets the same correction between 640 and 900 px.

**Checks and tests**
- Contrast: the search-highlight pair is added; the dark destructive tokens were re-measured and kept (O1).
- Styleguide: a D3 specimen.
- Flaky `panel-blocks` composite order test made deterministic (fix in the test only).
- Allowlist cut to commented, deliberate exceptions, with a test that holds each line to that.
- New `web/e2e/render-check.spec.ts`.

## 2. Left alone on purpose

**Leave-alone trees**
- `web/src/panels/**`: caller-facing panel content. The notebook paper theme, ink, chart series, the `form.tsx` native select and the handwriting fonts belong to the panels owner. The notebook snapshot is unchanged.
- `web/src/components/agents-ui/**`: vendored; themed only from outside.
- `web/src/contracts/**`, `web/public/widget.js`, the API proxy and the middleware.

**Kept by design**
- The state meter's geometry and keyframes.
- The vendor-mark tint and the stage letterbox: both allowlisted, with reasons.

**Deferred**
- **Legacy token aliases are not deleted.** `panels/**` (about 210 uses) and `agents-ui/**` (56) read them, and about 50 uses remain in 24 other files (F1, O2).
- **One `GatedButton` remains:** Test model in `registry/model-test-panel.tsx` (F2, O3).

## 3. Open product decisions

| # | Decision | Outcome |
|---|---|---|
| D1 | Accent hue | Adopted: teal |
| D2 | Theme default | Adopted: follows the system |
| D3 | Caller page | Adopted: always dark, 16 px body, bottom sheet; in the styleguide |
| D4 | Font | Adopted: Inter |
| D5 | Settings layout | Adopted: vertical nav keeping `?tab=` |
| D6 | Overview stats | Adopted |
| D7 | Sheets | Adopted: dialogs only, plus the D3 sheet |
| D8 | Phone tab bar | Adopted, per role |
| D9 | Forgot password | **Open**: needs an API endpoint |
| D10 | Sign-in showcase | Adopted: quiet showcase |
| D11 | Phone input size | Adopted: 16 px on phones |
| D12 | Permissions | Adopted, with one leftover (O3) |

| # | New decision | Evidence | Options |
|---|---|---|---|
| O1 | Dark destructive tokens stay at 70% / 65% | At the spec values, the agents-ui "off" state reads 4.42 and danger hover text reads 4.28 (TOKENS.md) | Accept as the dark palette, or restyle the agents-ui "off" state from outside plus a ~61% hover and return to the spec |
| O2 | Retire the legacy aliases | Panels and agents-ui depend on them | Migrate the other 24 files and lint them, or ask the panels owner to migrate and then delete |
| O3 | Test model for viewers | D12 says hide it; it is disabled with a tooltip, and its test asserts that | Hide it (one assertion changes), or keep it as an exception |
| O4 | Wording for `disabled` | V chose "Disabled" to pair with "Enabled"; no connection state maps to it | Keep, or use "On / Off" |
| O5 | Notifications bell (spec 9) | No notification feed exists | Build it, or keep it out of scope |
| O6 | Per-role render check | The dev server runs with the admin bypass | Provide builder and viewer accounts, or a dev role switch |

Package-level product questions raised along the way (kept here so none is lost):
- **Agents (S1):** typed confirmation plus a Danger zone for deleting an agent; read-only editor fields for viewers; search in the starter gallery; hide internal config paths in the create-agent error list; rename the inline rename "Save".
- **Build library (S4):** Tools filtered by kind rather than status; views without a primary (Ragie knowledge base, enabled Apps tab); one "Add ▾" menu instead of four add buttons on phones; vendor text shown when it reads as plain copy (pinned by tests).
- **Connect (S2):** vendor test messages such as "401 Unauthorized" and the key form's "Show value" toggle (pinned by tests); typed delete for connections vs a plain confirm for keys; Fleet Stop confirms but Restart doesn't; "Manage keys" as the Providers primary.
- **Telephony (S3):** "Add trunk" as the page primary although LiveKit-hosted numbers need no trunk; rule delete now confirms; Hang up stays one click; call search only covers the loaded 50 calls.
- **Observe (S5):** Sessions and Analytics without a primary; exact 7-day Overview counts via `/analytics/summary`; no per-person time zone or 12/24-hour setting yet; "Forget this caller" now a read-only note for non-admins.
- **Settings (S6):** API keys admin-only; "Your prices" hidden for non-admins; webhook deliveries show the user's own endpoint error; phone nav switches at 767 px.
- **Caller page (S7):** stage failure overlay shows LiveKit's own reasons (pinned by a test); `viewport-fit=cover` for notched phones; whether screen readers should read the transcript during voice calls.
- **Sign-in (S8):** "check the API" vs "the server" in the server-error copy; the redirect label always says "Opening the console…".

## 4. Verification

**Gates (in `web/`)**
- `tsc --noEmit`: clean.
- `pnpm lint`: 0 errors (12 warnings, all in files V didn't touch).
- `vitest run`, twice: 3173/3173 both times.
- `check:contrast`: 212/212. Design lint: pass.

**Render check**
- Scope: 40 routes × 2 themes × 3 widths = 240 shots; 0 unreachable, 0 redirects.
- Limits: the V run's server served the main checkout before V merged, and only as owner.
- Console errors: 2 shots, an intermittent dev-only hydration warning on settings tabs (F3).
- Overflow: only the agent editor, fixed in V and checked by applying the same styles to the live page.

**Axe**
- `a11y.spec.ts`: 22 passed, 1 failed (F5).
- Dialog specs: all 3 passed; the Add-kit test was run with a real agent id.

## 5. Follow-ups

- **F1** Migrate about 50 legacy alias uses outside `ui/`, `agents-ui/` and `panels/`, then add a lint rule.
- **F2** Move Test model off `GatedButton`, then delete the shim.
- **F3** Check the settings-tabs hydration warning under `next start`.
- **F4** Done 2026-10-01: re-run against the dev server serving the merged code — 40 routes × 2 themes × 3 widths reached, 0 horizontal overflow, 0 console errors on the final pass (a first pass hit dev-server connection resets on 6 routes, which passed on re-run; the intermittent settings-tabs hydration warning (F3) appeared once).
- **F5** For the panels owner: axe `scrollable-region-focusable` on `src/panels/blocks/chart.tsx:74`, the chart block's scroll region (filled state).
