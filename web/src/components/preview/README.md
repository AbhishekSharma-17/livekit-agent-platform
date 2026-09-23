# Preview route (WP-10)

Contract source: `docs/UI_UX_SPEC.md` §7.11. `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md`
§3 leaves WP-10 unchanged and hands `preview/**` to V2-11 once WP-10 lands —
this file is that hand-off: the scene registry contract V2-11 (block scenes)
and any later package build on.

## Route

`/console/preview/panels` (unlisted, `robots: {index:false}`, no console
sidebar). It renders as `app/(preview)/console/preview/panels/page.tsx` — a
**route group**, not a nested `app/console/preview/layout.tsx` — because
`app/console/layout.tsx` (WP-1) unconditionally wraps every `/console/**`
route in `ConsoleShell` (sidebar, top bar, `ApiHealthBanner`) with no
path-based bypass, and a nested layout can only add chrome, never remove an
ancestor's. The route group keeps the URL exactly as the spec names it while
skipping `app/console/layout.tsx` entirely; `app/(preview)/layout.tsx` is its
own root (fonts + `metadata.robots`), same pattern as `app/(session)/layout.tsx`.
**This is a deviation from the letter of the spec's file list
(`app/console/preview/**`) forced by WP-1's shell having no per-path escape
hatch — flagged in the WP-10 hand-off note, not hidden.**

## The scene registry — the contract V2-11 (and anyone else) builds on

`src/components/preview/scenes.tsx` exports `SCENES: Record<string, SceneDefinition>`:

```ts
interface SceneDefinition {
  id: string;
  label: string;
  /** Every param this scene accepts and its allowed values. */
  params: Record<string, readonly string[]>;
  /** Value used when a param is missing or invalid in the query string. */
  defaults: Record<string, string>;
  /** Surfaces (light/dark) worth capturing when a combo doesn't override it. */
  surfaces: readonly ("light" | "dark")[];
  /** Every combination worth a screenshot — drives `scene=list` and the capture script. */
  combos: SceneCombo[];
  /** Pure render from resolved (fully-defaulted) params. No room, no hooks
      beyond what the component being shown already owns, no next/font. */
  render: (params: Record<string, string>) => React.ReactNode;
  /** `data-testid`s the render must contain — asserted by
      `tests/preview-scenes.test.tsx` and by the capture script. */
  testIds: string[];
  /** True for the six scenes that render the dark, fixed session surface
      (§2.1) — `PreviewShell` sets `data-surface="session"` only for these,
      never for `primitives` (a console-like, light-by-default page). */
  isSessionSurface?: boolean;
  /** True when the scene's own root already renders a `<main>` (every
      `SessionCardScreen` screen — precall, ended, unavailable — does).
      `PreviewShell` skips its own `<main>` wrapper for these, otherwise axe's
      `landmark-no-duplicate-main` / `landmark-main-is-top-level` fire on
      every shot. */
  ownsMain?: boolean;
}
```

**Adding a scene** (this is the whole procedure — nothing else in the preview
route changes): add one entry to `SCENES` in `scenes.tsx` with its own
`render`, `params`/`defaults`/`combos`/`testIds`. `?scene=list` and the
capture script pick it up automatically; add its expected `data-testid`s to
`tests/preview-scenes.test.tsx`.

V2-11 owns `preview/**` after WP-10 lands and is expected to add a `blocks`
scene (`status`/`notes`/`checklist`/`activity` from `panels/generic/blocks.tsx`,
per `panels/README.md`) the same way.

### `?scene=list`

The self-describing entry (not a `SceneDefinition` — handled directly by
`page.tsx`): dumps `sceneListPayload()` as JSON inside
`<pre data-testid="scene-list">`, one row per shootable combo:

```json
[{ "scene": "session", "name": "side-listening", "query": "scene=session&layout=side&state=listening&test=0", "surfaces": ["dark"] }, ...]
```

`web/scripts/ui-capture.mjs` fetches this once and iterates it — it never
hard-codes a scene or param list, so a new scene needs no capture-script
change.

## Scenes shipped by WP-10

| Scene | Params | Notes |
|---|---|---|
| `session` | `layout` (side\|wide), `state` (every `AgentUiState` + `audio-blocked`), `test` (0\|1) | Real `SessionShell` + `StageView` + `ConnectionBanner` + the real `TestModeBar`, a static transcript placeholder, `ControlBarPlaceholder` (the vendored control bar needs a room — see below). `wide` uses the insurance notebook, `side` the generic panel, matching production pairing. The `listening` combo also ships a `surface=light` shot per §2.2 ("the preview route renders the session shell in both to prove it"); every other state is dark-only (the session surface is dark, fixed). |
| `notebook` | `fixture` (blank\|auto\|flood) | `resolvePanel("insurance_notebook")` against the WP-9 golden fixtures (`web/tests/fixtures/`). |
| `generic` | — | `resolvePanel("generic")` against its one fixture. |
| `precall` | `devices` (idle\|requesting\|granted\|denied\|unsupported), `test` (0\|1) | The real, injectable `PreCallCard` — no `getUserMedia` call. |
| `ended` | `test` (0\|1) | The real `EndOfCallCard`. |
| `unavailable` | `kind` (not_found\|not_published\|unreachable), `test` (0\|1) | The real `SessionUnavailable`. |
| `blocks` (V2-11) | `type` (the 12 `BlockType`s), `state` (filled\|empty\|submitted) | One block alone via `<Block>`, against `panels/blocks/__fixtures__`. Combos: every type filled and empty, plus `form` submitted (25). |
| `composite` (V2-11) | `layout` (side\|wide) | The real `SessionShell` with the composite panel showing all twelve blocks (`CompositePreview`, `block-scenes.tsx`). The agent editor's composer renders its live preview through the same `CompositePreview`, with the author's draft layout. |
| `primitives` | — | Every WP-0 shared primitive (`StateMeter`, `StatusChip`, `Badge`, `CapabilityBadge`, `Kbd`, `Button`, `Field`, `DescriptionList`, `CopyButton`, `RelativeTime`, `VendorMark`, `EmptyState`, `Icon`) in every state, per §7.1's acceptance line. Light by default (console-like), `surface=dark` also captured. |

## Known seam gaps (reported, not worked around)

- **`camera=1` / a local video preview cannot be simulated.**
  `StageView.localTrack` and `.videoTrack` are LiveKit `TrackReference`s tied
  to a real `Room`/`Participant` — there is no way to construct one outside a
  connected room. The `session` scene therefore never sets `localTrack` or
  `videoTrack`; camera-on and avatar-video states are **not** screenshot-able
  through this route. Request for WP-8 (or V2-11, which plugs avatar/video
  blocks into the same seam): accept a `localPreview?: ReactNode` /
  `posterUrl?: string` escape so the preview route can fake the pixels
  without a track.
- **The vendored control bar cannot render without a room** (`useChat()` /
  room context throw outside a `RoomContext.Provider`), which is exactly why
  §7.11 item 2 pre-authorizes a static `ControlBarPlaceholder` "with the same
  dimensions" — implemented in `control-bar-placeholder.tsx`. It is a visual
  stand-in only; it never reflects `visibleControls()`/`lockedControls()`
  beyond a fixed camera/chat toggle for realism.
- **`app/console/preview/**` (spec's literal path) became a route group**
  (`app/(preview)/console/preview/panels/**`) — see the route section above.
