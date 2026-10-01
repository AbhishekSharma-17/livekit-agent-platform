"use client";

import * as React from "react";
import { BotIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CheckboxRow, OptionCard } from "@/components/shared/choice";
import { CopyButton } from "@/components/shared/copy-button";
import { Field } from "@/components/shared/field";
import { Tag } from "@/components/shared/tag";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { api } from "@/lib/api";

import type { ApiKeyCreated, Scope } from "./api-types";
import {
  AGENT_KEY_CLIENTS,
  AGENT_KEY_EXPIRY_OPTIONS,
  AGENT_KEY_PRESETS,
  ASK_AGENT_LINE,
  CALLS_WRITE_SCOPE,
  CODEX_AGENTS_NOTE,
  DEFAULT_AGENT_KEY_CLIENT,
  DEFAULT_AGENT_KEY_EXPIRY_DAYS,
  DEFAULT_AGENT_KEY_PRESET,
  KEEP_KEY_NOTE,
  SNIPPET_TABS,
  TRANSCRIPT_ACK_LABEL,
  TRANSCRIPT_WARNING,
  expiresAtIso,
  presetById,
  skillInstallLine,
  snippetClientFor,
  snippetFor,
  type AgentKeyClientId,
  type AgentKeyPresetId,
  type SnippetClientId,
  type SnippetContext,
} from "./snippets";

const API_ORIGIN_PLACEHOLDER = "<api origin>";

/** `NEXT_PUBLIC_API_BASE_URL` is already public (the console proxy's own target, `app/api/console/[...path]/route.ts`) — safe to read directly, no `.env` file access involved (Next inlines it at build time). */
function apiOrigin(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || API_ORIGIN_PLACEHOLDER;
}

/** Unset until V3-06 ships the remote MCP service — the Remote choice stays disabled until an operator sets this. */
function publicMcpUrl(): string | undefined {
  return process.env.NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL || undefined;
}

type Step = "key" | "reveal" | "done";

/**
 * "Connect an AI agent" (docs/v3/AGENT-ACCESS.md §5, PLAN-V3 V3-04, R-V3-2):
 * one `Dialog`, three steps in the same dialog body — never a second dialog
 * or a side sheet. Needs `admin` server-side (`api_keys.py::KeyAdminDep`);
 * the AI agents tab only renders it for admins and owners.
 */
export function ConnectAgentDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [step, setStep] = React.useState<Step>("key");

  const [name, setName] = React.useState("AI agent key");
  const [client, setClient] = React.useState<AgentKeyClientId>(DEFAULT_AGENT_KEY_CLIENT);
  const [remote, setRemote] = React.useState(false);
  const [presetId, setPresetId] = React.useState<AgentKeyPresetId>(DEFAULT_AGENT_KEY_PRESET);
  const [allowCalls, setAllowCalls] = React.useState(false);
  const [expiryDays, setExpiryDays] = React.useState(DEFAULT_AGENT_KEY_EXPIRY_DAYS);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);
  const [snippetTab, setSnippetTab] = React.useState<SnippetClientId>(snippetClientFor(DEFAULT_AGENT_KEY_CLIENT));

  const remoteUrl = publicMcpUrl();
  const remoteAvailable = Boolean(remoteUrl);

  function reset() {
    setStep("key");
    setName("AI agent key");
    setClient(DEFAULT_AGENT_KEY_CLIENT);
    setRemote(false);
    setPresetId(DEFAULT_AGENT_KEY_PRESET);
    setAllowCalls(false);
    setExpiryDays(DEFAULT_AGENT_KEY_EXPIRY_DAYS);
    setAcknowledged(false);
    setCreating(false);
    setError(null);
    setCreated(null);
    setSnippetTab(snippetClientFor(DEFAULT_AGENT_KEY_CLIENT));
  }

  const scopes = React.useMemo<Scope[]>(() => {
    const preset = presetById(presetId);
    return allowCalls ? [...preset.scopes, CALLS_WRITE_SCOPE] : preset.scopes;
  }, [presetId, allowCalls]);

  const canSubmit = name.trim().length > 0 && acknowledged && !creating;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setCreating(true);
    setError(null);
    try {
      const key = await api.post<ApiKeyCreated>("api-keys", {
        name: name.trim(),
        scopes,
        expires_at: expiresAtIso(expiryDays),
        kind: "agent",
        client,
      });
      setCreated(key);
      setSnippetTab(snippetClientFor(client));
      setStep("reveal");
      onCreated();
    } catch (err) {
      setError(err);
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="primary" size="sm">
          <BotIcon aria-hidden="true" />
          Connect an AI agent
        </Button>
      </DialogTrigger>
      <DialogContent size="lg">
        {step === "key" ? (
          <KeyStep
            name={name}
            setName={setName}
            client={client}
            setClient={setClient}
            remote={remote}
            setRemote={setRemote}
            remoteAvailable={remoteAvailable}
            presetId={presetId}
            setPresetId={setPresetId}
            allowCalls={allowCalls}
            setAllowCalls={setAllowCalls}
            expiryDays={expiryDays}
            setExpiryDays={setExpiryDays}
            acknowledged={acknowledged}
            setAcknowledged={setAcknowledged}
            error={error}
            creating={creating}
            canSubmit={canSubmit}
            onSubmit={onSubmit}
          />
        ) : step === "reveal" && created ? (
          <RevealStep
            created={created}
            remote={remote}
            snippetTab={snippetTab}
            setSnippetTab={setSnippetTab}
            onNext={() => setStep("done")}
          />
        ) : (
          <DoneStep onClose={() => setOpen(false)} />
        )}
      </DialogContent>
    </Dialog>
  );
}

// -------------------------------------------------------------- step 1: key

interface KeyStepProps {
  name: string;
  setName: (v: string) => void;
  client: AgentKeyClientId;
  setClient: (v: AgentKeyClientId) => void;
  remote: boolean;
  setRemote: (v: boolean) => void;
  remoteAvailable: boolean;
  presetId: AgentKeyPresetId;
  setPresetId: (v: AgentKeyPresetId) => void;
  allowCalls: boolean;
  setAllowCalls: (v: boolean) => void;
  expiryDays: number;
  setExpiryDays: (v: number) => void;
  acknowledged: boolean;
  setAcknowledged: (v: boolean) => void;
  error: unknown;
  creating: boolean;
  canSubmit: boolean;
  onSubmit: (event: React.FormEvent) => void;
}

/** A labelled group of option cards (a native radio group under a legend). */
function ChoiceGroup({ legend, className, children }: { legend: string; className?: string; children: React.ReactNode }) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
      <legend className="mb-1.5 text-label font-medium text-foreground">{legend}</legend>
      <div className={className ?? "grid gap-2"}>{children}</div>
    </fieldset>
  );
}

function KeyStep({
  name,
  setName,
  client,
  setClient,
  remote,
  setRemote,
  remoteAvailable,
  presetId,
  setPresetId,
  allowCalls,
  setAllowCalls,
  expiryDays,
  setExpiryDays,
  acknowledged,
  setAcknowledged,
  error,
  creating,
  canSubmit,
  onSubmit,
}: KeyStepProps) {
  return (
    <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
      <DialogHeader>
        <DialogTitle>Connect an AI agent</DialogTitle>
        <DialogDescription>
          Create a scoped key for Claude Code, Codex or any MCP-capable coding agent to use on this workspace.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="gap-6">
        {error ? <ErrorBanner error={error} context={{ action: "create the key" }} /> : null}

        <Field label="Name" htmlFor="agent-key-name">
          <Input id="agent-key-name" value={name} onChange={(event) => setName(event.target.value)} disabled={creating} />
        </Field>

        <ChoiceGroup legend="Client" className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-2 sm:grid-cols-4">
          {AGENT_KEY_CLIENTS.map((c) => (
            <OptionCard
              key={c.id}
              id={`agent-key-client-${c.id}`}
              name="agent-key-client"
              value={c.id}
              checked={client === c.id}
              onChange={() => setClient(c.id)}
              disabled={creating}
              title={c.label}
            />
          ))}
        </ChoiceGroup>

        <ChoiceGroup legend="Connection" className="grid gap-2 sm:grid-cols-2">
          <OptionCard
            id="agent-key-conn-local"
            name="agent-key-connection"
            value="local"
            checked={!remote}
            onChange={() => setRemote(false)}
            disabled={creating}
            title="Local (stdio)"
            description="The agent starts lkap-mcp on your own machine."
          />
          <OptionCard
            id="agent-key-conn-remote"
            name="agent-key-connection"
            value="remote"
            checked={remote}
            onChange={() => setRemote(true)}
            disabled={creating || !remoteAvailable}
            title="Remote (HTTP)"
            description={
              remoteAvailable
                ? "Connects to the shared MCP endpoint your operator has turned on."
                : "Ask your operator to enable the remote MCP service."
            }
          />
        </ChoiceGroup>

        <ChoiceGroup legend="Access">
          {AGENT_KEY_PRESETS.map((preset) => (
            <OptionCard
              key={preset.id}
              id={`agent-key-preset-${preset.id}`}
              name="agent-key-preset"
              value={preset.id}
              checked={presetId === preset.id}
              onChange={() => setPresetId(preset.id)}
              disabled={creating}
              title={preset.label}
              description={
                <>
                  {preset.description}
                  <span className="mt-2 flex flex-wrap gap-1.5">
                    {preset.scopes.map((scope) => (
                      <Tag key={scope} className="font-mono">
                        {scope}
                      </Tag>
                    ))}
                  </span>
                </>
              }
            />
          ))}
        </ChoiceGroup>

        <OptionCard
          type="checkbox"
          id="agent-key-calls-write"
          checked={allowCalls}
          onChange={(event) => setAllowCalls(event.target.checked)}
          disabled={creating}
          title={
            <>
              Allow outbound phone calls (<code className="font-mono">calls:write</code>)
            </>
          }
          description={
            <>
              Off by default. Dialing also needs the MCP process started with{" "}
              <code className="font-mono">LKAP_MCP_ALLOW_DIAL=1</code> and a confirmation on every call. The
              workspace&apos;s dialing policy (allowed prefixes, rate limits) always applies.
            </>
          }
        />

        <Field label="Expiry" htmlFor="agent-key-expiry" hint="30 days by default.">
          <Select value={String(expiryDays)} onValueChange={(v) => setExpiryDays(Number(v))} disabled={creating}>
            <SelectTrigger id="agent-key-expiry" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {AGENT_KEY_EXPIRY_OPTIONS.map((option) => (
                <SelectItem key={option.days} value={String(option.days)}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <div className="flex flex-col gap-3">
          <Alert tone="warning" title="Before you create this key">
            {TRANSCRIPT_WARNING}
          </Alert>
          <CheckboxRow
            id="agent-key-ack"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            disabled={creating}
            label={TRANSCRIPT_ACK_LABEL}
          />
        </div>
      </DialogBody>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" disabled={creating}>
            Cancel
          </Button>
        </DialogClose>
        <Button type="submit" variant="primary" disabled={!canSubmit && !creating} busy={creating} busyLabel="Creating key…">
          Create key
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------- step 2: reveal

function SnippetBlock({ id, value, copyLabel, className }: { id?: string; value: string; copyLabel: string; className?: string }) {
  return (
    <div className="relative">
      <pre
        id={id}
        tabIndex={0}
        className={
          className ??
          "overflow-auto rounded border border-border bg-muted p-3 pr-10 font-mono text-caption leading-5 break-all whitespace-pre-wrap text-foreground"
        }
      >
        {value}
      </pre>
      <CopyButton value={value} label={copyLabel} className="absolute top-2 right-2" />
    </div>
  );
}

function RevealStep({
  created,
  remote,
  snippetTab,
  setSnippetTab,
  onNext,
}: {
  created: ApiKeyCreated;
  remote: boolean;
  snippetTab: SnippetClientId;
  setSnippetTab: (v: SnippetClientId) => void;
  onNext: () => void;
}) {
  const ctx: SnippetContext = {
    apiOrigin: apiOrigin(),
    key: created.key,
    remote,
    publicMcpUrl: publicMcpUrl(),
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>Your agent key</DialogTitle>
        <DialogDescription>The raw key is shown once, right after creation. Copy it now.</DialogDescription>
      </DialogHeader>
      <DialogBody className="gap-4">
        <div className="flex items-center gap-2 rounded border border-border bg-muted p-2">
          <code className="min-w-0 flex-1 font-mono text-caption break-all">{created.key}</code>
          <CopyButton value={created.key} label="Copy agent key" />
        </div>
        <p className="text-caption text-text-secondary">This key won&apos;t be shown again.</p>

        <Tabs value={snippetTab} onValueChange={(v) => setSnippetTab(v as SnippetClientId)}>
          <TabsList>
            {SNIPPET_TABS.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {SNIPPET_TABS.map((tab) => {
            const snippet = snippetFor(tab.id, ctx);
            return (
              <TabsContent key={tab.id} value={tab.id} className="pt-3">
                <SnippetBlock
                  value={snippet}
                  copyLabel={`Copy the ${tab.label} snippet`}
                  className="max-h-72 overflow-auto rounded border border-border bg-muted p-4 pr-10 font-mono text-caption leading-5 break-all whitespace-pre-wrap text-foreground"
                />
              </TabsContent>
            );
          })}
        </Tabs>
        <p className="text-caption text-text-secondary">{KEEP_KEY_NOTE}</p>
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="primary" onClick={onNext}>
          Next
        </Button>
      </DialogFooter>
    </>
  );
}

// ------------------------------------------------------------- step 3: done

function DoneStep({ onClose }: { onClose: () => void }) {
  const installLine = skillInstallLine();

  return (
    <>
      <DialogHeader>
        <DialogTitle>You&apos;re set</DialogTitle>
        <DialogDescription>Give your agent the platform guide, then ask it what it can do.</DialogDescription>
      </DialogHeader>
      <DialogBody className="gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="agent-key-skill-line">Install the Claude Code skill (optional)</Label>
          <SnippetBlock id="agent-key-skill-line" value={installLine} copyLabel="Copy the skill install command" />
        </div>
        <p className="text-label text-text-secondary">{CODEX_AGENTS_NOTE}</p>
        <div className="flex flex-col gap-2">
          <Label htmlFor="agent-key-ask-line">Ask your agent</Label>
          <SnippetBlock id="agent-key-ask-line" value={ASK_AGENT_LINE} copyLabel="Copy the prompt" />
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="primary" onClick={onClose}>
          Done
        </Button>
      </DialogFooter>
    </>
  );
}
