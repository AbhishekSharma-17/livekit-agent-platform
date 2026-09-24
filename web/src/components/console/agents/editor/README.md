# Agent editor shell (WP-3)

Spec: `docs/UI_UX_SPEC.md` §4.3, §4.8, §4.9, §6, §7.4 and `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md` §2.3 / §3 (WP-3 "Change").
Entry point: `../agent-editor.tsx` (`<AgentEditor agentId sections? />`), rendered by `app/console/agents/[id]/page.tsx`.

## Files

| File | What it does |
|---|---|
| `types.ts` | `EditorSectionDef`, `EditorSectionProps`, `EditorSlots`, `EditorExtension` — the contract below. |
| `builtin-sections.tsx` | The eight built-in sections (ids, labels, icons, order, issue paths, keyword heuristics). |
| `extensions.ts` | `EDITOR_EXTENSIONS` — the **shared, append-only** plug-in list. |
| `sections.ts` | `EDITOR_SECTIONS`, `EDITOR_SLOTS` (built-ins + extensions, resolved once), `DEFAULT_SECTION_ID`. |
| `registry.ts` | Pure `resolveEditorSections`, `visibleSections`, `resolveEditorSlots`. |
| `editor-shell.tsx` | Sticky header (back link, `h1` name + pencil, slug + copy, Draft/Live, connection chip, mode chip, unsaved indicator, Test call, Publish/Unpublish, Save, overflow → Delete), three-column layout, Summary dialog. |
| `section-nav.tsx` | Vertical list (≥ 1024 px) / scrollable segmented bar (< 1024 px) with validation dots and roving focus. |
| `summary-rail.tsx` | "What this agent does". |
| `publish-popover.tsx` | Publish / Unpublish (§4.9). Also exports `publicUrl`, `SaveOutcome`. |
| `test-call-menu.tsx` | Test call split button (§4.8). |
| `validation-map.ts` | `ValidationResult` / 422 details / zod errors → per-section `EditorIssue`s. |
| `editor-context.tsx` | `useEditorContext`, **`useSectionIssues`**. |
| `issue-list.tsx` | `<SectionIssueList />` (the "Issues" list at the top of a section). |
| `unsaved-guard.tsx` | `beforeunload` + in-app link confirmation. |
| `form-values.ts` | Agent ⇄ form conversion; builds the `PUT` body (AgentConfig v2, nothing dropped). |
| `header-chips.tsx`, `use-connection.ts` | Read-only connection and mode chips. |
| `sections/*` | WP-3's own section content: `recording`, `limits`, and the `flow` placeholder. |

## Section registry contract (WP-4, WP-5, V2-11, V2-13, V2-16, and later)

A section is an `EditorSectionDef`:

```ts
{
  id: string;             // the ?section= value; never rename once shipped
  label: string;          // sentence case
  icon: LucideIcon;
  order: number;          // built-ins: providers 10 · instructions 20 · flow 30 · panel 40 · tools 50 · knowledge 60 · recording 70 · limits 80
  Component: React.ComponentType<{ agent: AgentOut }>;
  visible?: (ctx: { agent: AgentOut; mode: "prompt" | "flow" }) => boolean;   // built-in flow placeholder: mode === "flow" (the V2-16 builder drops it)
  issuePaths?: string[];  // config-relative prefixes whose issues this section owns ("pipeline", "voice", …)
  issueKeywords?: RegExp; // fallback for plain api strings with no path (§7.14 heuristic)
  issueKeywordPriority?: number;
  layout?: "default" | "full";   // "full": content also takes the rail's column (flow canvas)
  ownsIssueList?: boolean;       // true = you render <SectionIssueList /> yourself
}
```

Rules:

1. **Section components read and write the form with `useFormContext<AgentEditorForm>()`** (`components/console/lib/schemas.ts`). The shell owns `<FormProvider>`, Save, ⌘S, dirty state and the unsaved guard. Always `setValue(…, { shouldDirty: true })` so Save and the guard see the change.
2. **The form holds only the fields a section edits.** Everything else in the stored `AgentConfig` v2 is merged back from the loaded agent at save (`form-values.ts → buildAgentUpdate`), so a section never has to round-trip fields it does not show. If your section starts editing a field that is not in `agentEditorFormSchema` yet (e.g. `config.panel`, `config.qa`, `config.flow`, `pipeline.vad`), that is a one-line schema addition plus a line in `toFormValues`/`buildAgentUpdate` — request it from the integrator (those files are WP-3's). Already in the form for you: `mode`, `connection_id` (sent only when changed), `limits`, `allowed_origins`, `config.recording`, and all v1 config fields.
3. **Issues.** The shell renders the section's issue list above the section (unless `ownsIssueList`). For field-level hints call `useSectionIssues()` — it is safe outside the shell (empty result), so section tests do not need the editor:
   ```ts
   const { issues, errors, warnings, general, issueFor, focusIssue } = useSectionIssues(); // active section by default
   const sttIssue = issueFor("pipeline.stt");   // exact path or any path below it
   ```
   Put `data-issue-path="<config-relative path>"` (e.g. `pipeline.stt`, `limits.rate_per_ip_per_min`) on the control an issue should focus, or give it `name="config.<path>"`; "Show field" uses those after `form.setFocus`.
4. **Deep links.** `/console/agents/<id>?section=<id>`. Unknown or hidden ids fall back to `providers`. Use `useEditorContext()?.goToSection(id)` to switch programmatically (it updates the URL with `router.replace`).

### Adding or replacing a section without touching WP-3 files

Export an `EditorExtension` from a module **you own**, then add one import and one array entry to `extensions.ts` (shared, append-only; never edit someone else's entry):

```ts
// your file, e.g. components/console/agents/panel-section/extension.ts (V2-11)
import { LayoutPanelLeftIcon } from "lucide-react";
import type { EditorExtension } from "@/components/console/agents/editor/types";
import { PanelComposer } from "./panel-composer";

export const panelComposerExtension: EditorExtension = {
  id: "V2-11",
  sections: [{ id: "panel", label: "Panel & capabilities", icon: LayoutPanelLeftIcon, order: 40,
               Component: PanelComposer, issuePaths: ["panel", "capabilities", "ui_panel_id"] }],
};

// extensions.ts
import { panelComposerExtension } from "@/components/console/agents/panel-section/extension";
export const EDITOR_EXTENSIONS: EditorExtension[] = [panelComposerExtension];
```

- A full `sections[]` entry with an existing `id` **replaces** that section; a new `id` **adds** one (pick an `order` between the built-ins).
- `sectionPatches: [{ id, patch }]` changes part of an existing section (label, `visible`, `issuePaths`, …) without re-declaring it.
- The resolution is pure (`registry.ts`); tests can pass their own list with `<AgentEditor sections={…} />`.

Who plugs in where:

| Package | How |
|---|---|
| **WP-4** (providers) | Keeps editing `tabs/providers-tab.tsx` (`ProvidersTab`, no props); the built-in `providers` entry already points at it. No registry change needed. |
| **WP-5** (sections) | Same for `tabs/{instructions,panel,tools,knowledge}-tab.tsx` (`ToolsTab` receives `{ agent }`; the others may ignore it). Replace the "Save & validate" copy in `tools-tab.tsx` (the button is now "Save"). |
| **V2-11** (panel composer) | Replace `panel` via an extension from `components/console/agents/panel-section/**`. Once it edits `config.panel`, ask for `config.panel` in the form schema; until then `buildAgentUpdate` keeps `config.panel.panel_id` in step with `ui_panel_id`. |
| **V2-13** (connections, modes, slots) | Replace `providers` from `components/console/agents/providers-section/**`; fill the `connectionChip` slot (change popover). `connection_id` is already a form field. |
| **V2-16** (flow builder) | Replace `flow` (`layout: "full"`, visible in both modes — prompt mode shows an explainer whose "Switch to flow" opens the mode chip's dialog); fill `modeChip` (switch dialog; set the `mode` form field) and `versionHistory` (the rail's "History" link). |
| **V2-17 / V2-18** | `testCallItems` ("Call a number", "Test chat"), `headerActions` ("Add to website"). V2-18's `allowed_origins` editor already exists in the `limits` section — patch or replace that section rather than adding a second editor. |

### Slots

| Slot | Default | Contract |
|---|---|---|
| `connectionChip` | read-only chip (`header-chips.tsx`) | `ComponentType<{ agent }>`; last extension wins |
| `modeChip` | read-only Prompt/Flow chip | `ComponentType<{ agent }>`; last extension wins |
| `versionHistory` | disabled "History" button next to "Version n" | `ComponentType<{ agent }>` rendered next to the version |
| `testCallItems` | — | `ComponentType<{ agent, dirty }>[]`, rendered inside the Test call `DropdownMenuContent`; render `DropdownMenuItem`s, open any dialog as a sibling controlled by state |
| `headerActions` | — | `ComponentType<{ agent }>[]`, rendered left of Test call |

## Layout contract with the console shell (WP-1)

The header is `position: sticky` under the top bar: `top: var(--console-topbar-height, 3.5rem)` (< 1024 px) / `var(--console-topbar-height, 3rem)` (≥ 1024 px), matching `shell/top-bar.tsx` (`h-14` / `lg:h-12`). If the top bar height changes, set `--console-topbar-height` on the shell. The header bleeds to `main`'s gutter (`px-4 md:px-6 lg:px-8`). Below 1024 px only the actions row and the section bar stay pinned (the title block scrolls away). The top bar's trail is set with WP-1's `useSetBreadcrumbs` ("Agents / <name>").

## Testing notes

- `tests/console-editor-shell.test.tsx` (shell with stand-in sections), `tests/validation-map.test.ts` (mapping + registry), `tests/console-editor-form-values.test.ts` (payload), `tests/console-editor-sections.test.tsx` (recording, limits, flow placeholder), `tests/console-zod-resolver.test.ts`.
- **Radix popovers/menus in jsdom:** floating-ui's `isTopLayer` calls `element.matches(":popover-open")` and `(":modal")` on every ancestor; jsdom's nwsapi takes seconds per call, so an open "hangs" (~8 s). Answer those two selectors with `false` in a `beforeAll` (see the top of `console-editor-shell.test.tsx`) and popovers, dropdown menus and dialogs opened from menu items all work in ~30 ms.
