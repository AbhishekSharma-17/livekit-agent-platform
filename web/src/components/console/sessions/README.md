# Sessions list and detail (WP-7)

Spec: `docs/UI_UX_SPEC.md` §4.10, §6, §7.8 and `docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md` §1, §2.4, §3 (WP-7 "Change").
Routes: `app/console/sessions/page.tsx` → `<SessionsTable />`; `app/console/sessions/[id]/page.tsx` → `<SessionDetailView sessionId />` (`?tab=` is read client-side).

## Files

| File | What it does |
|---|---|
| `sessions-table.tsx` | List: status chips, agent / date / channel / connection selects (all URL-synced: `?status= &agent= &range= &channel= &connection= &page=`), `ResponsiveTable` (Agent, Status, Channel, Duration, Started, Mode, Turns), 25 rows per page, row links. |
| `session-model.ts` | Pure list helpers: status labels/tones, the muted swept-failure rule (`sweptReason`), `sessionDurationMs`, `filterSessions`, `paginate`, channel/mode labels, `formatUsd`. |
| `use-session-queries.ts` | WP-7's reads: `useSessionList()` (`GET /v1/sessions?limit=500`), `useAllSessionEvents(id)` (pages `GET /v1/sessions/{id}/events` with `after_id`, 1000 per page), `useConnectionNames()`. |
| `session-detail-view.tsx` | Detail: header (agent → editor link, status/channel/mode/recording/QA chips, started, duration, config version, connection, cost, room + copy), stats strip, and the tab bar from the registry. Re-exports `formatUsageLabel`/`formatUsageValue`. |
| `session-usage.tsx` | `usage` → label/value pairs and provider/model rows (`describeUsage`, `UsageGroups`). Never JSON. |
| `timeline-model.ts` | Pure timeline: turn source, tool pairing by `call_id`, filters, state-track and `collapse` folding, minute markers, `formatOffset`. |
| `session-timeline.tsx` | Timeline tab (default): filter chips, "Jump to" minute index (sticky), rows. |
| `session-transcript.tsx`, `session-panel-tab.tsx`, `session-raw-events.tsx` | The other three built-in tabs. |
| `details-disclosure.tsx` | `DetailsDisclosure` + `CodeBlock`: the **only** place raw JSON may appear. |
| `detail/types.ts` | `SessionTabDef`, `TimelineEventKind`, `SessionDetailSlots`, `SessionDetailExtension`: the contract below. |
| `detail/builtin-tabs.tsx`, `detail/builtin-event-kinds.ts` | WP-7's tabs and timeline row kinds. |
| `detail/extensions.ts` | `SESSION_DETAIL_EXTENSIONS`: the **shared, append-only** plug-in list. |
| `detail/registry.ts` | Pure `resolveSessionTabs`, `visibleTabs`, `pickTab`, `resolveEventKinds`, `resolveSessionDetailSlots`. |
| `detail/resolved.ts` | `sessionTabs()`, `sessionEventKinds()`, `sessionDetailSlots()` (built-ins + extensions, resolved lazily on first use), `DEFAULT_SESSION_TAB_ID = "timeline"`. |

## Tab registry contract (V2-14 and later)

A tab is a `SessionTabDef`:

```ts
{
  id: string;          // the ?tab= value; never rename once shipped
  label: string;       // sentence case
  icon: LucideIcon;
  order: number;       // built-ins: timeline 10 · transcript 20 · panel 60 · raw 90
                       // → recording 30 · cost 40 · qa 50 (amendments §1 order)
  Component: React.ComponentType<{ session: SessionDetailOut }>;
  visible?: (ctx: { session: SessionDetailOut }) => boolean;   // default: always shown
}
```

Rules:

1. **A tab gets the `SessionDetailOut` and nothing else.** Fetch anything more with your own hooks. For the event list, call `useAllSessionEvents(session.id)` from `use-session-queries.ts`; it is the same query the Timeline and Raw events use, so react-query fetches once. Mutations (e.g. QA re-score) should invalidate `["sessions", session.id]`. That prefix covers the detail (`useSessionDetail`) and the events.
2. **No raw JSON outside a disclosure.** Put payloads in `<DetailsDisclosure><CodeBlock value={prettyJson(x)} /></DetailsDisclosure>`. `prettyJson` is in `timeline-model.ts`.
3. **Degrade when fields are empty.** The running api still returns pydantic defaults for most v2 fields (see "API gaps"), so use `visible` to hide a tab that has nothing to show (e.g. Recording when `session.recording?.status` is `"none"`/missing), or render a compact `EmptyState`.
4. **Deep links.** `/console/sessions/<id>?tab=<id>`. Unknown or hidden ids fall back to `timeline`. Selecting the default tab removes the param.
5. Use WP-0 primitives (`StatusChip`, `DescriptionList`, `EmptyState`, `RelativeTime`, `ResponsiveTable`) and token utilities. No raw `var(--color-*)` in CSS strings (it resolves to light values in dark mode).

### Timeline row kinds

Every `SessionEventOut.type` other than the core five (`user_turn`, `agent_turn`, `tool_call_started`, `tool_call_ended`, `agent_state`, which the timeline renders itself and extensions cannot override) is drawn from a `TimelineEventKind`:

```ts
{
  type: string;                                   // e.g. "handoff"
  filter: "turns" | "tools" | "state" | "errors" | "other";   // which chip shows it
  tone?: "neutral" | "info" | "success" | "warning" | "danger"; // warning/danger = soft surface row
  icon?: LucideIcon;
  title: (payload) => string;                     // "Moved to Billing"
  summary?: (payload) => ReactNode;               // one line, no JSON
  collapse?: boolean;                             // consecutive rows merge into one "×n" row
  hidden?: boolean;                               // Raw events only
}
```

A type with no kind renders as a neutral "Other" row titled after the type (`form_submitted` → "Form submitted"), with its payload under "Details". Built-ins: `session_started`, `session_ended`, `workflow_run` (Tools chip), `metrics` (collapsed "Usage updated ×n"), `error` (danger), `escalation` (warning). The v2 types listed in CONTRACTS-V2 §3 (`handoff`, `block_update`, `form_submitted`, `dtmf`, `transfer`, `recording`) are **V2-14's** to add as kinds.

### Adding tabs or row kinds without touching WP-7 files

Export a `SessionDetailExtension` from a module **you own**, then add one import and one array entry to `detail/extensions.ts` (shared, append-only; never edit someone else's entry):

```ts
// your file, e.g. components/console/sessions-v2/extension.ts (V2-14)
import { CoinsIcon, AudioLinesIcon, GaugeIcon, ArrowRightLeftIcon } from "lucide-react";
import type { SessionDetailExtension } from "@/components/console/sessions/detail/types";
import { RecordingTab } from "./recording-tab";
import { CostTab } from "./cost-tab";
import { QaTab } from "./qa-tab";
import { RescoreButton } from "./rescore-button";

export const sessionsV2Extension: SessionDetailExtension = {
  id: "V2-14",
  tabs: [
    { id: "recording", label: "Recording", icon: AudioLinesIcon, order: 30, Component: RecordingTab,
      visible: ({ session }) => (session.recording?.status ?? "none") !== "none" },
    { id: "cost", label: "Cost", icon: CoinsIcon, order: 40, Component: CostTab },
    { id: "qa", label: "QA", icon: GaugeIcon, order: 50, Component: QaTab },
  ],
  eventKinds: [
    { type: "handoff", filter: "other", icon: ArrowRightLeftIcon,
      title: (p) => `Moved to ${String(p.to ?? "next step")}`, summary: (p) => (p.from ? `from ${String(p.from)}` : null) },
  ],
  slots: { headerActions: [RescoreButton] },
};

// detail/extensions.ts
import { sessionsV2Extension } from "@/components/console/sessions-v2/extension";
export const SESSION_DETAIL_EXTENSIONS: SessionDetailExtension[] = [sessionsV2Extension];
```

- A `tabs[]` entry with an existing `id` **replaces** that tab (e.g. V2-11 swapping the panel tab for a composer-aware one). A new `id` **adds** one.
- `tabPatches: [{ id, patch }]` changes part of an existing tab (`label`, `visible`, `order`, …) without re-declaring it.
- `eventKinds` with an existing `type` replace that kind; core types are ignored.
- Slots: `headerActions: ComponentType<{ session }>[]`, rendered right of the title, concatenated in extension order.
- Resolution is pure (`detail/registry.ts`). Tests can pass `<SessionDetailView sessionId tabs={resolveSessionTabs(BUILTIN_SESSION_TABS, [myExtension])} />` and `<TimelineView eventKinds={resolveEventKinds(BUILTIN_EVENT_KINDS, [myExtension])} … />`.

## Interpretations (not spelled out in the spec)

- **Turn source.** The worker records each turn twice: live as `user_turn`/`agent_turn` events and again in the final `transcript`. The timeline and transcript use the stored transcript when it has turns. They fall back to the turn events when none was posted. Merging both would show every line twice.
- **`metrics` events** (up to ~90 % of a call's events) sit in the "Other" chip and fold into one "Usage updated ×n" row per run.
- **Usage** hides zero-valued counters. `model_usage` becomes provider/model rows.
- The "Panel at end of call" tab uses the agent's `ui_panel_id` (the session carries none). `composite` and deleted agents fall back to the generic panel with a note.

## API gaps worked around

- `GET /v1/sessions` paginates (`limit` ≤ 500, `offset`) but has no `channel` / `connection_id` / `from` / `to` filters (CONTRACTS-V2 §3 lists them). The list fetches the newest 500 and filters/paginates client-side, and says so when `total` exceeds that. Switch `useSessionList` to server paging once those filters land.
- The sessions router's `_to_out` does not map `channel`, `connection_id`, `cost_usd`, `disposition`, and the detail has no `recording`/`cost`/`latency`/`qa` mapping. The values are pydantic defaults (`channel: "web"`, `connection_id: null`, …), so the connection filter matches nothing and the v2 header chips stay hidden until the api fills them.
- No turn counter in `usage`: the list's Turns column is "—"; the detail counts turns from the transcript.

## Testing notes

`tests/console-sessions-table.test.tsx` (list + `session-model`), `tests/console-session-detail-view.test.ts` (usage formatting, header, tabs, `?tab=`, panel snapshot), `tests/session-timeline.test.tsx` (merge order across ISO and epoch seconds, tool pairing, filters, folding, the registry). Radix `Tabs` switch on `mouseDown` (`fireEvent.mouseDown(tab, { button: 0 })`). If a test opens a Radix `Select`, add the `:popover-open`/`:modal` override from `tests/console-editor-shell.test.tsx`.
