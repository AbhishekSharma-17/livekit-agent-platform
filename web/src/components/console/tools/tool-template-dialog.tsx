"use client";

import * as React from "react";
import { toast } from "sonner";
import { ExternalLinkIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { useInstantiateToolTemplate, useTools, useToolTemplates } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { ProviderSpec, ToolTemplate, ToolTemplateDefault, ToolTemplateInstantiated } from "@/contracts/lkap-contracts";

export interface ToolTemplateDialogProps {
  /** Attach created tools to this agent (`agent_id`); `null` creates shared tools, as `/console/tools` does. */
  agentId: string | null;
  secretBagSpec: ProviderSpec | undefined;
  /**
   * The agent's own `config.timezone` — shown as a hint for templates whose
   * arguments take the caller's time zone (R-V5-10, docs/v5/_asks.md #150:
   * `time_zone` is a required argument the model fills from the time note,
   * not a stored default). Omit outside an agent's editor.
   */
  businessTimezone?: string;
  onInstantiated: (result: ToolTemplateInstantiated) => void;
  trigger: React.ReactNode;
}

interface TemplateGroup {
  group: string;
  group_label: string;
  docs_url: string | null;
  templates: ToolTemplate[];
}

function groupTemplates(items: ToolTemplate[]): TemplateGroup[] {
  const groups: TemplateGroup[] = [];
  const byId = new Map<string, TemplateGroup>();
  for (const template of items) {
    let group = byId.get(template.group);
    if (!group) {
      group = { group: template.group, group_label: template.group_label, docs_url: template.docs_url ?? null, templates: [] };
      byId.set(template.group, group);
      groups.push(group);
    }
    group.templates.push(template);
  }
  return groups;
}

/**
 * "Add a tool" → "From a template" (V5-25's `GET /v1/tool-templates`, D-V5-36):
 * ready-made HTTP tools grouped by service — the Cal.com booking set today.
 * Pick a subset (or all) of a group, a key holding the group's secret names,
 * and any fixed arguments (`event_type_id`); `POST /v1/tool-templates/{id}/instantiate`
 * creates one tool per picked template. Instantiating binds a key, so it
 * needs `providers:write` like `POST /v1/tools` (docs/v5/_asks.md #154).
 */
export function ToolTemplateDialog({ agentId, secretBagSpec, businessTimezone, onInstantiated, trigger }: ToolTemplateDialogProps) {
  const [open, setOpen] = React.useState(false);
  const templatesQuery = useToolTemplates();
  const toolsQuery = useTools();
  const instantiate = useInstantiateToolTemplate();
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  const groups = React.useMemo(() => groupTemplates(templatesQuery.data?.items ?? []), [templatesQuery.data]);
  const [groupId, setGroupId] = React.useState<string | null>(null);
  const activeGroup = groups.find((g) => g.group === groupId) ?? groups[0];

  // Names already created in this scope (this agent, or shared when `agentId` is null) —
  // shown as "Already added" and left unchecked, so re-opening the dialog never offers
  // to create `booking_create` a second time by accident (docs/v5/_asks.md #154(e)).
  const existingNames = React.useMemo(
    () => new Set((toolsQuery.data?.items ?? []).filter((t) => (t.agent_id ?? null) === agentId).map((t) => t.name)),
    [toolsQuery.data, agentId],
  );

  const [picked, setPicked] = React.useState<Set<string>>(new Set());
  const [defaults, setDefaults] = React.useState<Record<string, string>>({});
  const [credentialId, setCredentialId] = React.useState<string | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setGroupId(null);
    setDefaults({});
    setCredentialId(null);
    setFormError(null);
  }, [open]);

  const activeGroupKey = activeGroup?.group;
  React.useEffect(() => {
    if (!activeGroup) return;
    setPicked(new Set(activeGroup.templates.filter((t) => !existingNames.has(t.definition.name)).map((t) => t.id)));
    // Re-derive only when the group changes (or the dialog reopens); toggling one
    // checkbox by hand must not be undone by an existingNames refetch mid-edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGroupKey, open]);

  function toggle(id: string, checked: boolean) {
    setPicked((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const chosen = activeGroup?.templates.filter((t) => picked.has(t.id) && !existingNames.has(t.definition.name)) ?? [];
  const defaultSpecs = new Map<string, ToolTemplateDefault>();
  for (const template of chosen) {
    for (const spec of template.defaults ?? []) defaultSpecs.set(spec.name, spec);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (!activeGroup || chosen.length === 0) return;
    if (!credentialId) {
      setFormError("Choose a key first.");
      return;
    }
    const missing = Array.from(defaultSpecs.values()).filter((spec) => spec.required && !(defaults[spec.name] ?? "").trim());
    if (missing.length > 0) {
      setFormError(`Fill in ${missing.map((spec) => spec.label).join(", ")} first.`);
      return;
    }
    try {
      const result = await instantiate.mutateAsync({
        templateId: activeGroup.group,
        body: {
          agent_id: agentId,
          credential_id: credentialId,
          names: chosen.map((t) => t.definition.name),
          defaults: Object.fromEntries(Array.from(defaultSpecs.keys()).map((name) => [name, defaults[name] ?? ""])),
          enabled: true,
        },
      });
      toast.success(result.names.length === 1 ? `Added "${result.names[0]}".` : `Added ${result.names.length} tools.`);
      onInstantiated(result);
      setOpen(false);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const secretNames = Array.from(new Set(chosen.flatMap((t) => t.secret_names)));
  const nothingToAdd = chosen.length === 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="lg">
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>Add tools from a template</DialogTitle>
            <DialogDescription>
              Ready-made tools for a service you already use. Pick the ones you want; each becomes its own tool you can
              edit or remove later.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="gap-5">
            {templatesQuery.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : templatesQuery.isError ? (
              <ErrorBanner message={errorMessage(templatesQuery.error)} onRetry={() => templatesQuery.refetch()} />
            ) : !activeGroup ? (
              <EmptyState compact title="No templates yet" description="Nothing to add from a template right now." />
            ) : (
              <>
                <div className="flex flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-foreground">{activeGroup.group_label}</h3>
                    {activeGroup.docs_url ? (
                      <a
                        href={activeGroup.docs_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-0.5 rounded-xs text-xs font-medium text-muted-foreground underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        Docs
                        <ExternalLinkIcon className="size-3" aria-hidden="true" />
                      </a>
                    ) : null}
                  </div>
                  {businessTimezone ? (
                    <p className="text-[0.8125rem] text-muted-foreground">
                      These tools ask for the caller&apos;s own time zone (the agent reads it from the time note at the
                      start of the call). Your business runs on <span className="font-mono">{businessTimezone}</span>.
                    </p>
                  ) : null}
                </div>

                <ul className="flex flex-col gap-2">
                  {activeGroup.templates.map((template) => {
                    const already = existingNames.has(template.definition.name);
                    const id = `tool-template-${template.id}`;
                    return (
                      <li key={template.id} className="flex items-start gap-2.5 rounded-md border border-border p-2.5">
                        <Checkbox
                          id={id}
                          className="mt-0.5"
                          checked={already || picked.has(template.id)}
                          disabled={already}
                          onCheckedChange={(checked) => toggle(template.id, checked === true)}
                        />
                        <label htmlFor={id} className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="text-sm font-medium text-foreground">{template.label}</span>
                            {template.risk === "write" ? (
                              <StatusChip tone="warning" size="sm">
                                Changes things
                              </StatusChip>
                            ) : (
                              <StatusChip tone="neutral" size="sm">
                                Read only
                              </StatusChip>
                            )}
                            {already ? (
                              <StatusChip tone="success" size="sm">
                                Already added
                              </StatusChip>
                            ) : null}
                          </span>
                          <span className="text-[0.8125rem] text-pretty text-muted-foreground">{template.summary}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>

                {defaultSpecs.size > 0 ? (
                  <div className="flex flex-col gap-3 border-t border-border pt-4">
                    <h4 className="text-xs font-semibold tracking-wide text-muted-foreground">Fixed values</h4>
                    {Array.from(defaultSpecs.values()).map((spec) => (
                      <Field key={spec.name} label={spec.label} htmlFor={`tool-template-default-${spec.name}`} hint={spec.help ?? undefined} required={spec.required}>
                        <Input
                          id={`tool-template-default-${spec.name}`}
                          value={defaults[spec.name] ?? ""}
                          onChange={(event) => setDefaults((prev) => ({ ...prev, [spec.name]: event.target.value }))}
                        />
                      </Field>
                    ))}
                  </div>
                ) : null}

                {secretBagSpec ? (
                  <div className="border-t border-border pt-4">
                    <CredentialPicker
                      spec={secretBagSpec}
                      value={credentialId}
                      onChange={setCredentialId}
                      label="Key"
                      required
                    />
                    {secretNames.length > 0 ? (
                      <p className="mt-1.5 text-xs text-muted-foreground">
                        The key must hold: <span className="font-mono">{secretNames.join(", ")}</span>.
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {formError ? <p className="text-[0.8125rem] text-danger-text">{formError}</p> : null}
              </>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!canWrite || !activeGroup || nothingToAdd || instantiate.isPending}
              title={canWrite ? undefined : writeReason}
            >
              {instantiate.isPending ? "Adding…" : nothingToAdd ? "Nothing to add" : `Add ${chosen.length} tool${chosen.length === 1 ? "" : "s"}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
