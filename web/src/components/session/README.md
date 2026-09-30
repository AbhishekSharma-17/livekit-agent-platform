# Session surface (WP-8)

Contract source: `docs/UI_UX_SPEC.md` §5 (+ `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md`
§3, which keeps WP-8 unchanged and plugs the v2 avatar/video blocks and the
embed layouts into the `StageView` seam described below).

Nothing here imports from `components/console/**` — the session bundle stays
console-free (`/s/[slug]` budget: Size ≤ 400 kB; `tests/flow-bundle-split.test.ts`
checks the static import graph).

## Design system (S7, decision D3)

The caller page follows `docs/ui/DESIGN-SYSTEM.md` (tokens, intent-map icons,
the `components/shared` / `components/ui` primitives, the section 8 states,
plain copy) with three documented exceptions from `docs/ui/AUDIT.md` D3:

- **Always dark.** The root `ThemeProvider` forces `dark` on `/s/*`
  (`lib/theme.ts`); the `.dark` wrapper in `app/(session)/layout.tsx` is the
  first-paint guarantee.
- **16 px body text** (`text-base` on the layout).
- **The transcript is a bottom sheet** below `lg` (not a dialog): it is the
  caller's own transcript, not console editing. Escape closes it; closed, it
  leaves the tab order.

Rules that are easy to miss here:

- Toasts use the app's one `Toaster` policy (no `richColors`, no position
  override).
- Caller actions get `PHONE_TOUCH_TARGET` (48 px below `lg`); the vendored
  control bar gets `CONTROL_BAR_TOUCH`.
- Errors a caller reads go through `callerConnectError` (`caller-error.ts`)
  or `deviceErrorHint` (`session-state.ts`), never raw api or browser text.
  `friendlyError` lives under `components/console/**`, so it can't be used.
- The only raw colour is the self-view's `bg-black` letterbox
  (`stage-view.tsx`, allowlisted in `scripts/design-lint-allowlist.txt`).
- `web/src/panels/**` is exempt (caller content); this package styles only
  the chrome around it.

## Composition

```
app/(session)/layout.tsx        .dark + data-surface="session" + --font-hand*
└── s/[slug]/page.tsx           server load → classifyConnectError → kind
    └── SessionExperience       pre-call ↔ live ↔ end-of-call ↔ unavailable
        ├── PreCallCard         §5.1 (injectable `devices`, useMicCheck at runtime)
        ├── EndOfCallCard       §5.6
        ├── SessionUnavailable  §5.7
        └── LiveSession         one attempt: TokenSource + Room + useSession
            └── SessionRoom     room state → §5.4 state model → everything below
                └── SessionShell            §5.3 layouts (pure)
                    ├── AgentStage          hooks → StageView (pure)
                    ├── ConnectionBanner    reconnecting only
                    ├── panel               resolvePanel(uiPanelId)
                    └── SessionControls     wrapper around the vendored bar
```

Pure (renderable with no room, no browser permissions, no timers):
`StageView`, `SessionShell`, `PreCallCard`, `EndOfCallCard`,
`SessionUnavailable`, `TestModeBar`, `ConnectionBanner`, plus the logic in
`session-state.ts` and `session-layout.ts`.

## The `StageView` seam

`stage-view.tsx` is the one place the stage is drawn. `agent-stage.tsx` is the
only thing that knows LiveKit: it resolves the avatar video, the agent audio
level (`useTrackVolume`) and the local self-view, and passes them down.

| Prop | Type | Meaning |
|---|---|---|
| `agentState` | `AgentUiState` | The §5.4 state model. Drives the meter state, the caption, the `data-state` attribute and which overlay shows. |
| `agentName` | `string` | Display name on the stage and in the overlays' copy. |
| `videoTrack?` | `TrackReference` | **Avatar / agent video.** When present it fills the well, sized to the resolved frame (V6-26 — see below) and replaces the meter; tapping toggles a forced `cover`. |
| `framing?` | `AvatarFraming \| null` | V6-26, `AvatarOptions.framing`. `undefined`/`"auto"`/`null` defers to `declaredAspect`, then a 16:9 guess. |
| `fit?` | `AvatarFit \| null` | V6-26, `AvatarOptions.fit`. `undefined`/`null` renders like `"contain"` (the crop-free default). |
| `declaredAspect?` | `AvatarFraming \| null` | V6-26, the selected avatar provider's `ProviderCapabilities.avatar_aspect` — a pre-connect guess so the well doesn't jump once the real frame arrives. |
| `onAspectChange?` | `(ratio: number) => void` | V6-26. The resolved well aspect, for a caller with no shell box of its own (the `video` block) to size its own wrapper. |
| `audioTrack?` | `TrackReference` | The agent's audio track. Kept in the contract for visualizer plug-ins; the level itself arrives as `level`. |
| `localTrack?` | `TrackReference` | Camera or screen share → self-view PiP (144 px desktop / 96 px mobile, tap to grow to 50 %). |
| `localLabel?` | `"You" \| "Screen"` | Chip on the self-view. |
| `compact?` | `boolean` | The `wide` layout's rail: a 72 px strip on phones, a 200 px rail from `lg`. The shell owns the box; the stage fills it. |
| `elapsedMs?` | `number` | Elapsed call time. Rendered as a corner chip only on the video stage (the caption carries the state otherwise; the top strip always has the timer). |
| `audioBlocked?` | `boolean` | §5.2 — shows the "Tap to hear <agent>" overlay and swaps the caption. |
| `onEnableAudio?` | `() => void` | Wired to `useStartAudio().mergedProps.onClick`. |
| `level?` | `number` (0–1) | Drives the meter bars while speaking (`StateMeter level`). |
| `failureReasons?` | `string[] \| null` | Shown inside the failure overlay. |
| `onRetry?` / `onLeave?` | `() => void` | The failure overlay's two actions. |

Rules for anything plugging in:

- **Add props, never branches.** `StageView` has no internal state machine; the
  only local state is "is the self-view / video enlarged". A new source (an
  avatar provider, a `video` block, an embed) supplies `videoTrack` and, if it
  needs a different box, gets it from `SessionShell` — not from the stage.
- **Layout belongs to the shell.** `session-layout.ts` owns every breakpoint
  decision (`sessionLayoutModel(layout)`); V2-18's embed layouts extend that
  model, not `StageView`.
- **State belongs to `session-state.ts`.** `toAgentUiState()` is the only
  mapping from room + agent state to `AgentUiState`; add a state there and
  every surface (stage, strip, banner, controls) follows.

## Avatar framing (V6-26, PLAN-V6 §3)

`avatar-framing.ts` is the one place the well's aspect and fit/position are
computed (`resolveFrame`) and the one place a track's real dimensions are
measured (`useMeasuredVideoAspect`, via a `ref` on `VideoTrack` + the video
element's `loadedmetadata`/`resize` events). It has no LiveKit import, so
`components/console/**` shares it for the agent editor's framing preview
without pulling the session bundle in.

Precedence: the real measured aspect (once known) → an explicit `framing`
(the admin's override) → `declaredAspect` (the provider registry's
documented `avatar_aspect`, a pre-connect guess) → a 16:9 guess (the
pre-V6-26 assumption, so a stored agent with none of this data renders
exactly as it always has, minus the crop). `fit` defaults to `contain`
(the whole avatar visible, letterboxed on `bg-stage`); `cover` biases its
`object-position` to the upper third so a face is the last thing lost.

`AgentPublicOut.avatar_framing` (V6-26b, docs/v6/_asks.md #151) carries
`AvatarOptions.framing`/`.fit` as stored plus the avatar provider's registry
`avatar_aspect` (`declared_aspect`), `null` without an avatar. `SessionRoom`
maps it (`stageAvatarFraming()`) onto `AgentStage`'s
`framing`/`fit`/`declaredAspect` props unless its own `avatarFraming`
override is set. It reads `ConnectResponse.agent` once connected, so
`/s/[slug]`, the embed and the console's `?mode=test` preview (whose
pre-connect `toPublicAgent()` copy has no `declared_aspect`) all render the
api's value. A `null` value keeps the crop-free `auto` + `contain` default.

## Audio priming (§5.2)

1. The Start click (a user gesture) creates an `AudioContext` and resumes it
   **synchronously** in `SessionExperience.start()`.
2. `LiveSession` builds that attempt's `Room` with
   `webAudioMix: { audioContext }` (so LiveKit mixes through the already
   unblocked context) and `audioCaptureDefaults: { deviceId }` (the microphone
   the visitor picked in the device check). `useSession(tokenSource, { room })`.
3. Because `webAudioMix` is an object, `livekit-client` does not close the
   context on disconnect. `SessionExperience` owns it and closes it when the
   call ends (or when a new one is primed) — never from a mount-scoped effect,
   which StrictMode would fire mid-call, and never leaving a closed context to
   be reused, which `Room` accepts silently.
4. Fallback: `room.startAudio()` once connected; if `canPlaybackAudio` stays
   false, `StageView` shows the "Tap to hear" overlay wired to `useStartAudio`.

## The vendored control bar

`components/agents-ui/**` is never forked. `session-controls.tsx` restyles it
from outside with descendant arbitrary variants
(`CONTROL_BAR_BRAND_ON`: brand tokens replacing the hard-coded `blue-500`
"on" state, and "END CALL" on the destructive status tokens with a ring on
hover instead of a darker fill; `CONTROL_BAR_TOUCH`: 48 px targets below
`lg`), hides controls a state does not allow
(`visibleControls`), and makes the rest inert while connecting/reconnecting
(`lockedControls` + a capture-phase click guard, because the vendored bar takes
no per-control `disabled`).
