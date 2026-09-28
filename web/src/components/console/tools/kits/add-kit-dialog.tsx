"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
import {
  useDatasets,
  useInstantiateToolKit,
  useProviders,
  useToolProviderConnections,
} from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { KIT_PREFIX_PATTERN } from "@/components/console/tools/kits/constants";
import type {
  AgentOut,
  KitChange,
  KitSetting,
  KitVariant,
  ToolKit,
  ToolKitInstantiate,
  ToolKitInstantiated,
} from "@/contracts/lkap-contracts";

/** Where each `KitChange.kind` shows, in the order it's shown — ask #142's own list. */
const CHANGE_GROUPS: { kinds: KitChange["kind"][]; heading: string }[] = [
  { kinds: ["tool"], heading: "Tools" },
  { kinds: ["block"], heading: "Panel blocks" },
  { kinds: ["instructions"], heading: "Instructions" },
  { kinds: ["variable"], heading: "Fields" },
  { kinds: ["extraction"], heading: "Live extraction" },
  { kinds: ["rule"], heading: "Rules" },
  { kinds: ["flow_node", "flow_edge", "flow_variable"], heading: "Flow steps" },
  { kinds: ["test_case"], heading: "Test case" },
  { kinds: ["notify_team"], heading: "Team notification" },
];

const STATUS_LABEL: Record<KitChange["status"], string> = {
  added: "Will add",
  exists: "Already there",
  skipped: "Left out",
};
const STATUS_TONE: Record<KitChange["status"], StatusTone> = {
  added: "success",
  exists: "neutral",
  skipped: "warning",
};

interface Draft {
  variant: string;
  blockPrefix: string;
  settings: Record<string, string>;
  credentialId: string | null;
  connectionId: string | null;
  datasetId: string | null;
  keyColumns: string[];
  flowAnchor: string | null;
  addTestCase: boolean;
}

function draftFor(kit: ToolKit): Draft {
  return {
    variant: kit.default_variant,
    blockPrefix: kit.default_prefix,
    settings: {},
    credentialId: null,
    connectionId: null,
    datasetId: null,
    keyColumns: [],
    flowAnchor: null,
    addTestCase: true,
  };
}

/**
 * A setting's value as the form shows it: what the builder typed, else the kit's default
 * (ask #276 — the shown default is sent and counts as filled; a cleared input is empty).
 */
function settingValue(setting: KitSetting, draft: Draft): string {
  return draft.settings[setting.name] ?? setting.default ?? "";
}

/** Everything this variant of the kit is missing to be added (client-side echo of the settings it needs). */
function requiredSettingsMissing(kit: ToolKit, variant: KitVariant, draft: Draft): string[] {
  return (kit.defaults ?? [])
    .filter((setting) => setting.required && ((setting.variants ?? []).length === 0 || (setting.variants ?? []).includes(variant.id)))
    .filter((setting) => !settingValue(setting, draft).trim())
    .map((setting) => setting.label);
}

/**
 * "Add a kit" (V6-19, D-V6-26; ask #142): a form, then a required **Preview** (`dry_run:
 * true`) that lists every tool, panel block, instruction, field, rule and flow step the kit
 * would add — grouped, with a plain status per row — before **Add** ever becomes clickable.
 * Any change to the form after a preview clears it, so Add always reflects what was just shown.
 */
export function AddKitDialog({
  kit,
  agent,
  trigger,
}: {
  kit: ToolKit;
  agent: AgentOut;
  trigger: React.ReactNode;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft>(() => draftFor(kit));
  const [preview, setPreview] = React.useState<ToolKitInstantiated | null>(null);
  const [snippetOpen, setSnippetOpen] = React.useState(false);
  const instantiate = useInstantiateToolKit();
  // `open` gates every fetch (ask #234's pattern, `create-kb-dialog.tsx`): the gallery mounts
  // one of these dialogs per kit, so nothing here fetches until the admin opens one.
  const providersQuery = useProviders({ enabled: open });
  const connectionsQuery = useToolProviderConnections({ enabled: open });
  const datasetsQuery = useDatasets({ enabled: open });
  const { canWrite: canAdmin } = useWriteAccess("admin");
  const adminReason = writeAccessReason("admin");

  React.useEffect(() => {
    if (open) {
      setDraft(draftFor(kit));
      setPreview(null);
      setSnippetOpen(false);
    }
  }, [open, kit]);

  const variant = kit.variants.find((item) => item.id === draft.variant) ?? kit.variants[0];
  // Every `KitRequires`/`KitSetting` field the generator marks optional (a Pydantic default,
  // not an absent field) — normalised once here rather than `?? []` at every call site below.
  const requires = {
    secretNames: variant.requires?.secret_names ?? [],
    apps: variant.requires?.apps ?? [],
    dataset: variant.requires?.dataset ?? null,
    minKeyColumns: variant.requires?.min_key_columns ?? 1,
  };
  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const connections = (connectionsQuery.data?.items ?? []).filter(
    (connection) => requires.apps.includes(connection.toolkit) && connection.status === "active",
  );
  const readyDatasets = (datasetsQuery.data?.items ?? []).filter((item) => item.status === "ready");
  const dataset = readyDatasets.find((item) => item.id === draft.datasetId);
  // `kit_apply.py::_add_flow` only accepts an anchor that is a conversation step (`agent` or
  // `start`) — offering anything else would just fail Preview with an error naming this field.
  // `kind` is optional on the wire (its pydantic default, `flow-model.ts`'s own `kindOf`
  // convention): an agent step's `kind` may be omitted, never absent-means-something-else.
  const flowNodes = (agent.config.flow?.nodes ?? []).filter((node) => (node.kind ?? "agent") === "agent" || node.kind === "start");
  const flowFragment = variant.flow_nodes ?? kit.flow_nodes;
  const settingsForVariant = (kit.defaults ?? []).filter(
    (setting) => (setting.variants ?? []).length === 0 || (setting.variants ?? []).includes(variant.id),
  );

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setPreview(null);
  }

  function buildBody(dryRun: boolean): ToolKitInstantiate {
    const settings: Record<string, string | number | boolean> = {};
    for (const setting of settingsForVariant) {
      const raw = settingValue(setting, draft);
      if (raw === "") continue;
      settings[setting.name] = setting.kind === "integer" ? Number(raw) : raw;
    }
    return {
      agent_id: agent.id,
      variant: variant.id,
      block_prefix: draft.blockPrefix || null,
      settings,
      credential_id: draft.credentialId,
      connection_id: draft.connectionId,
      dataset_id: draft.datasetId,
      key_columns: draft.datasetId ? draft.keyColumns : null,
      flow_anchor: draft.flowAnchor,
      add_test_case: draft.addTestCase,
      dry_run: dryRun,
    };
  }

  const missingSettings = requiredSettingsMissing(kit, variant, draft);
  const prefixValid = new RegExp(KIT_PREFIX_PATTERN).test(draft.blockPrefix);
  const needsDataset = variant.source === "dataset" && !draft.datasetId;
  const needsMoreKeyColumns =
    variant.source === "dataset" && draft.datasetId && draft.keyColumns.length < requires.minKeyColumns;
  const canPreview =
    prefixValid && missingSettings.length === 0 && !needsDataset && !needsMoreKeyColumns && !instantiate.isPending;

  async function handlePreview() {
    try {
      const result = await instantiate.mutateAsync({ kitId: kit.id, body: buildBody(true) });
      setPreview(result);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function handleAdd() {
    try {
      const result = await instantiate.mutateAsync({ kitId: kit.id, body: buildBody(false) });
      setOpen(false);
      toast.success(
        (result.tool_ids ?? []).length > 0
          ? `"${kit.name}" added — ${(result.tool_ids ?? []).length === 1 ? "1 tool" : `${(result.tool_ids ?? []).length} tools`} attached.`
          : `"${kit.name}" added.`,
      );
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const canAdd = preview !== null && preview.validation.ok !== false;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <DialogHeader>
          <DialogTitle>Add &ldquo;{kit.name}&rdquo;</DialogTitle>
          <DialogDescription id={`${uid}-description`}>{kit.summary}</DialogDescription>
        </DialogHeader>

        <DialogBody className="gap-6">
          <section className="flex flex-col gap-4">
            {kit.variants.length > 1 ? (
              <Field label="How it runs" htmlFor={`${uid}-variant`}>
                <Select value={variant.id} onValueChange={(next) => set("variant", next)}>
                  <SelectTrigger id={`${uid}-variant`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {kit.variants.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-[0.8125rem] text-muted-foreground">{variant.summary}</p>
              </Field>
            ) : null}

            <Field
              label="Name prefix"
              htmlFor={`${uid}-prefix`}
              required
              hint="Names what this kit adds — add it twice under two prefixes for two of the same job."
              error={!prefixValid ? "Lower case letters, digits and _, starting with a letter." : undefined}
            >
              <Input
                id={`${uid}-prefix`}
                className="w-48 font-mono text-sm"
                value={draft.blockPrefix}
                onChange={(e) => set("blockPrefix", e.target.value)}
              />
            </Field>

            {settingsForVariant.map((setting) => (
              <Field
                key={setting.name}
                label={setting.label}
                htmlFor={`${uid}-setting-${setting.name}`}
                required={setting.required}
                optional={!setting.required}
                hint={setting.help}
              >
                <Input
                  id={`${uid}-setting-${setting.name}`}
                  type={setting.kind === "integer" ? "number" : "text"}
                  value={settingValue(setting, draft)}
                  placeholder={setting.example ?? undefined}
                  onChange={(e) => set("settings", { ...draft.settings, [setting.name]: e.target.value })}
                />
              </Field>
            ))}

            {requires.secretNames.length > 0 ? (
              secretBagSpec ? (
                <div
                  className={canAdmin ? undefined : "pointer-events-none opacity-50"}
                  aria-disabled={!canAdmin}
                  title={canAdmin ? undefined : adminReason}
                >
                  <CredentialPicker
                    spec={secretBagSpec}
                    value={draft.credentialId}
                    onChange={(id) => set("credentialId", id)}
                    required={false}
                    label="Key"
                  />
                </div>
              ) : null
            ) : null}
            {requires.secretNames.length > 0 && !draft.credentialId ? (
              <p className="text-[0.8125rem] text-muted-foreground">
                Added without a key; the calls run unauthenticated until an admin adds one.
              </p>
            ) : null}

            {variant.source === "composio_action" ? (
              canAdmin ? (
                <Field label="Connected app" htmlFor={`${uid}-connection`} required>
                  <Select value={draft.connectionId ?? undefined} onValueChange={(next) => set("connectionId", next)}>
                    <SelectTrigger id={`${uid}-connection`} className="w-full">
                      <SelectValue placeholder="Choose a connected app…" />
                    </SelectTrigger>
                    <SelectContent>
                      {connections.map((connection) => (
                        <SelectItem key={connection.id} value={connection.id}>
                          {connection.label ? `${connection.toolkit_name ?? connection.toolkit} (${connection.label})` : (connection.toolkit_name ?? connection.toolkit)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {connections.length === 0 ? (
                    <p className="mt-1 text-[0.8125rem] text-muted-foreground">
                      No connected app of this kind yet — connect one under Tools → Apps.
                    </p>
                  ) : null}
                </Field>
              ) : (
                <p className="text-[0.8125rem] text-muted-foreground">
                  Connecting an app needs an admin — {adminReason}.
                </p>
              )
            ) : null}

            {variant.source === "dataset" ? (
              <Field label="Lookup table" htmlFor={`${uid}-dataset`} required>
                <Select value={draft.datasetId ?? undefined} onValueChange={(next) => set("datasetId", next)}>
                  <SelectTrigger id={`${uid}-dataset`} className="w-full">
                    <SelectValue placeholder="Choose a lookup table…" />
                  </SelectTrigger>
                  <SelectContent>
                    {readyDatasets.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {dataset ? (
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
                    {dataset.key_columns.map((column) => (
                      <label key={column.name} className="flex items-center gap-1.5 text-sm">
                        <input
                          type="checkbox"
                          className="size-4"
                          checked={draft.keyColumns.includes(column.name)}
                          onChange={(e) =>
                            set(
                              "keyColumns",
                              e.target.checked
                                ? [...draft.keyColumns, column.name]
                                : draft.keyColumns.filter((c) => c !== column.name),
                            )
                          }
                        />
                        <span className="font-mono text-[0.8125rem]">{column.name}</span>
                      </label>
                    ))}
                  </div>
                ) : null}
                {needsMoreKeyColumns ? (
                  <p className="mt-1 text-[0.8125rem] text-danger-text">
                    Match on at least {requires.minKeyColumns}{" "}
                    {requires.minKeyColumns === 1 ? "column" : "columns"}.
                  </p>
                ) : null}
              </Field>
            ) : null}

            {flowFragment && flowNodes.length > 0 ? (
              <Field
                label="Add after this flow step"
                htmlFor={`${uid}-anchor`}
                optional
                hint="Leave unset to skip the flow steps this kit would otherwise add."
              >
                <Select value={draft.flowAnchor ?? undefined} onValueChange={(next) => set("flowAnchor", next)}>
                  <SelectTrigger id={`${uid}-anchor`} className="w-full">
                    <SelectValue placeholder="Don't add flow steps" />
                  </SelectTrigger>
                  <SelectContent>
                    {flowNodes.map((node) => (
                      <SelectItem key={node.id} value={node.id}>
                        {node.label || node.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            ) : null}

            {kit.test_case ? (
              <Field inline label="Add the sample conversation" htmlFor={`${uid}-test-case`} hint="Tries the kit offline — its tools answer from fakes.">
                <Switch
                  id={`${uid}-test-case`}
                  checked={draft.addTestCase}
                  onCheckedChange={(v) => set("addTestCase", v)}
                />
              </Field>
            ) : null}
          </section>

          {preview ? (
            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">What this adds</h3>
              {CHANGE_GROUPS.map((group) => {
                const rows = preview.changes.filter((change) => group.kinds.includes(change.kind));
                if (rows.length === 0) return null;
                return (
                  <div key={group.heading} className="flex flex-col gap-1.5">
                    <h4 className="text-sm font-medium text-foreground">{group.heading}</h4>
                    <ul className="flex flex-col gap-1 rounded-md border border-border p-2.5">
                      {rows.map((change) => (
                        <li key={`${change.kind}-${change.id}`} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.8125rem]">
                          <StatusChip tone={STATUS_TONE[change.status]} size="sm">
                            {STATUS_LABEL[change.status]}
                          </StatusChip>
                          <span className="font-medium text-foreground">{change.label || change.id}</span>
                          {change.note ? <span className="text-muted-foreground">— {change.note}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}

              {(preview.notes ?? []).length > 0 ? (
                <ul className="flex flex-col gap-1">
                  {(preview.notes ?? []).map((note) => (
                    <li key={note} className="text-[0.8125rem] text-muted-foreground">
                      {note}
                    </li>
                  ))}
                </ul>
              ) : null}

              {!preview.validation.ok ? (
                <div className="rounded-md border border-danger-text/20 bg-danger-soft p-2.5 text-[0.8125rem]">
                  <p className="font-medium text-danger-text">This would leave the agent&rsquo;s configuration invalid.</p>
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {(preview.validation.errors ?? []).map((message) => (
                      <li key={message} className="text-danger-text">
                        {message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (preview.validation.warnings ?? []).length > 0 ? (
                <ul className="flex flex-col gap-0.5">
                  {(preview.validation.warnings ?? []).map((message) => (
                    <li key={message} className="text-[0.8125rem] text-warning-text">
                      {message}
                    </li>
                  ))}
                </ul>
              ) : null}

              <Collapsible open={snippetOpen} onOpenChange={setSnippetOpen}>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" size="sm" className="w-fit text-xs text-muted-foreground">
                    {snippetOpen ? "Hide" : "Show"} the instructions this adds
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <pre className="mt-1 max-h-48 overflow-y-auto rounded-md bg-muted p-2.5 text-xs whitespace-pre-wrap">
                    {preview.instructions_snippet}
                  </pre>
                </CollapsibleContent>
              </Collapsible>
            </section>
          ) : null}
        </DialogBody>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          {preview === null ? (
            <Button type="button" disabled={!canPreview} onClick={() => void handlePreview()}>
              {instantiate.isPending ? "Checking…" : "Preview"}
            </Button>
          ) : (
            <Button type="button" disabled={!canAdd || instantiate.isPending} onClick={() => void handleAdd()}>
              {instantiate.isPending ? "Adding…" : "Add"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
