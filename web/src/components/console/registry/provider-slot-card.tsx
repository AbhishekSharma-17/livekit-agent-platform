"use client";

import * as React from "react";
import { PencilIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { displayMessage } from "@/components/console/agents/editor/validation-map";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { catalogSaysVision } from "@/components/console/registry/model-capabilities";
import { isCustomModelId, ModelSummary, useModelCatalog } from "@/components/console/registry/model-combobox";
import { TestedChip, useTestedState } from "@/components/console/registry/model-test-panel";
import { findModel, isInferenceProvider } from "@/components/console/registry/provider-meta";
import {
  MODEL_KINDS,
  modelFieldOf,
  ProviderSlotEditor,
  slotModelId,
  slotSuggestions,
  SPEAKING_KINDS,
  THINKING_KINDS,
  useSlotProviders,
  type ProviderSlotEditorProps,
} from "@/components/console/registry/provider-slot-editor";
import { isSendableModelId } from "@/lib/model-ids";
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
  const { issueFor } = useSectionIssues();
  const fieldIssueFor = React.useCallback(
    (fieldName: string) => {
      if (!issuePath) return undefined;
      const issue = issueFor(`${issuePath}.fields.${fieldName}`);
      if (!issue) return undefined;
      return { message: displayMessage(issue), severity: issue.severity === "error" ? ("error" as const) : ("warning" as const) };
    },
    [issuePath, issueFor],
  );

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
          <ProviderSlotEditor {...editorProps} issuePath={issuePath} fieldIssueFor={fieldIssueFor} />
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

  return <SlotSummaryBody spec={spec} value={value} kind={kind} />;
}

/**
 * The collapsed card's model line (V4-09): the model's label and id, a
 * `Custom` badge for an id outside the suggestions and the vendor's list, the
 * tested chip, and a vision badge from the model's record when it is custom.
 * The record is read only for an id that passes the model-id rule (R-V4-32).
 */
function SlotSummaryBody({ spec, value, kind }: { spec: ProviderSpec; value: ProviderRef; kind: ProviderSpec["kind"] }) {
  const modelField = modelFieldOf(spec);
  const modelId = slotModelId(spec, value) || null;
  const suggestions = slotSuggestions(spec);
  const listed = modelId ? suggestions.find((m) => m.id === modelId) : undefined;
  const modelKind = MODEL_KINDS.has(spec.kind);
  const sendable = modelKind && isSendableModelId(modelId);
  // The vendor list is read only when the id is not a suggestion (it may still be a catalog id).
  const catalog = useModelCatalog(spec.id, spec.catalog, value.credential_id ?? null, { enabled: sendable && !listed });
  const catalogItem = modelId ? catalog.data?.items?.find((item) => item.id === modelId) : undefined;
  const custom = sendable && !listed && isCustomModelId(modelId as string, suggestions, catalog.data?.items ?? []);
  // One record read per unlisted (catalog or custom) id; a suggested model needs none on the collapsed card.
  const unlisted = sendable && !listed;
  const tested = useTestedState(unlisted ? spec : undefined, unlisted ? modelId : null, value.credential_id ?? null);
  const model = modelField ? listed : findModel(spec, modelId);
  const summaryModel = model ?? (catalogItem ? { id: catalogItem.id, label: catalogItem.label } : undefined);
  const caps = spec.capabilities ?? {};
  const voices = caps.voices?.length ?? 0;
  const thinks = THINKING_KINDS.has(kind);
  const recordVision =
    tested.record?.declared?.vision ?? tested.record?.detected?.vision ?? catalogSaysVision(catalogItem?.meta) ?? null;
  const vision =
    thinks &&
    (model
      ? model.supports_video === true
      : custom || catalogItem
        ? recordVision === true
        : kind === "realtime" && caps.video_input === true);
  const isDefault = modelField ? !value.fields?.[modelField.name] : !value.model && Boolean(spec.default_model);
  const showChip = unlisted && (custom || tested.state.kind === "ok" || tested.state.kind === "failed");

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2.5">
        <VendorMark vendor={spec.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col">
          <span className="text-sm font-medium text-foreground">{spec.label}</span>
          {modelId ? <ModelSummary model={summaryModel} modelId={modelId} isDefault={isDefault} /> : null}
          {custom || showChip ? (
            <span className="mt-1 flex flex-wrap items-center gap-1">
              {custom ? <CapabilityBadge kind="custom" /> : null}
              {showChip ? <TestedChip state={tested.state} className="max-w-full" /> : null}
            </span>
          ) : null}
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
