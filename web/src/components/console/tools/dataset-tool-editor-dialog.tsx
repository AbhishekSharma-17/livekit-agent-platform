"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateTool, useDatasets, useUpdateTool } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { BindingsEditor, bindingsHaveIssues } from "@/components/console/tools/bindings-editor";
import { PinnedArgumentsEditor, type PinnedValue } from "@/components/console/tools/pinned-arguments-editor";
import { RequiresVarsField, requiresVarsHaveIssues } from "@/components/console/tools/requires-vars-field";
import { useAgentToolContextOptions } from "@/components/console/tools/use-agent-tool-context";
import { TOOL_ARGUMENT_PATTERN } from "@/components/console/tools/tool-context";
import type { DatasetOut, DatasetToolDefinition, ToolBinding, ToolOut } from "@/contracts/lkap-contracts";

interface Draft {
  name: string;
  description: string;
  datasetId: string;
  keyColumns: string[];
  returnColumns: string[];
  match: "exact" | "prefix";
  maxRows: number;
  maxResultChars: number;
  enabled: boolean;
  requiresVars: string[];
  bindings: ToolBinding[];
  pinnedArguments: Record<string, PinnedValue>;
}

function draftFromTool(tool: ToolOut | undefined): Draft {
  const def = tool?.definition.kind === "dataset" ? tool.definition : undefined;
  return {
    name: tool?.name ?? "",
    description: def?.description ?? "",
    datasetId: def?.dataset_id ?? "",
    keyColumns: def?.key_columns ?? [],
    returnColumns: def?.return_columns ?? [],
    match: def?.match ?? "exact",
    maxRows: def?.max_rows ?? 5,
    maxResultChars: def?.max_result_chars ?? 2000,
    enabled: tool?.enabled ?? true,
    requiresVars: def?.requires_vars ?? [],
    bindings: def?.bindings ?? [],
    pinnedArguments: (def?.pinned_arguments ?? {}) as Record<string, PinnedValue>,
  };
}

interface DraftErrors {
  name?: string;
  dataset?: string;
  keyColumns?: string;
  pinnedArguments?: string;
}

/**
 * Create/edit a lookup-table tool (`kind: "dataset"`, V6-16/V6-19, D-V6-27): pick a lookup
 * table, which of its key columns this tool matches on and which columns a found row carries
 * back, then the V6-11 session-value controls (ask #104's note on reusing them). Until this
 * dialog existed, `tool-row.tsx` showed such a tool as "Lookup table · by <keys>" with no way
 * to edit it (ask #104).
 */
export function DatasetToolEditorDialog({
  agentId,
  tool,
  trigger,
  onSaved,
}: {
  agentId: string | null;
  tool?: ToolOut;
  trigger: React.ReactNode;
  onSaved: (tool: ToolOut) => void;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState(() => draftFromTool(tool));
  const [errors, setErrors] = React.useState<DraftErrors>({});
  const createTool = useCreateTool();
  const updateTool = useUpdateTool();
  // `open` gates the fetch (ask #234's pattern, `create-kb-dialog.tsx`): the shared tools list
  // and every agent's Tools tab mount this dialog's trigger unconditionally, so a table lookup
  // never happens until the admin actually opens it.
  const datasetsQuery = useDatasets({ enabled: open });
  const agentContext = useAgentToolContextOptions(agentId);

  React.useEffect(() => {
    if (open) {
      setDraft(draftFromTool(tool));
      setErrors({});
    }
  }, [open, tool]);

  const datasets = datasetsQuery.data?.items ?? [];
  const readyDatasets = datasets.filter((item) => item.status === "ready");
  const dataset: DatasetOut | undefined = datasets.find((item) => item.id === draft.datasetId);
  const keyColumnOptions = dataset?.key_columns ?? [];
  const columnOptions = dataset?.columns ?? [];

  function chooseDataset(datasetId: string) {
    const next = datasets.find((item) => item.id === datasetId);
    setDraft((d) => ({
      ...d,
      datasetId,
      // A different table's columns rarely mean the same thing — start fresh rather than
      // carry over key/return picks (and pinned values) that would silently refuse at save.
      keyColumns: next?.key_columns.length === 1 ? [next.key_columns[0].name] : [],
      returnColumns: [],
      pinnedArguments: {},
    }));
  }

  function toggleKeyColumn(name: string, checked: boolean) {
    setDraft((d) => ({
      ...d,
      keyColumns: checked ? [...d.keyColumns, name] : d.keyColumns.filter((c) => c !== name),
      // A pinned value only makes sense for a column this tool still matches on.
      pinnedArguments: checked
        ? d.pinnedArguments
        : Object.fromEntries(Object.entries(d.pinnedArguments).filter(([key]) => key !== name)),
    }));
  }

  function toggleReturnColumn(name: string, checked: boolean) {
    setDraft((d) => ({
      ...d,
      returnColumns: checked ? [...d.returnColumns, name] : d.returnColumns.filter((c) => c !== name),
    }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    const nextErrors: DraftErrors = {};
    if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(draft.name)) {
      nextErrors.name = "Use letters, numbers or underscore; start with a letter or underscore.";
    }
    if (!draft.datasetId) {
      nextErrors.dataset = "Choose a lookup table.";
    } else if (dataset && dataset.status !== "ready") {
      nextErrors.dataset = "This table is still importing.";
    }
    if (draft.keyColumns.length === 0) {
      nextErrors.keyColumns = "Match on at least one column.";
    }
    const pinnedOutsideKeys = Object.keys(draft.pinnedArguments).filter((name) => !draft.keyColumns.includes(name));
    if (pinnedOutsideKeys.length > 0) {
      nextErrors.pinnedArguments = `Fixed values can only name a match-on column: ${pinnedOutsideKeys.join(", ")}`;
    } else if (Object.keys(draft.pinnedArguments).some((name) => !TOOL_ARGUMENT_PATTERN.test(name))) {
      nextErrors.pinnedArguments = "Fixed value names must be column names.";
    }

    if (requiresVarsHaveIssues(draft.requiresVars) || bindingsHaveIssues(draft.bindings)) {
      nextErrors.pinnedArguments ??= "Fix the session values or bindings below.";
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const definition: DatasetToolDefinition = {
      kind: "dataset",
      name: draft.name,
      description: draft.description,
      dataset_id: draft.datasetId,
      key_columns: draft.keyColumns as DatasetToolDefinition["key_columns"],
      return_columns: draft.returnColumns,
      match: draft.match,
      max_rows: draft.maxRows,
      max_result_chars: draft.maxResultChars,
      requires_vars: draft.requiresVars,
      bindings: draft.bindings,
      pinned_arguments: draft.pinnedArguments,
    };

    try {
      const saved = tool
        ? await updateTool.mutateAsync({
            id: tool.id,
            body: { agent_id: agentId, kind: "dataset", name: draft.name, definition, enabled: draft.enabled },
          })
        : await createTool.mutateAsync({
            agent_id: agentId,
            kind: "dataset",
            name: draft.name,
            definition,
            enabled: draft.enabled,
          });
      setOpen(false);
      toast.success(`Lookup tool "${saved.name}" saved.`);
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const pending = createTool.isPending || updateTool.isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>{tool ? "Edit lookup tool" : "New lookup tool"}</DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              Looks rows up in one of the workspace&rsquo;s lookup tables. Read-only — nothing is ever written back.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="gap-6">
            <section className="flex flex-col gap-4">
              <h3 className="text-body font-semibold text-foreground">Basics</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name}>
                  <Input
                    id={`${uid}-name`}
                    className="font-mono text-body"
                    value={draft.name}
                    onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                    placeholder="lookup_policy"
                  />
                </Field>
                <Field inline label="Enabled" htmlFor={`${uid}-enabled`}>
                  <Switch
                    id={`${uid}-enabled`}
                    checked={draft.enabled}
                    onCheckedChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
                  />
                </Field>
                <div className="sm:col-span-2">
                  <Field label="Description (shown to the model)" htmlFor={`${uid}-description-field`}>
                    <Textarea
                      id={`${uid}-description-field`}
                      rows={2}
                      value={draft.description}
                      onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                      placeholder="Look a caller's policy up by policy number or phone."
                    />
                  </Field>
                </div>
              </div>
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-body font-semibold text-foreground">Lookup table</h3>
              <Field label="Table" htmlFor={`${uid}-dataset`} required error={errors.dataset}>
                <Select value={draft.datasetId || undefined} onValueChange={chooseDataset}>
                  <SelectTrigger id={`${uid}-dataset`} className="w-full">
                    <SelectValue placeholder="Choose a lookup table…" />
                  </SelectTrigger>
                  <SelectContent>
                    {readyDatasets.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                    {dataset && dataset.status !== "ready" ? (
                      <SelectItem value={dataset.id}>{dataset.name} (still importing)</SelectItem>
                    ) : null}
                  </SelectContent>
                </Select>
                {readyDatasets.length === 0 ? (
                  <p className="mt-1 text-label text-text-secondary">
                    No lookup tables ready yet — add one under Lookup tables.
                  </p>
                ) : null}
              </Field>

              {dataset ? (
                <>
                  <ColumnChecklist
                    label="Match on these columns"
                    hint="A caller's lookup must match every one of these."
                    error={errors.keyColumns}
                    options={keyColumnOptions.map((column) => ({ value: column.name, label: `${column.name} (${column.type})` }))}
                    value={draft.keyColumns}
                    onToggle={toggleKeyColumn}
                  />
                  <ColumnChecklist
                    label="Columns returned"
                    hint="Leave every box unchecked to return every column."
                    options={columnOptions.map((column) => ({ value: column.name, label: column.label || column.name }))}
                    value={draft.returnColumns}
                    onToggle={toggleReturnColumn}
                  />
                </>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Match" htmlFor={`${uid}-match`}>
                  <Select value={draft.match} onValueChange={(v) => setDraft((d) => ({ ...d, match: v as "exact" | "prefix" }))}>
                    <SelectTrigger id={`${uid}-match`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="exact">Exactly</SelectItem>
                      <SelectItem value="prefix">Starts with</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Rows returned at most" htmlFor={`${uid}-max-rows`} hint="1 to 20.">
                  <Input
                    id={`${uid}-max-rows`}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={20}
                    value={draft.maxRows}
                    onChange={(e) => setDraft((d) => ({ ...d, maxRows: Number(e.target.value) }))}
                  />
                </Field>
                <Field label="Max result characters" htmlFor={`${uid}-max-result-chars`}>
                  <Input
                    id={`${uid}-max-result-chars`}
                    type="number"
                    inputMode="numeric"
                    min={100}
                    max={8000}
                    value={draft.maxResultChars}
                    onChange={(e) => setDraft((d) => ({ ...d, maxResultChars: Number(e.target.value) }))}
                  />
                </Field>
              </div>
            </section>

            <section className="flex flex-col gap-5 border-t border-border pt-5">
              <h3 className="text-body font-semibold text-foreground">Session and variables</h3>
              <RequiresVarsField
                uid={uid}
                values={draft.requiresVars}
                onChange={(requiresVars) => setDraft((d) => ({ ...d, requiresVars }))}
                knownVariables={agentContext.variableNames}
              />
              <BindingsEditor
                uid={uid}
                values={draft.bindings}
                onChange={(bindings) => setDraft((d) => ({ ...d, bindings }))}
                detailsBlocks={agentContext.detailsBlocks}
                tableBlocks={agentContext.tableBlocks}
              />
              <PinnedArgumentsEditor
                title="Fixed values"
                description="Fix a match-on column's value so the model never supplies it — e.g. {{ ctx.caller_phone }} for a caller-number lookup."
                emptyLabel="No fixed values yet."
                values={draft.pinnedArguments}
                onChange={(pinnedArguments) => setDraft((d) => ({ ...d, pinnedArguments }))}
                variableNames={agentContext.variableNames}
              />
              {errors.pinnedArguments ? <p className="text-label text-destructive-text">{errors.pinnedArguments}</p> : null}
              {!agentId ? (
                <p className="text-label text-text-secondary">
                  Attach this tool to an agent to pick from its panel blocks and flow variables.
                </p>
              ) : null}
            </section>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {pending ? "Saving…" : "Save tool"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ColumnChecklist({
  label,
  hint,
  error,
  options,
  value,
  onToggle,
}: {
  label: string;
  hint?: string;
  error?: string;
  options: { value: string; label: string }[];
  value: readonly string[];
  onToggle: (name: string, checked: boolean) => void;
}) {
  const id = React.useId();
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-body font-medium text-foreground" id={`${id}-label`}>
        {label}
      </span>
      {hint ? <p className="text-label text-text-secondary">{hint}</p> : null}
      {options.length === 0 ? (
        <p className="text-label text-text-secondary">This table has no columns yet.</p>
      ) : (
        <div role="group" aria-labelledby={`${id}-label`} className="flex flex-wrap gap-x-4 gap-y-1.5">
          {options.map((option) => (
            <label key={option.value} className="flex items-center gap-1.5 text-body">
              <input
                type="checkbox"
                className="size-4"
                checked={value.includes(option.value)}
                onChange={(e) => onToggle(option.value, e.target.checked)}
              />
              <span className="font-mono text-label">{option.label}</span>
            </label>
          ))}
        </div>
      )}
      {error ? <p className="text-label text-destructive-text">{error}</p> : null}
    </div>
  );
}
