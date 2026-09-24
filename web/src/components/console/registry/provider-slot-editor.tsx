"use client";

import * as React from "react";
import { ChevronRightIcon, ExternalLinkIcon } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useProviders } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { ModelCombobox } from "@/components/console/registry/model-combobox";
import {
  inferenceProviderFor,
  isInferenceProvider,
  isVerified,
  slotAvailability,
  unavailableCopy,
  type ProviderKind,
} from "@/components/console/registry/provider-meta";
import { defaultFieldValues, RegistryForm } from "@/components/console/registry/registry-form";
import { cn } from "@/lib/utils";
import type { FieldSpec, ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * What a slot may offer. Every member is optional; `{}` is the v1 behaviour.
 * V2-13 extends slots by passing constraints, not by editing this file's
 * internals: `disabledReason` for "not installed on <connection>" /
 * "disabled for this workspace" / "needs text modality", `include` to scope
 * the list, `inference: "off"` for self-hosted connections.
 */
export interface SlotConstraints {
  /** The slot must hold a provider (the card offers no "Remove"). Default `false`. */
  required?: boolean;
  /**
   * `"auto"` (default): offer "LiveKit Inference" when the registry has an
   * Inference provider of this kind. `"off"`: own key only (e.g. a
   * self-hosted connection, which cannot reach Inference).
   */
  inference?: "auto" | "off";
  /** Hide providers entirely (applied before grouping). */
  include?: (spec: ProviderSpec) => boolean;
  /**
   * Extra gate, composed after the registry's availability gate (R-V2-1):
   * return a sentence (with a fix) to show the provider disabled, or `null`.
   */
  disabledReason?: (spec: ProviderSpec) => string | null;
  /** Open the model list filtered to vision-capable models. */
  preferVision?: boolean;
  /**
   * Render an option field read-only with this reason (or `null` to leave it
   * editable). The providers section uses it for endpoint fields below
   * `admin` (V2-22, R-V2-33); the api refuses those edits either way.
   */
  fieldLockedReason?: (field: FieldSpec) => string | null;
}

export interface ProviderSlotEditorProps {
  /** Which registry kind this slot takes (`stt`, `llm`, `tts`, `realtime`, `avatar`, `image_gen`, …). */
  kind: ProviderKind;
  /** The slot's `ProviderRef` (null = not set). */
  value: ProviderRef | null | undefined;
  /** Called with the next `ProviderRef` (or null). The payload shape is the contract's `ProviderRef`, unchanged. */
  onChange: (next: ProviderRef | null) => void;
  constraints?: SlotConstraints;
  /** Registry override (tests, or V2-13's enriched `ProviderOut[]`); defaults to `GET /v1/providers`. */
  providers?: ProviderSpec[];
  /** Prefix for control ids (unique per slot on the page). */
  idPrefix?: string;
  /** Controlled model-list open state (the vision note opens it). */
  modelPickerOpen?: boolean;
  onModelPickerOpenChange?: (open: boolean) => void;
}

type RunChoice = "inference" | "own";

/** Kinds where vision / tool badges mean something (the registry sets them on every entry). */
export const THINKING_KINDS: ReadonlySet<ProviderKind> = new Set<ProviderKind>(["llm", "realtime"]);
/** Kinds where a voice count means something. */
export const SPEAKING_KINDS: ReadonlySet<ProviderKind> = new Set<ProviderKind>(["tts", "realtime"]);

interface UnavailableEntry {
  spec: ProviderSpec;
  reason: { chip: string; reason: string };
}

/** The registry, from props or from the shared `useProviders()` cache. */
export function useSlotProviders(providers?: ProviderSpec[]): ProviderSpec[] {
  const query = useProviders({ enabled: providers === undefined });
  return providers ?? query.data?.providers ?? [];
}

/** A fresh `ProviderRef` for a newly picked provider. */
export function newProviderRef(spec: ProviderSpec): ProviderRef {
  return {
    provider_id: spec.id,
    credential_id: null,
    model: spec.default_model ?? null,
    fields: defaultFieldValues(spec.fields ?? []),
  };
}

/**
 * One pipeline slot's editor — the expanded body of a slot card
 * (docs/UI_UX_SPEC.md §4.4 step 3, §7.5 item 2):
 *   1. "Run it with": LiveKit Inference (no key) vs your own key;
 *   2. vendor list (own key): selectable cards first, the rest under
 *      "More providers" with a reason chip, never hidden silently;
 *   3. model (`ModelCombobox`);
 *   4. key (`CredentialPicker`, own key only);
 *   5. options (`RegistryForm`).
 *
 * Contract (V2-13 composes on this): `kind`, `value`, `onChange`,
 * `constraints`. It reads no form context, so it works for any slot path
 * (`pipeline.stt`, `pipeline.vad`, a flow node's override, …).
 */
export function ProviderSlotEditor({
  kind,
  value,
  onChange,
  constraints = {},
  providers: providersProp,
  idPrefix,
  modelPickerOpen,
  onModelPickerOpenChange,
}: ProviderSlotEditorProps) {
  const registry = useSlotProviders(providersProp);
  const autoId = React.useId();
  const prefix = idPrefix ?? `slot-${kind}-${autoId}`;

  const ofKind = React.useMemo(
    () =>
      registry.filter(
        (p) => p.kind === kind && slotAvailability(p) !== "removed" && (constraints.include?.(p) ?? true),
      ),
    [registry, kind, constraints],
  );
  const inference = constraints.inference === "off" ? undefined : inferenceProviderFor(kind, ofKind);
  const current = value ? ofKind.find((p) => p.id === value.provider_id) : undefined;
  const currentIsInference = current ? isInferenceProvider(current) : false;

  // Remember the other choice's config so toggling back does not lose it.
  const remembered = React.useRef<Partial<Record<RunChoice, ProviderRef>>>({});
  // An empty slot starts with no run choice, so picking "LiveKit Inference" fills it.
  const [run, setRun] = React.useState<RunChoice | null>(null);
  const effectiveRun: RunChoice | null = !inference
    ? "own"
    : value && current
      ? currentIsInference
        ? "inference"
        : "own"
      : run;

  React.useEffect(() => {
    if (value && current) remembered.current[isInferenceProvider(current) ? "inference" : "own"] = value;
  }, [value, current]);

  function chooseRun(next: RunChoice) {
    setRun(next);
    if (next === effectiveRun && value) return;
    if (next === "inference" && inference) {
      onChange(remembered.current.inference ?? newProviderRef(inference));
    } else if (next === "own") {
      onChange(remembered.current.own ?? null);
    }
  }

  function chooseVendor(spec: ProviderSpec) {
    if (value?.provider_id === spec.id) return;
    onChange(newProviderRef(spec));
  }

  const vendors = ofKind.filter((p) => !isInferenceProvider(p));
  const selectable: ProviderSpec[] = [];
  const unavailable: UnavailableEntry[] = [];
  for (const spec of vendors) {
    const reason = disabledReasonFor(spec, constraints);
    if (reason) unavailable.push({ spec, reason });
    else selectable.push(spec);
  }

  const showVendors = effectiveRun === "own";
  const models = current?.models ?? [];
  const showModel = Boolean(current) && (models.length > 0 || Boolean(current?.default_model));

  return (
    <div className="flex flex-col gap-5" data-slot="provider-slot-editor" data-kind={kind}>
      {inference ? (
        <RunChoiceControl
          name={`${prefix}-run`}
          value={effectiveRun}
          onChange={chooseRun}
        />
      ) : null}

      {showVendors ? (
        <VendorList
          name={`${prefix}-vendor`}
          kind={kind}
          selectedId={value?.provider_id ?? null}
          selectable={selectable}
          unavailable={unavailable}
          onSelect={chooseVendor}
        />
      ) : null}

      {current && value ? (
        <>
          {current.notes ? (
            <p data-slot="provider-notes" className="text-[0.8125rem] text-pretty break-words text-muted-foreground">
              {current.notes}
            </p>
          ) : null}

          {showModel ? (
            <Field label="Model" htmlFor={`${prefix}-model`} hint={current.default_model ? undefined : "Type any id the provider accepts."}>
              <ModelCombobox
                id={`${prefix}-model`}
                models={models}
                value={value.model ?? ""}
                defaultModel={current.default_model}
                onChange={(model) => onChange({ ...value, model: model.trim() === "" ? null : model.trim() })}
                visionOnly={constraints.preferVision}
                open={modelPickerOpen}
                onOpenChange={onModelPickerOpenChange}
              />
            </Field>
          ) : null}

          {current.requires_credential !== false && !isInferenceProvider(current) ? (
            <CredentialPicker
              id={`${prefix}-credential`}
              spec={current}
              value={value.credential_id}
              onChange={(credentialId) => onChange({ ...value, credential_id: credentialId })}
            />
          ) : null}

          {(current.fields ?? []).length > 0 ? (
            <div className="flex flex-col gap-3">
              <h4 className="text-sm font-semibold text-foreground">Options</h4>
              <RegistryForm
                fields={current.fields ?? []}
                values={value.fields ?? {}}
                onChange={(name, fieldValue) => onChange({ ...value, fields: { ...(value.fields ?? {}), [name]: fieldValue } })}
                idPrefix={`${prefix}-field`}
                voices={current.capabilities?.voices}
                lockedReason={constraints.fieldLockedReason}
                catalogContext={{
                  providerId: current.id,
                  kind: current.kind,
                  catalog: current.catalog,
                  credentialId: value.credential_id ?? null,
                }}
              />
            </div>
          ) : null}
        </>
      ) : value && !current ? (
        <p className="text-[0.8125rem] text-warning-text">
          This slot uses <span className="font-mono">{value.provider_id}</span>, which isn&apos;t in the provider registry any
          more. Pick another provider.
        </p>
      ) : null}
    </div>
  );
}

function disabledReasonFor(spec: ProviderSpec, constraints: SlotConstraints): { chip: string; reason: string } | null {
  if (slotAvailability(spec) !== "selectable") return unavailableCopy(spec);
  const extra = constraints.disabledReason?.(spec) ?? null;
  return extra ? { chip: "Unavailable", reason: extra } : null;
}

function RunChoiceControl({
  name,
  value,
  onChange,
}: {
  name: string;
  value: RunChoice | null;
  onChange: (next: RunChoice) => void;
}) {
  const options: { value: RunChoice; title: string; hint: string }[] = [
    { value: "inference", title: "LiveKit Inference", hint: "No key needed; billed through LiveKit Cloud." },
    { value: "own", title: "Your own key", hint: "Pick a vendor and use your account with them." },
  ];
  return (
    <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
      <legend className="mb-2 text-sm font-medium text-foreground">Run it with</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option.value}
            className={cn(
              "relative flex cursor-pointer flex-col gap-0.5 rounded-md border border-border bg-background px-3 py-2.5",
              "transition-colors duration-(--dur-2) hover:bg-accent",
              "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
              "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="sr-only"
              aria-describedby={`${name}-${option.value}-hint`}
            />
            <span className="flex items-center gap-2 text-sm font-medium text-foreground">
              {option.title}
              {option.value === "inference" ? <CapabilityBadge kind="no-key" /> : null}
            </span>
            <span id={`${name}-${option.value}-hint`} className="text-xs text-muted-foreground">
              {option.hint}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function VendorList({
  name,
  kind,
  selectedId,
  selectable,
  unavailable,
  onSelect,
}: {
  name: string;
  kind: ProviderKind;
  selectedId: string | null;
  selectable: ProviderSpec[];
  unavailable: UnavailableEntry[];
  onSelect: (spec: ProviderSpec) => void;
}) {
  const legendId = `${name}-legend`;
  return (
    <div className="flex flex-col gap-2">
      <div role="radiogroup" aria-labelledby={legendId} className="flex flex-col gap-2">
        <span id={legendId} className="text-sm font-medium text-foreground">
          Vendor
        </span>
        {selectable.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground">No {kind === "llm" ? "language model" : "provider"} is available for this slot yet.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {selectable.map((spec) => (
              <VendorCard key={spec.id} name={name} spec={spec} checked={spec.id === selectedId} onSelect={() => onSelect(spec)} />
            ))}
          </div>
        )}
      </div>
      {unavailable.length > 0 ? (
        <Collapsible>
          <CollapsibleTrigger
            className={cn(
              "group/more inline-flex items-center gap-1 rounded-xs text-[0.8125rem] font-medium text-muted-foreground outline-none",
              "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <ChevronRightIcon
              className="size-3.5 transition-transform duration-(--dur-2) group-data-[state=open]/more:rotate-90"
              aria-hidden="true"
            />
            More providers ({unavailable.length})
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-3 flex flex-col gap-4">
            {groupUnavailable(unavailable).map((group) => (
              <div key={group.chip} className="flex flex-col gap-2">
                <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <StatusChip tone="neutral" size="sm">
                    {group.chip}
                  </StatusChip>
                  {group.sharedReason ? <span className="text-pretty">{group.sharedReason}</span> : null}
                </p>
                <ul className="grid gap-x-3 gap-y-1.5 sm:grid-cols-2" aria-label={`${group.chip}: providers you can't pick yet`}>
                  {group.entries.map(({ spec, reason }) => (
                    <li key={spec.id} data-provider-id={spec.id} data-unavailable="" className="flex min-w-0 items-start gap-2 py-0.5">
                      <VendorMark vendor={spec.vendor} size="sm" className="opacity-60" />
                      <span className="flex min-w-0 flex-col">
                        <span className="truncate text-[0.8125rem] leading-5 text-muted-foreground">{spec.label}</span>
                        {group.sharedReason ? null : <span className="text-xs text-muted-foreground">{reason.reason}</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}

/** Unavailable providers grouped by chip; one shared sentence per group when the reasons match. */
function groupUnavailable(entries: UnavailableEntry[]) {
  const groups = new Map<string, UnavailableEntry[]>();
  for (const entry of entries) {
    const list = groups.get(entry.reason.chip) ?? [];
    list.push(entry);
    groups.set(entry.reason.chip, list);
  }
  return Array.from(groups, ([chip, list]) => {
    const first = list[0]?.reason.reason ?? "";
    const shared = list.every((entry) => entry.reason.reason === first) ? first : null;
    return { chip, sharedReason: shared, entries: list.sort((a, b) => a.spec.label.localeCompare(b.spec.label)) };
  });
}

function VendorCard({
  name,
  spec,
  checked,
  onSelect,
}: {
  name: string;
  spec: ProviderSpec;
  checked: boolean;
  onSelect: () => void;
}) {
  const caps = spec.capabilities ?? {};
  const voices = caps.voices?.length ?? 0;
  const thinks = THINKING_KINDS.has(spec.kind);
  const speaks = SPEAKING_KINDS.has(spec.kind);
  return (
    <div
      className={cn(
        "relative flex flex-col gap-1.5 rounded-md border border-border bg-background px-3 py-2.5",
        "transition-colors duration-(--dur-2) hover:bg-accent",
        "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
        "has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-ring has-[input:focus-visible]:ring-offset-2 has-[input:focus-visible]:ring-offset-background",
      )}
      data-provider-id={spec.id}
    >
      <label className="flex cursor-pointer items-start gap-2.5">
        <input type="radio" name={name} value={spec.id} checked={checked} onChange={onSelect} className="sr-only" />
        <VendorMark vendor={spec.vendor} size="sm" className="mt-0.5" />
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-foreground">{spec.label}</span>
            {isVerified(spec) ? (
              <StatusChip tone="success" size="sm">
                Verified
              </StatusChip>
            ) : null}
          </span>
          <span className="flex flex-wrap gap-1">
            {thinks && caps.video_input ? <CapabilityBadge kind="vision" /> : null}
            {thinks && caps.tool_calling ? <CapabilityBadge kind="tools" /> : null}
            {thinks && caps.silent_tool_reply ? <CapabilityBadge kind="silent-tools" /> : null}
            {speaks && voices > 0 ? <CapabilityBadge kind="voices" count={voices} /> : null}
            {spec.requires_credential === false ? <CapabilityBadge kind="no-key" /> : null}
          </span>
        </span>
      </label>
      {spec.docs_url || spec.get_key_url ? (
        <span className="flex gap-3 pl-[1.875rem] text-xs">
          {spec.docs_url ? <VendorLink href={spec.docs_url}>Docs</VendorLink> : null}
          {spec.get_key_url ? <VendorLink href={spec.get_key_url}>Get a key</VendorLink> : null}
        </span>
      ) : null}
    </div>
  );
}

function VendorLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="relative inline-flex items-center gap-0.5 rounded-xs font-medium text-muted-foreground underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
      <ExternalLinkIcon className="size-3" aria-hidden="true" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
