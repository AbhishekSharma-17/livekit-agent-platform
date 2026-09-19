"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";
import { PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { BUILTIN_TOOLS } from "@/components/console/lib/constants";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ToolRow } from "@/components/console/tools/tool-row";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ToolOut } from "@/contracts/lkap-contracts";

export function ToolsTab({ agent }: { agent: AgentOut }) {
  const maxToolStepsId = React.useId();
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const toolIds = watch("config.tools.tool_ids");
  const builtinDisabled = watch("config.tools.builtin_disabled");

  const ownToolsQuery = useTools(agent.id);
  const allToolsQuery = useTools();
  const providersQuery = useProviders();
  const packsQuery = usePacks();

  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const pack = packsQuery.data?.items.find((p) => p.manifest.id === agent.pack_id)?.manifest;

  function setAttached(id: string, attached: boolean) {
    const current = toolIds ?? [];
    setValue(
      "config.tools.tool_ids",
      attached ? Array.from(new Set([...current, id])) : current.filter((existing) => existing !== id),
      { shouldDirty: true },
    );
  }

  function toggleBuiltin(name: string, enabled: boolean) {
    const current = builtinDisabled ?? [];
    setValue(
      "config.tools.builtin_disabled",
      enabled ? current.filter((existing) => existing !== name) : Array.from(new Set([...current, name])),
      { shouldDirty: true },
    );
  }

  const ownTools = ownToolsQuery.data?.items ?? [];
  const httpTools = ownTools.filter((t) => t.kind === "http");
  const mcpTools = ownTools.filter((t) => t.kind === "mcp");
  const sharedAttachable = (allToolsQuery.data?.items ?? []).filter(
    (t) => t.agent_id === null && !(toolIds ?? []).includes(t.id),
  );
  const [sharedToAttach, setSharedToAttach] = React.useState("");

  return (
    <div className="space-y-6">
      <div className="space-y-4 rounded-xl border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">Built-in tools</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {BUILTIN_TOOLS.map((tool) => (
            <div key={tool.name} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <div className="min-w-0 pr-2">
                <p className="text-sm font-medium">{tool.label}</p>
                <p className="truncate text-xs text-muted-foreground">{tool.help}</p>
              </div>
              <Switch
                checked={!(builtinDisabled ?? []).includes(tool.name)}
                onCheckedChange={(checked) => toggleBuiltin(tool.name, checked)}
              />
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
          <div>
            <p className="text-sm font-medium">HTTP request tool</p>
            <p className="text-xs text-muted-foreground">
              Lets the model make ad-hoc outbound HTTP calls (separate from the declared HTTP tools below).
            </p>
          </div>
          <Controller
            control={control}
            name="config.tools.http_request_enabled"
            render={({ field }) => <Switch checked={field.value} onCheckedChange={field.onChange} />}
          />
        </div>

        <div>
          <label htmlFor={maxToolStepsId} className="mb-1 block text-sm font-medium">
            Max tool steps per turn
          </label>
          <Controller
            control={control}
            name="config.tools.max_tool_steps"
            render={({ field }) => (
              <Input
                id={maxToolStepsId}
                type="number"
                className="w-32"
                value={field.value}
                onChange={(event) => field.onChange(Number(event.target.value))}
              />
            )}
          />
        </div>
      </div>

      <div className="space-y-3 rounded-xl border border-border bg-card p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">HTTP tools</h3>
          <HttpToolEditorDialog
            agentId={agent.id}
            secretBagSpec={secretBagSpec}
            onSaved={(tool) => {
              setAttached(tool.id, true);
              void ownToolsQuery.refetch();
            }}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon className="size-3.5" /> HTTP tool
              </Button>
            }
          />
        </div>
        <p className="text-xs text-muted-foreground">
          New tools are attached to this agent when you click Save &amp; validate.
        </p>
        {ownToolsQuery.isError ? (
          <ErrorBanner message={errorMessage(ownToolsQuery.error)} onRetry={() => ownToolsQuery.refetch()} />
        ) : ownToolsQuery.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : httpTools.length === 0 ? (
          <p className="text-sm text-muted-foreground">No HTTP tools yet.</p>
        ) : (
          <div className="space-y-2">
            {httpTools.map((tool) => (
              <ToolRow
                key={tool.id}
                tool={tool}
                agentId={agent.id}
                attached={(toolIds ?? []).includes(tool.id)}
                onToggleAttach={(attached) => setAttached(tool.id, attached)}
                onSaved={() => void ownToolsQuery.refetch()}
                onDeleted={() => {
                  setAttached(tool.id, false);
                  void ownToolsQuery.refetch();
                }}
                secretBagSpec={secretBagSpec}
              />
            ))}
          </div>
        )}
      </div>

      <div className="space-y-3 rounded-xl border border-border bg-card p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">MCP servers</h3>
          <McpToolEditorDialog
            agentId={agent.id}
            secretBagSpec={secretBagSpec}
            onSaved={(tool) => {
              setAttached(tool.id, true);
              void ownToolsQuery.refetch();
            }}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon className="size-3.5" /> MCP server
              </Button>
            }
          />
        </div>
        <p className="text-xs text-muted-foreground">
          New tools are attached to this agent when you click Save &amp; validate.
        </p>
        {mcpTools.length === 0 ? (
          <p className="text-sm text-muted-foreground">No MCP servers yet.</p>
        ) : (
          <div className="space-y-2">
            {mcpTools.map((tool: ToolOut) => (
              <ToolRow
                key={tool.id}
                tool={tool}
                agentId={agent.id}
                attached={(toolIds ?? []).includes(tool.id)}
                onToggleAttach={(attached) => setAttached(tool.id, attached)}
                onSaved={() => void ownToolsQuery.refetch()}
                onDeleted={() => {
                  setAttached(tool.id, false);
                  void ownToolsQuery.refetch();
                }}
                secretBagSpec={secretBagSpec}
              />
            ))}
          </div>
        )}
      </div>

      {sharedAttachable.length > 0 ? (
        <div className="rounded-xl border border-border bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold">Attach a shared tool</h3>
          <div className="flex gap-2">
            <Select value={sharedToAttach} onValueChange={setSharedToAttach}>
              <SelectTrigger className="w-full flex-1">
                <SelectValue placeholder="Choose a shared tool" />
              </SelectTrigger>
              <SelectContent>
                {sharedAttachable.map((tool) => (
                  <SelectItem key={tool.id} value={tool.id}>
                    {tool.name} ({tool.kind})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              disabled={sharedToAttach === ""}
              onClick={() => {
                setAttached(sharedToAttach, true);
                setSharedToAttach("");
              }}
            >
              Attach
            </Button>
          </div>
        </div>
      ) : null}

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">Pack tools (read-only)</h3>
        {packsQuery.isLoading ? (
          <Skeleton className="h-6 w-48" />
        ) : (pack?.tool_names.length ?? 0) === 0 ? (
          <p className="text-sm text-muted-foreground">This pack registers no code tools.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {pack?.tool_names.map((name) => (
              <li key={name} className="rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-secondary-foreground">
                {name}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-muted-foreground">Provided by the &quot;{agent.pack_id}&quot; pack&apos;s code; not editable here.</p>
      </div>
    </div>
  );
}
