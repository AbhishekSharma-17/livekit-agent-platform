"use client";

import * as React from "react";
import { CircleAlertIcon, CircleCheckIcon, CircleHelpIcon, FlaskConicalIcon, RotateCwIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { GatedButton } from "@/components/shared/gated-button";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import { useCredentials, useProviderModel, useTestModel } from "@/components/console/lib/api-hooks";
import { useWriteAccess } from "@/components/console/lib/roles";
import { isInferenceProvider } from "@/components/console/registry/provider-meta";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ApiError } from "@/lib/api";
import { isSendableModelId } from "@/lib/model-ids";
import { cn } from "@/lib/utils";
import type { ModelCapabilities, ModelTestResult, ProviderModelOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * "Test model" in the console (docs/v4/CUSTOM-MODELS.md D-V4-26 "Shown as",
 * V4-09): the tested chip, the Test button and the inline result panel.
 *
 * The chip lives here rather than in `CapabilityBadge` — it needs a danger
 * tone and a relative time, which the badge's three-tone metadata does not
 * carry — so it is a `StatusChip` (V4-09's choice).
 *
 * Everything a vendor sends back (`message`, `sample`, probe messages) is
 * untrusted data: plain text in a mono box, never markdown or HTML.
 */

/** A record counts as "tested" for this long (`TESTED_WINDOW_S`, 30 days). */
export const TESTED_WINDOW_MS = 30 * 24 * 60 * 60 * 1000;

export type TestedState =
  | { kind: "no-probe" }
  | { kind: "untested" }
  | { kind: "ok"; at: string }
  | { kind: "failed"; reason: string };

/**
 * The chip's state for one model record (D-V4-26): a test counts only while
 * it is younger than 30 days **and** was run with the key the slot uses now
 * (a rotated key has a new fingerprint and resets the chip to Untested).
 * Slots without a key (LiveKit Inference) accept any fingerprint.
 */
export function testedStateFor({
  spec,
  record,
  fingerprint,
  now = Date.now(),
}: {
  spec: Pick<ProviderSpec, "probe">;
  record: ProviderModelOut | null | undefined;
  /** The fingerprint of the key the slot uses; `null` for a key-less slot; `undefined` while unknown. */
  fingerprint: string | null | undefined;
  now?: number;
}): TestedState {
  if (!spec.probe) return { kind: "no-probe" };
  if (!record || !record.last_test_at || record.last_test_ok === null || record.last_test_ok === undefined) {
    return { kind: "untested" };
  }
  if (fingerprint && record.last_test_fingerprint && record.last_test_fingerprint !== fingerprint) {
    return { kind: "untested" };
  }
  if (record.last_test_ok === false) {
    return { kind: "failed", reason: record.last_test_message || "the vendor refused the model" };
  }
  const at = Date.parse(record.last_test_at);
  if (Number.isNaN(at) || now - at > TESTED_WINDOW_MS) return { kind: "untested" };
  return { kind: "ok", at: record.last_test_at };
}

/** A fresh result, as the record the chip reads (so the chip updates before the refetch lands). */
export function recordFromResult(result: ModelTestResult, fingerprint: string | null | undefined): ProviderModelOut {
  return {
    id: result.record_id ?? "",
    provider_id: result.provider_id,
    provider_home: result.provider_id,
    kind: result.kind,
    model_id: result.model,
    created_at: result.checked_at,
    updated_at: result.checked_at,
    last_test_at: result.checked_at,
    last_test_ok: result.ok ?? null,
    last_test_message: result.message ?? null,
    last_test_latency_ms: result.latency_ms ?? null,
    last_test_fingerprint: fingerprint ?? null,
    detected: result.detected ?? null,
  };
}

export function TestedChip({ state, className }: { state: TestedState; className?: string }) {
  switch (state.kind) {
    case "ok":
      return (
        <StatusChip tone="success" size="sm" className={className}>
          <span data-testid="tested-chip">
            Tested ✓ <RelativeTime iso={state.at} />
          </span>
        </StatusChip>
      );
    case "failed":
      return (
        <StatusChip tone="danger" size="sm" className={cn("max-w-full min-w-0", className)}>
          <span data-testid="tested-chip" className="truncate" title={`Test failed · ${state.reason}`}>
            Test failed · {state.reason}
          </span>
        </StatusChip>
      );
    case "no-probe":
      return (
        <StatusChip tone="neutral" size="sm" className={className}>
          <span data-testid="tested-chip">No test for this provider</span>
        </StatusChip>
      );
    default:
      return (
        <StatusChip tone="neutral" size="sm" className={className}>
          <span data-testid="tested-chip">Untested</span>
        </StatusChip>
      );
  }
}

/**
 * The record and chip state for one slot's model. The record is read only
 * when the id passes the model-id rule (R-V4-32); until then the state is
 * Untested and nothing is requested.
 */
export function useTestedState(
  spec: ProviderSpec | undefined,
  modelId: string | null | undefined,
  credentialId: string | null,
  { readRecord = true }: { readRecord?: boolean } = {},
) {
  const sendable = isSendableModelId(modelId);
  const record = useProviderModel(spec?.id, modelId, { enabled: readRecord });
  const credentials = useCredentials();
  const needsKey = spec ? spec.requires_credential !== false && !isInferenceProvider(spec) : false;
  const fingerprint = !needsKey
    ? null
    : credentialId
      ? credentials.data?.items.find((item) => item.id === credentialId)?.fingerprint
      : undefined;
  const state = spec ? testedStateFor({ spec, record: sendable ? record.data : null, fingerprint }) : ({ kind: "untested" } as const);
  return { state, record: sendable ? (record.data ?? null) : null, fingerprint };
}

/** "Too many tests right now — try again in 12 s." and friends; never repeats the model id. */
export function testErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      const details = (error.details ?? {}) as { retry_after_s?: number; retry_after?: number };
      const retry = details.retry_after_s ?? details.retry_after;
      const seconds = typeof retry === "number" && Number.isFinite(retry) ? Math.max(1, Math.ceil(retry)) : null;
      return seconds
        ? `Too many tests right now — try again in ${seconds} s.`
        : "Too many tests right now — try again in a minute.";
    }
    if (error.status === 403) return "Testing a model needs the builder role or higher.";
    if (error.status === 422) return `Couldn't run the test: ${error.message}`;
  }
  return `Couldn't run the test: ${errorMessage(error)}`;
}

export interface ModelTestControlsProps {
  spec: ProviderSpec;
  /** The effective model id (the typed id, or the provider default). */
  modelId: string;
  credentialId: string | null;
  /** The slot's option fields (a TTS voice, a `base_url`), sent with the test. */
  fields?: Record<string, string | number | boolean>;
  /** Show the chip even with no record ("Untested"); true for a custom id. */
  showUntested?: boolean;
  /** Called with the latest result (the capabilities block reads `detected`). */
  onResult?: (result: ModelTestResult) => void;
  /** Read the stored record for the chip (default true). Off for a suggested model: no lookup until a test runs. */
  readRecord?: boolean;
  className?: string;
}

const PROBES: ("basic" | "tools")[] = ["basic", "tools"];

/**
 * The tested chip, the Test button (builders and admins) and, after a run,
 * the inline result panel. Renders nothing for an id that fails the rule —
 * the field shows that error, and nothing may be sent (R-V4-32).
 */
export function ModelTestControls({
  spec,
  modelId,
  credentialId,
  fields,
  showUntested = false,
  onResult,
  readRecord = true,
  className,
}: ModelTestControlsProps) {
  const { state, fingerprint } = useTestedState(spec, modelId, credentialId, { readRecord });
  const { canWrite } = useWriteAccess("builder");
  const test = useTestModel(spec.id);
  const [result, setResult] = React.useState<ModelTestResult | null>(null);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const panelId = React.useId();

  // A different model or key makes the last panel stale.
  React.useEffect(() => {
    setResult(null);
    setPanelOpen(false);
    test.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the subject changes
  }, [spec.id, modelId, credentialId]);

  if (!isSendableModelId(modelId)) return null;

  const shownState: TestedState =
    result && result.model === modelId ? testedStateFor({ spec, record: recordFromResult(result, fingerprint), fingerprint }) : state;
  const showChip = shownState.kind !== "untested" || showUntested;
  const needsKey = spec.requires_credential !== false && !isInferenceProvider(spec);
  const missingKey = needsKey && !credentialId;

  function run(force = false) {
    setPanelOpen(true);
    test.mutate(
      {
        model: modelId,
        credential_id: credentialId,
        fields: fields ?? {},
        probes: PROBES,
        ...(force ? { force: true } : {}),
      },
      {
        onSuccess: (next) => {
          setResult(next);
          onResult?.(next);
        },
      },
    );
  }

  return (
    <div className={cn("flex flex-col gap-2", className)} data-slot="model-test">
      <div className="flex flex-wrap items-center gap-2">
        {showChip ? <TestedChip state={shownState} /> : null}
        {spec.probe ? (
          <GatedButton
            type="button"
            variant="outline"
            size="sm"
            allowed={canWrite}
            reason="Testing a model needs the builder role or higher."
            disabled={test.isPending || missingKey}
            aria-expanded={panelOpen}
            aria-controls={panelOpen ? panelId : undefined}
            onClick={() => run(false)}
          >
            <FlaskConicalIcon aria-hidden="true" />
            {test.isPending ? "Testing…" : result ? "Test again" : "Test model"}
          </GatedButton>
        ) : null}
        {spec.probe && missingKey ? (
          <span className="text-xs text-muted-foreground">Choose a key to test this model.</span>
        ) : null}
      </div>
      {panelOpen ? (
        <div id={panelId} role="region" aria-label="Test result" aria-live="polite" aria-busy={test.isPending}>
          {test.isPending ? (
            <p className="rounded-md border border-border bg-muted/40 px-3 py-2.5 text-[0.8125rem] text-muted-foreground">
              Calling the vendor once with this model…
            </p>
          ) : test.isError ? (
            <PanelFrame tone="danger" onClose={() => setPanelOpen(false)}>
              <p className="text-[0.8125rem] text-danger-text">{testErrorMessage(test.error)}</p>
            </PanelFrame>
          ) : result ? (
            <ModelTestPanel result={result} onRunAgain={() => run(true)} onClose={() => setPanelOpen(false)} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function PanelFrame({
  tone,
  onClose,
  children,
}: {
  tone: "success" | "danger" | "neutral";
  onClose?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      data-slot="model-test-panel"
      data-tone={tone}
      className={cn(
        "relative flex min-w-0 flex-col gap-3 rounded-md border px-3 py-3 pr-10 sm:px-4",
        tone === "success" ? "border-success/40" : tone === "danger" ? "border-danger/40" : "border-border",
        "bg-card",
      )}
    >
      {children}
      {onClose ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute top-1.5 right-1.5"
          aria-label="Close the test result"
          onClick={onClose}
        >
          <XIcon aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}

const PROBE_LABEL: Record<string, string> = {
  basic: "Reply",
  tools: "Tool call",
  vision: "Image input",
};

const CAPABILITY_LABEL: { key: keyof ModelCapabilities; label: string }[] = [
  { key: "vision", label: "Sees images" },
  { key: "tools", label: "Calls tools" },
  { key: "audio_in", label: "Hears audio" },
  { key: "audio_out", label: "Speaks audio" },
  { key: "streaming", label: "Streams" },
];

/** "About $0.0012 for this test." / "Less than $0.0001 for this test." or `null` without a price. */
export function costSentence(value: number | string | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return null;
  if (amount === 0) return "This test cost nothing.";
  if (amount < 0.0001) return "Less than $0.0001 for this test.";
  return `About $${amount.toFixed(amount < 0.01 ? 4 : 2)} for this test.`;
}

/** The inline result of one Test model run (D-V4-26: latency, probes, capabilities, cost, sample, caveat). */
export function ModelTestPanel({
  result,
  onRunAgain,
  onClose,
}: {
  result: ModelTestResult;
  onRunAgain?: () => void;
  onClose?: () => void;
}) {
  const tone = result.ok === true ? "success" : result.ok === false ? "danger" : "neutral";
  const title =
    result.ok === true ? "The vendor accepted this model" : result.ok === false ? "The vendor refused this model" : "Couldn't verify this model";
  const probes = result.probes ?? [];
  const detected = result.detected ?? {};
  const known = CAPABILITY_LABEL.filter(({ key }) => typeof detected[key] === "boolean");
  const costLine = costSentence(result.cost_estimate_usd) ?? (result.cost_note || "No price on file for this model.");

  return (
    <PanelFrame tone={tone} onClose={onClose}>
      <div className="flex items-start gap-2">
        <Icon
          as={result.ok === true ? CircleCheckIcon : result.ok === false ? CircleAlertIcon : CircleHelpIcon}
          size="sm"
          className={cn(
            "mt-0.5 shrink-0",
            result.ok === true ? "text-success-text" : result.ok === false ? "text-danger-text" : "text-muted-foreground",
          )}
        />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-sm font-medium text-foreground">{title}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
            {typeof result.latency_ms === "number" ? (
              <span className="tabular-nums" data-testid="test-latency">
                {result.latency_ms} ms
              </span>
            ) : null}
            {result.cached ? (
              <span>
                · From a test <RelativeTime iso={result.checked_at} />
              </span>
            ) : null}
          </p>
        </div>
      </div>

      {result.message ? <UntrustedBox label="What the vendor said">{result.message}</UntrustedBox> : null}

      {probes.length > 0 ? (
        <ul className="flex flex-col divide-y divide-border rounded-md border border-border" aria-label="Checks">
          {probes.map((probe) => (
            <li key={probe.name} className="flex min-w-0 flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3">
              <span className="flex shrink-0 items-center gap-2 sm:w-40">
                <StatusChip tone={probe.ok === true ? "success" : probe.ok === false ? "danger" : "neutral"} size="sm">
                  {probe.ok === true ? "Passed" : probe.ok === false ? "Failed" : "Inconclusive"}
                </StatusChip>
                <span className="text-[0.8125rem] text-foreground">{PROBE_LABEL[probe.name] ?? probe.name}</span>
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-xs text-muted-foreground">
                {typeof probe.latency_ms === "number" ? <span className="tabular-nums">{probe.latency_ms} ms</span> : null}
                {probe.message ? <span className="font-mono break-words whitespace-pre-wrap">{probe.message}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {known.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs font-medium text-muted-foreground">What the test found</p>
          <ul className="flex flex-wrap gap-1.5">
            {known.map(({ key, label }) => (
              <li key={key}>
                <StatusChip tone={detected[key] ? "success" : "neutral"} size="sm">
                  {detected[key] ? label : `${label}: no`}
                </StatusChip>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.sample ? <UntrustedBox label="What the model said">{result.sample}</UntrustedBox> : null}

      <p className="text-xs text-muted-foreground" data-testid="test-cost">
        {costLine}
      </p>
      <p className="text-xs text-pretty text-muted-foreground" data-testid="test-caveat">
        A pass proves the vendor accepts this model with this key, not that the agent can load it; the first call checks that.
      </p>
      {result.cached && onRunAgain ? (
        <Button type="button" variant="outline" size="sm" className="self-start" onClick={onRunAgain}>
          <RotateCwIcon aria-hidden="true" />
          Run it again now
        </Button>
      ) : null}
    </PanelFrame>
  );
}

/** Vendor text, shown as data: a mono, wrapping box that never interprets markup. */
function UntrustedBox({ label, children }: { label: string; children: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <pre
        data-slot="untrusted-text"
        tabIndex={0}
        aria-label={label}
        className="max-h-32 overflow-auto rounded-sm border border-border bg-muted/50 px-2.5 py-2 font-mono text-xs break-words whitespace-pre-wrap text-foreground"
      >
        {children}
      </pre>
    </div>
  );
}
