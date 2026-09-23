"use client";

import * as React from "react";
import { PencilIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { ModelSummary } from "@/components/console/registry/model-combobox";
import { effectiveModelId, findModel, isInferenceProvider } from "@/components/console/registry/provider-meta";
import {
  ProviderSlotEditor,
  SPEAKING_KINDS,
  THINKING_KINDS,
  useSlotProviders,
  type ProviderSlotEditorProps,
} from "@/components/console/registry/provider-slot-editor";
import { cn } from "@/lib/utils";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

export interface ProviderSlotCardProps extends ProviderSlotEditorProps {
  /** Card title, sentence case ("Speech-to-text"). */
  title: string;
  /** One line under the title. */
  description?: string;
  /** One card is open at a time; the parent owns which. */
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  /** Optional slots: "Remove" sets the slot back to null (also dismisses an added-but-empty slot). */
  onRemove?: () => void;
  /** Field-level problem for this slot (zod message or a mapped api issue). */
  error?: string;
  /** Severity of `error` (default "error"). */
  errorTone?: "error" | "warning";
  /** Inline note under the summary (e.g. the vision coherence warning). */
  notice?: React.ReactNode;
  /** Config-relative path (`pipeline.stt`) — lets "Show field" focus this card. */
  issuePath?: string;
}

/**
 * A pipeline slot as a card (docs/UI_UX_SPEC.md §4.4 item 2): collapsed, it
 * shows `VendorMark`, provider label, the model's human label (id in mono
 * beneath), `CapabilityBadge`s and the key status; "Edit" expands the
 * `ProviderSlotEditor` inline. The editor contract (`kind`, `value`,
 * `onChange`, `constraints`) passes straight through.
 */
export function ProviderSlotCard({
  title,
  description,
  expanded,
  onExpandedChange,
  onRemove,
  error,
  errorTone = "error",
  notice,
  issuePath,
  ...editorProps
}: ProviderSlotCardProps) {
  const { kind, value, providers: providersProp, idPrefix } = editorProps;
  const providers = useSlotProviders(providersProp);
  const autoId = React.useId();
  const bodyId = `${idPrefix ?? `slot-${kind}-${autoId}`}-body`;
  const titleId = `${bodyId}-title`;

  return (
    <Collapsible open={expanded} onOpenChange={onExpandedChange} asChild>
      <section
        aria-labelledby={titleId}
        data-slot="provider-slot-card"
        data-kind={kind}
        data-issue-path={issuePath}
        data-state={expanded ? "open" : "closed"}
        className={cn(
          "rounded-lg border bg-card",
          error && errorTone === "error" ? "border-danger/60" : "border-border",
        )}
      >
        <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex flex-col gap-0.5">
              <h3 id={titleId} className="text-sm font-semibold text-foreground">
                {title}
              </h3>
              {description ? <p className="text-xs text-pretty text-muted-foreground">{description}</p> : null}
            </div>
            <ProviderSlotSummary value={value} providers={providers} kind={kind} />
          </div>
          <div className="flex shrink-0 items-center gap-1 self-start">
            {onRemove ? (
              <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
                <XIcon aria-hidden="true" />
                Remove
              </Button>
            ) : null}
            <Button
              type="button"
              variant={expanded ? "secondary" : "outline"}
              size="sm"
              aria-expanded={expanded}
              aria-controls={bodyId}
              onClick={() => onExpandedChange(!expanded)}
            >
              {expanded ? null : <PencilIcon aria-hidden="true" />}
              {expanded ? "Done" : "Edit"}
              <span className="sr-only"> {title.toLowerCase()}</span>
            </Button>
          </div>
        </div>
        {error ? (
          <p
            className={cn(
              "mx-4 mb-3 -mt-1 text-[0.8125rem] leading-[1.125rem]",
              errorTone === "error" ? "text-danger-text" : "text-warning-text",
            )}
          >
            {error}
          </p>
        ) : null}
        {notice ? <div className="mx-4 mb-4">{notice}</div> : null}
        <CollapsibleContent id={bodyId} className="border-t border-border px-4 py-5 sm:px-5">
          <ProviderSlotEditor {...editorProps} />
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

/**
 * The collapsed view of a slot: who runs it, which model, what it can do,
 * and whether a key is missing. Exported for reuse (summary rail, V2-13).
 */
export function ProviderSlotSummary({
  value,
  providers,
  kind,
}: {
  value: ProviderRef | null | undefined;
  providers: ProviderSpec[];
  kind: ProviderSpec["kind"];
}) {
  const spec = value ? providers.find((p) => p.id === value.provider_id) : undefined;
  if (!value) {
    return <p className="text-[0.8125rem] text-muted-foreground">Not set</p>;
  }
  if (!spec) {
    return (
      <p className="text-[0.8125rem] text-muted-foreground">
        <span className="font-mono">{value.provider_id}</span>
        {providers.length > 0 ? " · not in the registry" : null}
      </p>
    );
  }

  const modelId = effectiveModelId(spec, value.model);
  const model = findModel(spec, modelId);
  const caps = spec.capabilities ?? {};
  const voices = caps.voices?.length ?? 0;
  const thinks = THINKING_KINDS.has(kind);
  const vision = thinks && (model ? model.supports_video === true : kind === "realtime" && caps.video_input === true);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2.5">
        <VendorMark vendor={spec.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col">
          <span className="text-sm font-medium text-foreground">{spec.label}</span>
          {modelId ? <ModelSummary model={model} modelId={modelId} isDefault={!value.model && Boolean(spec.default_model)} /> : null}
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        {vision ? <CapabilityBadge kind="vision" /> : null}
        {kind === "realtime" ? <CapabilityBadge kind="realtime" /> : null}
        {thinks && caps.tool_calling ? <CapabilityBadge kind="tools" /> : null}
        {thinks && caps.silent_tool_reply ? <CapabilityBadge kind="silent-tools" /> : null}
        {SPEAKING_KINDS.has(kind) && voices > 0 ? <CapabilityBadge kind="voices" count={voices} /> : null}
        <KeyStatus spec={spec} credentialId={value.credential_id ?? null} />
      </div>
    </div>
  );
}

function KeyStatus({ spec, credentialId }: { spec: ProviderSpec; credentialId: string | null }) {
  const needsKey = spec.requires_credential !== false && !isInferenceProvider(spec);
  // One shared "all credentials" query for every card (and the credentials page).
  const { data } = useCredentials();
  if (!needsKey) return <CapabilityBadge kind="no-key" />;
  if (!credentialId) return <CapabilityBadge kind="key-required" />;
  const credential = data?.items.find((item) => item.id === credentialId);
  return (
    <CapabilityBadge kind="key-set">
      {credential ? `${credential.label} · ${credential.fingerprint}` : "Key set"}
    </CapabilityBadge>
  );
}
