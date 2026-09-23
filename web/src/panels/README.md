# Panels (WP-9)

`registry.ts` maps `PackManifest.ui_panel_id` → `PanelDefinition`. Panels are pure renderers of `UiState` (docs/CONTRACTS.md §11): the only way out is `perform({action: "ui_action", …})`. Design contract: `docs/UI_UX_SPEC.md` §5.5 and §7.10; v2 delta: `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md` §3 (WP-9 unchanged, the generic panel is the block reference).

Panels are on the session bundle path: import shared primitives **per file** (`@/components/shared/state-meter`), never the barrel, and never anything from `@/components/console/**`.

## Block renderers — `generic/blocks.tsx`

The visual reference for the v2 `status` / `notes` / `checklist` / `activity` blocks. Each takes one slot of the envelope and nothing else (no room, no `perform`, no context), so the generic panel, a pack panel and the v2 panel composer render the same thing.

| Export | Props | Renders |
|---|---|---|
| `StatusBlock` | `status?: StatusStamp \| null`, `progress?: number \| null`, `label?: string` (progress bar's accessible name, default "Session progress"), `className?` | `StatusChip` (tone from `status.tone`, default label "Not started") + `%` + a 4 px `bg-brand` bar. One `role="progressbar"` per panel; the fill animates `transform: scaleX()` (§2.5). |
| `NotesBlock` | `notes: Note[]`, `empty?: ReactNode` | `<ul>` of `NoteRow`: a tone dot (`bg-info`/`bg-success`/…), the text, `kind · formatTime(ts)`. |
| `ChecklistBlock` | `items: ChecklistItem[]`, `empty?: ReactNode` | `<ul>` of `ChecklistRow`: `CheckGlyph`, label (struck through when done), `StatusChip warning sm` "Required" for a blocking item, hint. |
| `ActivityBlock` | `events: ActivityEvent[]`, `limit?: number`, `empty?: ReactNode` | `<ul>` of `ActivityRow`, newest first: `StateMeter xs` (`thinking`) while `running`, a tone dot otherwise, headline + `StatusChip danger sm` "Urgent", then `label · phase · duration`. |
| `AssetsBlock` | `assets: AssetRef[]`, `urls: Map<string, string>`, `empty?: ReactNode` | 2-column grid of `AssetTile` (`rounded-md`); a tile without bytes shows "Receiving…". |
| `PanelBlock` | `title: string`, `count?: number`, `children`, `className?` | The block wrapper: sentence-case `h3`, count chip, hairline above. **Not** the shared `Section` — that is a bordered card and cards do not nest inside the panel column. |
| `PanelEmpty` | `children` | One-sentence in-block empty. |
| `CheckGlyph` | `done: boolean`, `className?` | Static checkbox glyph (`components/ui/checkbox.tsx` look). Deliberately not the Radix `Checkbox`: checklist state is agent-owned, so it must not be focusable or announced as a control. |

Each row carries a `data-slot` (`panel-note`, `panel-checklist-item`, `panel-activity-item`, `panel-asset`) and activity rows a `data-phase`, for tests and the capture harness.

## Font variables (the notebook's handwriting)

`insurance_notebook/notebook-styles.ts` loads **no** fonts (the runtime Google Fonts `@import` is gone, UI_UX_SPEC §2.3). The paper reads two CSS variables and falls back to the locally-named faces:

```css
--hand:       var(--font-hand, "Caveat"), ui-rounded, cursive;
--hand-label: var(--font-hand-label, "Patrick Hand"), ui-rounded, cursive;
```

| Variable | Face | Weights | Used for |
|---|---|---|---|
| `--font-hand` | Caveat | 400, 500, 600 | note lines, blanks, the margin "!", frame captions, the pen |
| `--font-hand-label` | Patrick Hand | 400 | page head, "Claim details" summary, tags, the rubber stamp |

The surface that renders a session provides them with `next/font/google` — `app/(session)/layout.tsx` (WP-8) and the preview layout (WP-10). A panel module cannot call `next/font` itself: `panels/registry.ts` is a transitive import of every session-surface test and the loader only runs under Next's compiler. `globals.css` already maps both into `@theme inline` (`font-hand`, `font-hand-label`).

The exact lines the owning layout needs:

```tsx
import { Caveat, Patrick_Hand } from "next/font/google";

const hand = Caveat({ subsets: ["latin"], weight: ["400", "500", "600"], display: "swap", variable: "--font-hand" });
const handLabel = Patrick_Hand({ subsets: ["latin"], weight: "400", display: "swap", variable: "--font-hand-label" });

// on the wrapper that contains the panel:
<div className={`${hand.variable} ${handLabel.variable} dark …`} data-surface="session">
```

## `PanelDefinition.handleRequest` (optional)

`handleRequest?: (request: UiRequest) => UiRequestResult | Promise<UiRequestResult>` — the session room forwards the `lkap.ui.request` methods that target a panel affordance (UI_UX_SPEC §5.4) and declines politely when a panel does not define it.

Payload keys are fixed by CONTRACTS-V2 §4.4 (ruling R-V2-3b): `open_dialog {dialog, params?}`, `focus {target}`, `request_video_source {source}`, `toast {message, tone?}`. The last two are answered by the room, not by a panel.

**Insurance notebook** (`handleNotebookRequest`, exported from `insurance_notebook/index.tsx`):

| Request | Result |
|---|---|
| `open_dialog {dialog: "packet"}` | opens the adjuster packet → `{ok: true, payload: {dialog: "packet"}}` |
| same, but the workflow has not written a packet | `{ok: false, payload: {error: "the adjuster packet has not been written yet"}}` |
| same, but no notebook is mounted | `{ok: false, payload: {error: "the claim notebook is not open"}}` |
| `open_dialog` without a string `dialog` (e.g. the old `{id: …}`) | `{ok: false, payload: {error: "open_dialog needs a `dialog` name"}}` |
| `open_dialog {dialog: <other>}` | `{ok: false, payload: {error, dialogs: ["packet"]}}` (`NOTEBOOK_DIALOGS`) |
| any other method | `{ok: false, payload: {error}}` |

`payload.params` is accepted and ignored — the packet takes no parameters. The handler reaches the mounted dialog through `openPacketDialog()` in `packet-dialog.tsx` (a module-level opener set), because `PanelDefinition` is a module object while the dialog's open state belongs to the component.
