"use client";

import * as React from "react";
import Link from "next/link";
import { CheckCircle2Icon, CircleIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { useAgents, useCredentials, useHealth, useSessions } from "@/components/console/lib/api-hooks";
import { useMe } from "@/components/console/shell/use-me";
import { useConnectionsProbe, useWebhooksProbe } from "./probes";

interface ChecklistRow {
  id: string;
  title: string;
  help: string;
  done: boolean;
  action?: React.ReactNode;
}

/**
 * The 8-row v2 checklist (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1: "sign in,
 * connection tested, provider key added, agent created, test call, panel
 * added, recording on, webhook added"), replacing v1's 6-row list (§4.1).
 * Rows for endpoints that 404 today (connections, webhooks — V2-03/V2-08
 * land later this wave) render "not done" with their normal action, never
 * as an error; auth (V2-02) is the same story for "Sign in".
 *
 * "Panel added" and "Recording on" read the most recently updated agent's
 * config (the closest v1 precedent — "make a test call" also picked "the
 * most recently updated agent" — since the amendments name the rows but
 * don't specify per-row source data beyond that).
 */
export function SetupChecklist() {
  const { unavailable: authUnavailable } = useMe();
  const { data: health } = useHealth();
  const { data: agents } = useAgents();
  const { data: credentials } = useCredentials();
  const { data: sessions } = useSessions();
  const connections = useConnectionsProbe();
  const webhooks = useWebhooksProbe();

  const agentItems = agents?.items ?? [];
  const mostRecentAgent = [...agentItems].sort(
    (a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at),
  )[0];
  const panelAdded = agentItems.some((a) => (a.config.panel?.blocks?.length ?? 0) > 0);
  const recordingOn = agentItems.some((a) => a.config.recording?.enabled);
  const anyPublished = agentItems.some((a) => a.published);

  const rows: ChecklistRow[] = [
    {
      id: "sign-in",
      title: "Sign in",
      help: authUnavailable
        ? "Workspace accounts aren't set up yet — the console is reachable directly."
        : "You're signed in to this workspace.",
      done: true,
    },
    {
      id: "connection",
      title: "A LiveKit connection is tested",
      help: connections.available
        ? "At least one connection has been tested and can host agents."
        : health?.livekit_url
          ? `Using ${health.livekit_url} until connections ship.`
          : "Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET for the API and the worker (RUNBOOK §1).",
      done: connections.available && connections.total > 0,
      action: connections.available ? (
        <Button asChild size="sm" variant="outline">
          <Link href="/console/connections">Open connections</Link>
        </Button>
      ) : undefined,
    },
    {
      id: "credential",
      title: "Add a provider key (optional)",
      help: "LiveKit Inference needs no key. Add one to use Gemini Live, OpenAI, Deepgram, ElevenLabs…",
      done: (credentials?.total ?? 0) > 0,
      action: (
        <Button asChild size="sm" variant="outline">
          <Link href="/console/providers">Add credential</Link>
        </Button>
      ),
    },
    {
      id: "agent",
      title: "Create your first agent",
      help: "An agent is a voice or video assistant with its own providers, instructions and tools.",
      done: agentItems.length > 0,
      action: (
        <Button asChild size="sm">
          <Link href="/console/agents/new">New agent</Link>
        </Button>
      ),
    },
    {
      id: "test-call",
      title: "Make a test call",
      help: "Open an agent and start a call in test mode.",
      done: (sessions?.total ?? 0) > 0,
      action: mostRecentAgent ? (
        <Button asChild size="sm" variant="outline">
          <Link href={`/console/agents/${mostRecentAgent.id}`}>Test call</Link>
        </Button>
      ) : undefined,
    },
    {
      id: "panel",
      title: "Add a panel",
      help: "Give callers something to look at: notes, a checklist, a document.",
      done: panelAdded,
      action: mostRecentAgent ? (
        <Button asChild size="sm" variant="outline">
          <Link href={`/console/agents/${mostRecentAgent.id}?section=panel`}>Open panel</Link>
        </Button>
      ) : undefined,
    },
    {
      id: "recording",
      title: "Turn on recording",
      help: "Keep an audio recording of every call for review.",
      done: recordingOn,
      action: mostRecentAgent ? (
        <Button asChild size="sm" variant="outline">
          <Link href={`/console/agents/${mostRecentAgent.id}?section=recording`}>Open recording</Link>
        </Button>
      ) : undefined,
    },
    {
      id: "webhook",
      title: "Add a webhook",
      help: webhooks.available
        ? "Get notified when a call starts, ends or fails."
        : "Webhooks aren't available yet.",
      done: webhooks.available && webhooks.total > 0,
      action: webhooks.available ? (
        <Button asChild size="sm" variant="outline">
          <Link href="/console/settings?tab=webhooks">Add webhook</Link>
        </Button>
      ) : undefined,
    },
    // "Publish an agent" isn't one of the amendments' 8 named rows, but
    // dropping it entirely would be a regression from v1 with no
    // replacement signal on the page — kept as a 9th row rather than
    // silently lost; flagged in the WP-1 hand-off note.
    {
      id: "publish",
      title: "Publish an agent",
      help: "Publishing makes an agent's link answer calls from anyone who has it.",
      done: anyPublished,
      action: mostRecentAgent ? (
        <Button asChild size="sm" variant="outline">
          <Link href={`/console/agents/${mostRecentAgent.id}`}>Open agent</Link>
        </Button>
      ) : undefined,
    },
  ];

  const allDone = rows.every((row) => row.done);
  const [collapsed, setCollapsed] = React.useState(false);

  if (allDone && collapsed) {
    return (
      <Section id="setup" title="Set up LKAP">
        <SectionRow className="flex items-center justify-between gap-3">
          <p className="text-sm text-success-text">Setup complete</p>
          <Button size="sm" variant="ghost" onClick={() => setCollapsed(false)}>
            Show
          </Button>
        </SectionRow>
      </Section>
    );
  }

  return (
    <Section
      id="setup"
      title="Set up LKAP"
      aside={
        allDone ? (
          <Button size="sm" variant="ghost" onClick={() => setCollapsed(true)}>
            Collapse
          </Button>
        ) : undefined
      }
    >
      {rows.map((row) => (
        <SectionRow key={row.id} className="flex items-start gap-3">
          <Icon
            as={row.done ? CheckCircle2Icon : CircleIcon}
            size="md"
            className={row.done ? "mt-0.5 text-success" : "mt-0.5 text-muted-foreground"}
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{row.title}</p>
            <p className="mt-0.5 text-sm text-muted-foreground">{row.help}</p>
          </div>
          {!row.done && row.action ? <div className="shrink-0">{row.action}</div> : null}
        </SectionRow>
      ))}
    </Section>
  );
}
