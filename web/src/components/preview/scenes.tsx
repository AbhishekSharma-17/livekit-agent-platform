/**
 * The preview scene registry (docs/UI_UX_SPEC.md §7.11). See
 * `src/components/preview/README.md` for the full contract V2-11 builds on.
 *
 * A "scene" is a pure function of a resolved query string: no room, no API
 * call, no browser storage. `?scene=<id>&<param>=<value>...` renders it;
 * `?scene=list` (handled by the page, not a registry entry) dumps every
 * scene's known-good combinations as JSON for the capture script.
 */
import * as React from "react";
import {
  BellIcon,
  BotIcon,
  KeyRoundIcon,
  RadioIcon,
} from "lucide-react";

import type { AgentUiState, MeterState } from "@/components/shared/agent-state";
import { AGENT_UI_STATES, METER_STATES } from "@/components/shared/agent-state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { PageHeader } from "@/components/shared/page-header";
import { Section, SectionRow } from "@/components/shared/section";
import { Field } from "@/components/shared/field";
import { DescriptionList } from "@/components/shared/description-list";
import { CopyButton } from "@/components/shared/copy-button";
import { RelativeTime } from "@/components/shared/relative-time";
import { VendorMark } from "@/components/shared/vendor-mark";
import { CapabilityBadge, CAPABILITY_BADGE_META, type CapabilityKind } from "@/components/shared/capability-badge";
import { Kbd } from "@/components/shared/kbd";
import { EmptyState } from "@/components/shared/empty-state";
import { Input } from "@/components/ui/input";

import { SessionShell } from "@/components/session/session-shell";
import { StageView } from "@/components/session/stage-view";
import { ConnectionBanner } from "@/components/session/connection-banner";
import { TestModeBar } from "@/components/session/test-mode-bar";
import { PreCallCard } from "@/components/session/pre-call-card";
import { EndOfCallCard } from "@/components/session/end-of-call-card";
import { SessionUnavailable, type SessionUnavailableKind } from "@/components/session/session-unavailable";
import type { MicDevices } from "@/hooks/use-mic-check";
import type { MicCheckStatus } from "@/components/session/session-state";

import type { SessionLayout } from "@/components/session/session-layout";
import { resolvePanel } from "@/panels/registry";
import type { BlockType } from "@/panels/composite/layout";
import { ControlBarPlaceholder } from "./control-bar-placeholder";
import {
  BLOCK_SCENE_STATES,
  BLOCK_TYPES,
  BlockScene,
  CompositePreview,
  FIXTURE_LAYOUT,
  blockSceneCombos,
  type BlockSceneState,
} from "./block-scenes";
import {
  NOTEBOOK_FIXTURE_IDS,
  PREVIEW_GENERIC_AGENT,
  PREVIEW_INSURANCE_AGENT,
  genericState,
  notebookState,
  previewPerform,
  type NotebookFixtureId,
} from "./fixtures";

export type Surface = "light" | "dark";

export interface SceneCombo {
  /** Every param this combo sets, already resolved (never partially applied). */
  params: Record<string, string>;
  /** Surfaces worth capturing for this combo; falls back to the scene's own list. */
  surfaces?: readonly Surface[];
  /** Suffix appended to the capture filename, e.g. `-connecting`. Derived when omitted. */
  name?: string;
}

export interface SceneDefinition {
  id: string;
  label: string;
  /** Every param this scene accepts and its allowed values (the contract). */
  params: Record<string, readonly string[]>;
  /** Values used to fill a param missing from the query string. */
  defaults: Record<string, string>;
  /** Surfaces to shoot when a combo doesn't override it. */
  surfaces: readonly Surface[];
  /** Every combination worth a screenshot — drives `scene=list` and the capture script. */
  combos: SceneCombo[];
  /** Pure render from resolved (defaulted) params. */
  render: (params: Record<string, string>) => React.ReactNode;
  /** `data-testid`s the rendered scene must contain (asserted by tests and the capture script). */
  testIds: string[];
  /**
   * This scene renders the dark, fixed session surface (§2.1) — `PreviewShell`
   * sets `data-surface="session"` only for these. `primitives` is a console-like
   * light-by-default page and must not force `color-scheme: dark`.
   */
  isSessionSurface?: boolean;
  /**
   * The scene's own root already renders a `<main>` (every `SessionCardScreen`
   * screen does), so `PreviewShell` must not wrap it in a second one —
   * `landmark-no-duplicate-main` / `landmark-main-is-top-level`.
   */
  ownsMain?: boolean;
}

function resolve(scene: SceneDefinition, raw: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of Object.keys(scene.params)) {
    const value = raw[key];
    out[key] = value && scene.params[key].includes(value) ? value : scene.defaults[key];
  }
  return out;
}

/** Filename suffix for a combo: `layout=wide&state=failed` → `wide-failed`. */
function comboName(combo: SceneCombo, paramOrder: string[]): string {
  if (combo.name) return combo.name;
  return paramOrder
    .map((key) => combo.params[key])
    .filter((value): value is string => Boolean(value) && value !== "0")
    .join("-");
}

/* -------------------------------------------------------------------------- */
/* session — StageView/SessionShell through every AgentUiState                */
/* -------------------------------------------------------------------------- */

type SessionStateParam = AgentUiState | "audio-blocked";
const SESSION_STATES: SessionStateParam[] = [...AGENT_UI_STATES, "audio-blocked"];

function sessionSceneParams(params: Record<string, string>): {
  layout: SessionLayout;
  agentState: AgentUiState;
  audioBlocked: boolean;
  testMode: boolean;
} {
  const layout: SessionLayout = params.layout === "wide" ? "wide" : "side";
  const raw = params.state as SessionStateParam;
  const audioBlocked = raw === "audio-blocked";
  const agentState: AgentUiState = audioBlocked ? "listening" : (raw as AgentUiState);
  const testMode = params.test === "1";
  return { layout, agentState, audioBlocked, testMode };
}

function SessionScene({ params }: { params: Record<string, string> }) {
  const { layout, agentState, audioBlocked, testMode } = sessionSceneParams(params);
  const isWide = layout === "wide";
  const agent = isWide ? PREVIEW_INSURANCE_AGENT : PREVIEW_GENERIC_AGENT;
  const panel = resolvePanel(agent.ui_panel_id);
  const state = isWide ? notebookState("auto") : genericState();
  const level = agentState === "speaking" ? 0.65 : undefined;

  return (
    <SessionShell
      layout={layout}
      panelTitle={panel.title}
      agentName={agent.name}
      agentState={agentState}
      elapsedMs={125_000}
      testBar={testMode ? <TestModeBar backHref={`/console/agents/${agent.id}`} /> : undefined}
      banner={<ConnectionBanner agentState={agentState} />}
      stage={
        <StageView
          agentState={agentState}
          agentName={agent.name}
          compact={isWide}
          elapsedMs={125_000}
          audioBlocked={audioBlocked}
          onEnableAudio={() => {}}
          level={level}
          failureReasons={agentState === "failed" ? ["The agent couldn't join the room in time."] : null}
          onRetry={() => {}}
          onLeave={() => {}}
        />
      }
      transcript={
        <ul data-testid="preview-transcript" className="flex flex-col gap-3 overflow-y-auto p-4 text-sm">
          <li className="text-muted-foreground">
            <span className="text-foreground font-medium">{agent.name}</span> — Hi, thanks for calling. Can you tell me what happened?
          </li>
          <li className="text-muted-foreground">
            <span className="text-foreground font-medium">You</span> — There was a small kitchen fire this morning.
          </li>
        </ul>
      }
      transcriptCount={2}
      panel={
        <panel.Component
          state={state}
          assets={new Map()}
          agent={agent}
          sessionId="preview-session"
          perform={previewPerform}
          transcript={[]}
          connectionState="connected"
        />
      }
      controls={<ControlBarPlaceholder camera={Boolean(agent.capabilities.camera)} chat={Boolean(agent.capabilities.chat_input)} />}
    />
  );
}

const sessionScene: SceneDefinition = {
  id: "session",
  label: "In-call session",
  params: {
    layout: ["side", "wide"],
    state: SESSION_STATES,
    test: ["0", "1"],
  },
  defaults: { layout: "side", state: "listening", test: "0" },
  surfaces: ["dark"],
  // §2.2: "the tokens are complete in both [themes]... the preview route
  // renders the session shell in both to prove it" — one combo per layout
  // also gets a light shot; every other state stays dark-only (the session
  // surface is dark, fixed, per §2.1).
  combos: (["side", "wide"] as const).flatMap((layout) => [
    ...SESSION_STATES.map((state) => ({
      params: { layout, state, test: "0" },
      surfaces: state === "listening" ? (["dark", "light"] as const) : undefined,
    })),
    { params: { layout, state: "connecting", test: "1" }, name: `${layout}-connecting-test` },
  ]),
  render: (params) => <SessionScene params={params} />,
  testIds: ["session-shell", "stage-view"],
  isSessionSurface: true,
};

/* -------------------------------------------------------------------------- */
/* notebook / generic — the two panels against the golden fixtures            */
/* -------------------------------------------------------------------------- */

function NotebookScene({ params }: { params: Record<string, string> }) {
  const fixture = params.fixture as NotebookFixtureId;
  const panel = resolvePanel("insurance_notebook");
  return (
    <div className="bg-stage h-dvh w-full overflow-y-auto p-4">
      <div className="mx-auto max-w-3xl">
        <panel.Component
          state={notebookState(fixture)}
          assets={new Map()}
          agent={PREVIEW_INSURANCE_AGENT}
          sessionId="preview-session"
          perform={previewPerform}
          transcript={[]}
          connectionState="connected"
        />
      </div>
    </div>
  );
}

const notebookScene: SceneDefinition = {
  id: "notebook",
  label: "Insurance notebook panel",
  params: { fixture: NOTEBOOK_FIXTURE_IDS },
  defaults: { fixture: "auto" },
  surfaces: ["dark"],
  combos: NOTEBOOK_FIXTURE_IDS.map((fixture) => ({
    params: { fixture },
    surfaces: fixture === "blank" ? (["dark", "light"] as const) : undefined,
  })),
  render: (params) => <NotebookScene params={params} />,
  testIds: ["insurance-notebook"],
  isSessionSurface: true,
};

function GenericScene() {
  const panel = resolvePanel("generic");
  return (
    <div className="bg-stage h-dvh w-full max-w-md overflow-y-auto border-l border-border">
      <panel.Component
        state={genericState()}
        assets={new Map()}
        agent={PREVIEW_GENERIC_AGENT}
        sessionId="preview-session"
        perform={previewPerform}
        transcript={[]}
        connectionState="connected"
      />
    </div>
  );
}

const genericScene: SceneDefinition = {
  id: "generic",
  label: "Generic panel",
  params: {},
  defaults: {},
  surfaces: ["dark", "light"],
  combos: [{ params: {} }],
  render: () => <GenericScene />,
  testIds: ["generic-panel"],
  isSessionSurface: true,
};

/* -------------------------------------------------------------------------- */
/* blocks / composite — the v2 panel blocks (V2-11)                           */
/* -------------------------------------------------------------------------- */

const blocksScene: SceneDefinition = {
  id: "blocks",
  label: "Panel blocks",
  params: { type: BLOCK_TYPES, state: BLOCK_SCENE_STATES },
  defaults: { type: "form", state: "filled" },
  surfaces: ["dark"],
  combos: blockSceneCombos(),
  render: (params) => <BlockScene type={params.type as BlockType} state={params.state as BlockSceneState} />,
  testIds: ["preview-block"],
  isSessionSurface: true,
};

function CompositeScene({ params }: { params: Record<string, string> }) {
  const layout: SessionLayout = params.layout === "wide" ? "wide" : "side";
  const panelLayout = { ...FIXTURE_LAYOUT, layout };
  return (
    <SessionShell
      layout={layout}
      panelTitle="Session"
      agentName="Maya"
      agentState="listening"
      elapsedMs={125_000}
      banner={<ConnectionBanner agentState="listening" />}
      stage={
        <StageView agentState="listening" agentName="Maya" compact={layout === "wide"} elapsedMs={125_000} />
      }
      transcript={
        <p className="text-muted-foreground p-4 text-sm">The transcript block in the panel shows the turns.</p>
      }
      transcriptCount={3}
      panel={<CompositePreview layout={panelLayout} className="h-full min-h-0" />}
      controls={<ControlBarPlaceholder camera chat />}
    />
  );
}

const compositeScene: SceneDefinition = {
  id: "composite",
  label: "Composite panel",
  params: { layout: ["side", "wide"] },
  defaults: { layout: "side" },
  surfaces: ["dark"],
  combos: [
    { params: { layout: "side" }, surfaces: ["dark", "light"] },
    { params: { layout: "wide" } },
  ],
  render: (params) => <CompositeScene params={params} />,
  testIds: ["session-shell", "composite-panel"],
  isSessionSurface: true,
};

/* -------------------------------------------------------------------------- */
/* precall — every device-check state                                        */
/* -------------------------------------------------------------------------- */

const DEVICE_STATUSES: MicCheckStatus[] = ["idle", "requesting", "granted", "denied", "unsupported"];

function devicesFor(status: MicCheckStatus): MicDevices {
  if (status === "granted") {
    return {
      status,
      level: 0.55,
      inputs: [
        { deviceId: "default", kind: "audioinput", label: "MacBook Pro Microphone", groupId: "g1", toJSON: () => ({}) },
        { deviceId: "ext-mic", kind: "audioinput", label: "USB Microphone", groupId: "g2", toJSON: () => ({}) },
      ] as MediaDeviceInfo[],
      selectedId: "default",
    };
  }
  return { status, level: 0, inputs: [] };
}

function PrecallScene({ params }: { params: Record<string, string> }) {
  const status = params.devices as MicCheckStatus;
  const testMode = params.test === "1";
  return (
    <PreCallCard
      agent={PREVIEW_INSURANCE_AGENT}
      participantName="Guest"
      onParticipantNameChange={() => {}}
      onStart={() => {}}
      devices={devicesFor(status)}
      onCheckMicrophone={() => {}}
      onSelectInput={() => {}}
      testMode={testMode}
      backHref={`/console/agents/${PREVIEW_INSURANCE_AGENT.id}`}
    />
  );
}

const precallScene: SceneDefinition = {
  id: "precall",
  label: "Pre-call device check",
  params: { devices: DEVICE_STATUSES, test: ["0", "1"] },
  defaults: { devices: "idle", test: "0" },
  surfaces: ["dark"],
  combos: [
    ...DEVICE_STATUSES.map((devices) => ({ params: { devices, test: "0" } })),
    { params: { devices: "idle", test: "1" }, name: "idle-test" },
  ],
  render: (params) => <PrecallScene params={params} />,
  testIds: ["pre-call-card"],
  isSessionSurface: true,
  ownsMain: true,
};

/* -------------------------------------------------------------------------- */
/* ended                                                                      */
/* -------------------------------------------------------------------------- */

function EndedScene({ params }: { params: Record<string, string> }) {
  const testMode = params.test === "1";
  return (
    <EndOfCallCard
      agentName={PREVIEW_INSURANCE_AGENT.name}
      durationMs={125_000}
      onRestart={() => {}}
      testMode={testMode}
      backHref={`/console/agents/${PREVIEW_INSURANCE_AGENT.id}`}
    />
  );
}

const endedScene: SceneDefinition = {
  id: "ended",
  label: "End of call",
  params: { test: ["0", "1"] },
  defaults: { test: "0" },
  surfaces: ["dark"],
  combos: [{ params: { test: "0" } }, { params: { test: "1" }, name: "test" }],
  render: (params) => <EndedScene params={params} />,
  testIds: ["end-of-call-card"],
  isSessionSurface: true,
  ownsMain: true,
};

/* -------------------------------------------------------------------------- */
/* unavailable                                                                */
/* -------------------------------------------------------------------------- */

const UNAVAILABLE_KINDS: SessionUnavailableKind[] = ["not_found", "not_published", "unreachable"];

function UnavailableScene({ params }: { params: Record<string, string> }) {
  const kind = params.kind as SessionUnavailableKind;
  const testMode = params.test === "1";
  return (
    <SessionUnavailable
      kind={kind}
      slug="preview-insurance"
      testMode={testMode}
      detail={testMode ? "the console's admin token was not accepted (401)" : undefined}
    />
  );
}

const unavailableScene: SceneDefinition = {
  id: "unavailable",
  label: "Session unavailable",
  params: { kind: UNAVAILABLE_KINDS, test: ["0", "1"] },
  defaults: { kind: "not_found", test: "0" },
  surfaces: ["dark"],
  combos: [
    ...UNAVAILABLE_KINDS.map((kind) => ({ params: { kind, test: "0" } })),
    { params: { kind: "not_published", test: "1" }, name: "not_published-test" },
  ],
  render: (params) => <UnavailableScene params={params} />,
  testIds: ["session-unavailable"],
  isSessionSurface: true,
  ownsMain: true,
};

/* -------------------------------------------------------------------------- */
/* primitives — every WP-0 shared primitive, per §7.1 acceptance              */
/* -------------------------------------------------------------------------- */

function PrimitivesScene() {
  return (
    <div data-testid="preview-primitives" className="mx-auto max-w-4xl px-6 py-8">
      <PageHeader
        title="Shared primitives"
        description="Every WP-0 export in every state — used by the capture script's tokens proof, not shipped UI."
        breadcrumbs={[{ label: "Preview", href: "/console/preview/panels" }, { label: "Primitives" }]}
        actions={<Button variant="brand">Primary action</Button>}
      />

      <Section id="state-meter" title="StateMeter" description="Every meter state, xs–lg.">
        <SectionRow className="flex flex-wrap items-center gap-6">
          {METER_STATES.map((state: MeterState) => (
            <div key={state} className="flex flex-col items-center gap-1.5">
              <StateMeter state={state} size="md" bars={5} label />
              <span className="text-muted-foreground text-xs">{state}</span>
            </div>
          ))}
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="status-chip" title="StatusChip" description="Every tone, with and without a dot.">
        <SectionRow className="flex flex-wrap items-center gap-3">
          {(["neutral", "info", "success", "warning", "danger", "live"] as StatusTone[]).map((tone) => (
            <StatusChip key={tone} tone={tone} dot>
              {tone}
            </StatusChip>
          ))}
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="badges" title="Badge, CapabilityBadge, Kbd">
        <SectionRow className="flex flex-wrap items-center gap-3">
          <Badge tone="brand">brand</Badge>
          <Badge tone="info">info</Badge>
          <Badge tone="success">success</Badge>
          <Badge tone="warning">warning</Badge>
          <Badge tone="danger">danger</Badge>
        </SectionRow>
        <SectionRow className="flex flex-wrap items-center gap-3">
          {(Object.keys(CAPABILITY_BADGE_META) as CapabilityKind[]).map((kind) => (
            <CapabilityBadge key={kind} kind={kind} />
          ))}
        </SectionRow>
        <SectionRow className="flex items-center gap-2">
          <Kbd>⌘</Kbd>
          <Kbd>S</Kbd>
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="buttons" title="Button" description="Every variant at every size.">
        <SectionRow className="flex flex-wrap items-center gap-2">
          <Button variant="default">Default</Button>
          <Button variant="brand">Brand</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">Destructive</Button>
        </SectionRow>
        <SectionRow className="flex flex-wrap items-center gap-2">
          <Button size="xs">xs</Button>
          <Button size="sm">sm</Button>
          <Button size="default">default</Button>
          <Button size="lg">lg</Button>
          <Button size="xl">xl</Button>
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="field" title="Field" description="Default, required, error and inline.">
        <SectionRow className="grid gap-4 md:grid-cols-2">
          <Field label="Agent name" htmlFor="pv-name" required hint="Shown to callers.">
            <Input id="pv-name" defaultValue="Maya" />
          </Field>
          <Field label="Webhook URL" htmlFor="pv-url" error="Enter a valid https:// URL.">
            <Input id="pv-url" defaultValue="not-a-url" aria-invalid />
          </Field>
        </SectionRow>
        <SectionRow>
          <Field label="Recording" htmlFor="pv-recording" inline optional hint="Audio only in Phase 1.">
            <Input id="pv-recording" defaultValue="On" />
          </Field>
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="description-list" title="DescriptionList, RelativeTime, CopyButton, VendorMark">
        <SectionRow>
          <DescriptionList
            columns={2}
            items={[
              { term: "Started", detail: <RelativeTime iso="2026-09-23T12:00:00Z" withExact /> },
              { term: "Room", detail: "lkap-2e788a2a", mono: true },
              { term: "Provider", detail: <VendorMark vendor="deepgram" size="sm" /> },
              { term: "Copy", detail: <CopyButton value="lkap-2e788a2a" label="room id" /> },
            ]}
          />
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="empty-state" title="EmptyState">
        <SectionRow>
          <EmptyState
            icon={BotIcon}
            title="No agents yet"
            description="Create one from a pack template to get started."
            action={<Button variant="brand">New agent</Button>}
          />
        </SectionRow>
        <SectionRow>
          <EmptyState compact icon={KeyRoundIcon} title="No HTTP tools yet" description="Connect an API the agent can call." action={<Button size="sm" variant="secondary">Add tool</Button>} />
        </SectionRow>
      </Section>

      <div className="h-6" />

      <Section id="icons" title="Icon sizes">
        <SectionRow className="flex items-center gap-4">
          <Icon as={RadioIcon} size="sm" />
          <Icon as={RadioIcon} size="md" />
          <Icon as={RadioIcon} size="lg" />
          <Icon as={RadioIcon} size="xl" />
          <Icon as={BellIcon} size="lg" label="Notifications" />
        </SectionRow>
      </Section>
    </div>
  );
}

const primitivesScene: SceneDefinition = {
  id: "primitives",
  label: "Shared primitives",
  params: {},
  defaults: {},
  surfaces: ["light", "dark"],
  combos: [{ params: {} }],
  render: () => <PrimitivesScene />,
  testIds: ["preview-primitives"],
};

/* -------------------------------------------------------------------------- */
/* registry                                                                   */
/* -------------------------------------------------------------------------- */

export const SCENES: Record<string, SceneDefinition> = {
  session: sessionScene,
  notebook: notebookScene,
  generic: genericScene,
  blocks: blocksScene,
  composite: compositeScene,
  precall: precallScene,
  ended: endedScene,
  unavailable: unavailableScene,
  primitives: primitivesScene,
};

export const SCENE_IDS = Object.keys(SCENES);

export function resolveSceneParams(sceneId: string, raw: Record<string, string | undefined>) {
  const scene = SCENES[sceneId];
  if (!scene) return {};
  return resolve(scene, raw);
}

/** One row per shootable combo, for `scene=list` and the capture script. */
export interface SceneListEntry {
  scene: string;
  name: string;
  query: string;
  surfaces: Surface[];
}

export function sceneListPayload(): SceneListEntry[] {
  const entries: SceneListEntry[] = [];
  for (const scene of Object.values(SCENES)) {
    const paramOrder = Object.keys(scene.params);
    for (const combo of scene.combos) {
      const search = new URLSearchParams({ scene: scene.id, ...combo.params });
      entries.push({
        scene: scene.id,
        name: comboName(combo, paramOrder) || scene.id,
        query: search.toString(),
        surfaces: [...(combo.surfaces ?? scene.surfaces)],
      });
    }
  }
  return entries;
}
