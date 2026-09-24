"use client";

import * as React from "react";
import { BotIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CopyButton } from "@/components/shared/copy-button";
import { Field } from "@/components/shared/field";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

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
 * or a side sheet. Needs `admin` server-side (`api_keys.py::KeyAdminDep`),
 * same floor as the plain API-keys dialog.
 */
export function ConnectAgentDialog({ onCreated }: { onCreated: () => void }) {
  const { canWrite } = useWriteAccess("admin");
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
  const [error, setError] = React.useState<string | null>(null);
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
      setError(err instanceof ApiError ? err.message : errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog
      open={canWrite && open}
      onOpenChange={(next) => {
        if (!canWrite) return;
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeAccessReason("admin")}>
          <BotIcon />
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
  error: string | null;
  creating: boolean;
  canSubmit: boolean;
  onSubmit: (event: React.FormEvent) => void;
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
          Mint a scoped key for Claude Code, Codex or any MCP-capable coding agent to use against this workspace.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="gap-6">
        {error ? <ErrorBanner message={error} /> : null}

        <Field label="Name" htmlFor="agent-key-name" required>
          <Input id="agent-key-name" value={name} onChange={(event) => setName(event.target.value)} disabled={creating} />
        </Field>

        <div className="space-y-2">
          <Label id="agent-key-client-label">Client</Label>
          <RadioGroup
            aria-labelledby="agent-key-client-label"
            value={client}
            onValueChange={(v) => setClient(v as AgentKeyClientId)}
            disabled={creating}
            className="grid-cols-2 sm:grid-cols-4"
          >
            {AGENT_KEY_CLIENTS.map((c) => (
              <OptionCard key={c.id} value={c.id} id={`agent-key-client-${c.id}`} title={c.label} selected={client === c.id} />
            ))}
          </RadioGroup>
        </div>

        <div className="space-y-2">
          <Label id="agent-key-connection-label">Connection</Label>
          <RadioGroup
            aria-labelledby="agent-key-connection-label"
            value={remote ? "remote" : "local"}
            onValueChange={(v) => setRemote(v === "remote")}
            disabled={creating}
            className="grid-cols-1 sm:grid-cols-2"
          >
            <OptionCard
              value="local"
              id="agent-key-conn-local"
              title="Local (stdio)"
              description="The agent spawns lkap-mcp on your own machine."
              selected={!remote}
            />
            <OptionCard
              value="remote"
              id="agent-key-conn-remote"
              title="Remote (HTTP)"
              description={
                remoteAvailable
                  ? "Connects to the shared MCP endpoint your operator has enabled."
                  : "Ask your operator to enable the remote MCP service."
              }
              selected={remote}
              disabled={!remoteAvailable}
            />
          </RadioGroup>
        </div>

        <div className="space-y-2">
          <Label id="agent-key-preset-label">Access</Label>
          <RadioGroup
            aria-labelledby="agent-key-preset-label"
            value={presetId}
            onValueChange={(v) => setPresetId(v as AgentKeyPresetId)}
            disabled={creating}
          >
            {AGENT_KEY_PRESETS.map((preset) => (
              <OptionCard
                key={preset.id}
                value={preset.id}
                id={`agent-key-preset-${preset.id}`}
                title={preset.label}
                description={preset.description}
                selected={presetId === preset.id}
              >
                <div className="mt-2 flex flex-wrap gap-1">
                  {preset.scopes.map((scope) => (
                    <span
                      key={scope}
                      className="rounded-xs bg-muted px-1.5 py-0.5 font-mono text-[0.6875rem] text-muted-foreground"
                    >
                      {scope}
                    </span>
                  ))}
                </div>
              </OptionCard>
            ))}
          </RadioGroup>
        </div>

        <label
          htmlFor="agent-key-calls-write"
          data-checked={allowCalls ? "" : undefined}
          className="flex items-start gap-2 rounded-md border border-border p-3 text-sm transition-colors data-checked:border-warning data-checked:bg-warning-soft/40"
        >
          <Checkbox
            id="agent-key-calls-write"
            checked={allowCalls}
            onCheckedChange={(v) => setAllowCalls(v === true)}
            disabled={creating}
          />
          <span>
            <span className="font-medium text-foreground">
              Allow outbound phone calls (<code className="font-mono">calls:write</code>)
            </span>
            <p className="mt-0.5 text-xs text-pretty text-muted-foreground">
              Off by default. Dialing also needs the MCP process started with{" "}
              <code className="font-mono">LKAP_MCP_ALLOW_DIAL=1</code> and confirmation on every call; the
              workspace&apos;s dialing policy (allowed prefixes, rate limits) always applies.
            </p>
          </span>
        </label>

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

        <Alert variant="warning">
          <AlertTitle>Before you create this key</AlertTitle>
          <AlertDescription>{TRANSCRIPT_WARNING}</AlertDescription>
        </Alert>
        <label htmlFor="agent-key-ack" className="flex items-start gap-2 text-sm">
          <Checkbox
            id="agent-key-ack"
            checked={acknowledged}
            onCheckedChange={(v) => setAcknowledged(v === true)}
            disabled={creating}
          />
          <span>I understand my coding agent&apos;s own transcript will store what I paste into it.</span>
        </label>
      </DialogBody>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline" disabled={creating}>
            Cancel
          </Button>
        </DialogClose>
        <Button type="submit" disabled={!canSubmit}>
          {creating ? "Creating…" : "Create key"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function OptionCard({
  value,
  id,
  title,
  description,
  selected,
  disabled,
  children,
}: {
  value: string;
  id: string;
  title: string;
  description?: string;
  selected: boolean;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <label
      htmlFor={id}
      data-slot="option-card"
      data-selected={selected ? "" : undefined}
      className={cn(
        "flex flex-col gap-1 rounded-lg border border-border bg-card p-3 text-card-foreground transition-colors",
        "has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2 has-focus-visible:ring-offset-background",
        disabled
          ? "cursor-not-allowed opacity-50"
          : cn("cursor-pointer", selected ? "border-brand-line bg-brand-soft/40 ring-1 ring-brand-line" : "hover:bg-muted/50"),
      )}
    >
      <span className="flex items-start gap-2">
        <RadioGroupItem value={value} id={id} disabled={disabled} className="mt-0.5" />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-foreground">{title}</span>
          {description ? <span className="mt-0.5 block text-xs text-pretty text-muted-foreground">{description}</span> : null}
        </span>
      </span>
      {children}
    </label>
  );
}

// ---------------------------------------------------------- step 2: reveal

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
        <DialogDescription>The raw key is shown once, right after creation — copy it now.</DialogDescription>
      </DialogHeader>
      <DialogBody className="gap-4">
        <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 p-2">
          <code className="min-w-0 flex-1 truncate font-mono text-xs">{created.key}</code>
          <CopyButton value={created.key} label="Copy agent key" />
        </div>
        <p className="text-xs text-muted-foreground">This key won&apos;t be shown again.</p>

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
                <div className="relative">
                  <pre
                    tabIndex={0}
                    className="max-h-72 overflow-auto rounded-md border border-border bg-muted/40 p-4 pr-10 font-mono text-xs leading-5 break-all whitespace-pre-wrap text-foreground"
                  >
                    {snippet}
                  </pre>
                  <CopyButton
                    value={snippet}
                    label={`Copy the ${tab.label} snippet`}
                    className="absolute top-2 right-2"
                  />
                </div>
              </TabsContent>
            );
          })}
        </Tabs>
        <p className="text-xs text-muted-foreground">{KEEP_KEY_NOTE}</p>
      </DialogBody>
      <DialogFooter>
        <Button type="button" onClick={onNext}>
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
        <div>
          <Label htmlFor="agent-key-skill-line">Install the Claude Code skill (optional)</Label>
          <div className="relative mt-2">
            <pre
              id="agent-key-skill-line"
              tabIndex={0}
              className="overflow-auto rounded-md border border-border bg-muted/40 p-3 pr-10 font-mono text-xs text-foreground"
            >
              {installLine}
            </pre>
            <CopyButton value={installLine} label="Copy the skill install command" className="absolute top-2 right-2" />
          </div>
        </div>
        <p className="text-sm text-muted-foreground">{CODEX_AGENTS_NOTE}</p>
        <div>
          <Label htmlFor="agent-key-ask-line">Ask your agent</Label>
          <div className="relative mt-2">
            <pre
              id="agent-key-ask-line"
              tabIndex={0}
              className="overflow-auto rounded-md border border-border bg-muted/40 p-3 pr-10 font-mono text-xs text-foreground"
            >
              {ASK_AGENT_LINE}
            </pre>
            <CopyButton value={ASK_AGENT_LINE} label="Copy the prompt" className="absolute top-2 right-2" />
          </div>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="button" onClick={onClose}>
          Done
        </Button>
      </DialogFooter>
    </>
  );
}
