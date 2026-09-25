"use client";

import * as React from "react";
import { ChevronRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Icon } from "@/components/shared/icon";
import { formatUsd, useCostAssumptions } from "@/components/console/lib/cost-hooks";
import { useWriteAccess } from "@/components/console/lib/roles";
import {
  WorkspacePricesDialog,
  type WorkspacePricePrefill,
} from "@/components/console/settings/workspace-prices-dialog";
import type { Assumption, EstimateLine } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import { useDraftCostEstimate } from "./editor-context";

/**
 * The Cost estimate dialog (docs/v4/COSTS.md §5 item 1, R-V4-50): everything
 * here is an **estimate at list prices, never a bill** — the breakdown, the
 * editable assumptions, "Use my workspace's averages", the unpriced list with
 * "Set a price", and the footer. Plain labels only: `EstimateLine.label` and
 * `Assumption.label` already come from the api in plain language (D-V4-47),
 * so this file never needs its own slot/unit vocabulary — the one place a
 * technical unit is shown at all is `TechnicalUnitDisclosure`, behind its own
 * expandable control, well away from the summary sentences above it.
 */

export interface CostEstimateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function bandText(low: string, mid: string, high: string): string {
  return `≈ ${mid}/min · estimate — typically ${low}–${high}`;
}

export function CostEstimateDialog({ open, onOpenChange }: CostEstimateDialogProps) {
  const { estimate, isLoading, settings, setSettings } = useDraftCostEstimate();
  const assumptionsQuery = useCostAssumptions();
  const { canWrite: isAdmin } = useWriteAccess("admin");
  const [pricesOpen, setPricesOpen] = React.useState(false);
  const [pricesPrefill, setPricesPrefill] = React.useState<WorkspacePricePrefill | null>(null);

  function openPricesFor(line: EstimateLine) {
    setPricesPrefill({ provider_id: line.provider_id, model: line.model ?? null, unit: line.unit });
    setPricesOpen(true);
  }

  const perMinute = estimate?.per_minute_usd;
  const perSession = estimate?.per_session_usd;
  const unpricedLines = (estimate?.lines ?? []).filter((line) => line.usd_per_min == null && line.usd_per_session == null);
  const sessionsSampled = assumptionsQuery.data?.sessions_sampled ?? 0;
  const canUseAverages = sessionsSampled >= 10;

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Cost estimate</DialogTitle>
            <DialogDescription>
              What this agent is estimated to cost, at list prices — an estimate, not a bill.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-6">
            <section aria-label="Estimated cost" className="flex flex-col gap-1 rounded-lg border border-border bg-muted/30 p-4">
              {isLoading && !estimate ? (
                <p className="text-sm text-muted-foreground">Estimating…</p>
              ) : perMinute ? (
                <>
                  <p className="text-lg font-semibold text-foreground">
                    {bandText(
                      formatUsd(perMinute.low) ?? "—",
                      formatUsd(perMinute.mid) ?? "—",
                      formatUsd(perMinute.high) ?? "—",
                    )}
                  </p>
                  {perSession ? (
                    <p className="text-sm text-muted-foreground">
                      ≈ {formatUsd(perSession.mid) ?? "—"} for a {estimate?.session_minutes ?? "—"}-minute call · estimate
                    </p>
                  ) : null}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">Nothing here is priced yet — add a price below to see a figure.</p>
              )}
            </section>

            <section aria-label="Assumptions" className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">Assumptions</h3>
                <label className="flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
                  <Switch
                    checked={settings.workspaceAverages}
                    disabled={!canUseAverages}
                    onCheckedChange={(checked) => setSettings({ workspaceAverages: checked })}
                  />
                  Use my workspace&apos;s averages
                </label>
              </div>
              {!canUseAverages ? (
                <p className="text-xs text-muted-foreground">
                  Needs 10 ended sessions with usage — this workspace has {sessionsSampled}.
                </p>
              ) : settings.workspaceAverages ? (
                <p className="text-xs text-muted-foreground">
                  Using this workspace&apos;s own averages from its last 30 days of sessions.
                </p>
              ) : null}

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="cost-estimate-channel" className="text-xs font-medium text-muted-foreground">
                    Call type
                  </Label>
                  <Select value={settings.channel} onValueChange={(value) => setSettings({ channel: value as typeof settings.channel })}>
                    <SelectTrigger id="cost-estimate-channel" className="h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="web">Web call</SelectItem>
                      <SelectItem value="phone">Phone call</SelectItem>
                      <SelectItem value="text">Text chat</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {(estimate?.assumptions ?? [])
                  .filter((assumption): assumption is Assumption & { value: number } => typeof assumption.value === "number")
                  .map((assumption) => (
                    <AssumptionField
                      key={assumption.key}
                      assumption={assumption}
                      value={settings.assumptions[assumption.key]}
                      onChange={(value) => setSettings({ assumptions: { ...settings.assumptions, [assumption.key]: value } })}
                    />
                  ))}
              </div>
            </section>

            <section aria-label="Breakdown" className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold text-foreground">Breakdown</h3>
              <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
                {(estimate?.lines ?? []).map((line, index) => (
                  <BreakdownRow key={`${line.slot}-${index}`} line={line} />
                ))}
                {(estimate?.lines ?? []).length === 0 ? (
                  <li className="p-4 text-sm text-muted-foreground">Nothing to break down yet.</li>
                ) : null}
              </ul>
            </section>

            {unpricedLines.length > 0 ? (
              <section aria-label="Not included" className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">Not included (no published price)</h3>
                <ul className="flex flex-col gap-1.5">
                  {unpricedLines.map((line, index) => (
                    <li key={`${line.slot}-${index}`} className="flex items-center justify-between gap-2 text-[0.8125rem]">
                      <span className="text-muted-foreground">
                        {line.label} — {line.provider_id}
                        {line.model ? ` ${line.model}` : ""}
                      </span>
                      {isAdmin ? (
                        <button
                          type="button"
                          onClick={() => openPricesFor(line)}
                          className="shrink-0 rounded-xs font-medium text-brand-text underline underline-offset-2 outline-none hover:no-underline focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          Set a price
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <p className="text-xs text-pretty text-muted-foreground">
              List prices at the entry tier as of {estimate?.as_of ?? "today"}; OpenRouter prices live; your own
              prices where set. Estimates are not bills.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <WorkspacePricesDialog open={pricesOpen} onOpenChange={setPricesOpen} prefill={pricesPrefill} />
    </>
  );
}

function AssumptionField({
  assumption,
  value,
  onChange,
}: {
  assumption: Assumption & { value: number };
  value: number | string | undefined;
  onChange: (value: number) => void;
}) {
  const id = `cost-estimate-assumption-${assumption.key}`;
  const [text, setText] = React.useState(String(value ?? assumption.value));
  const timer = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  React.useEffect(() => {
    setText(String(value ?? assumption.value));
    // Only re-sync from the source when the *identity* of what drives it changes,
    // not on every keystroke this field itself just made.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assumption.key]);

  function commit(next: string) {
    setText(next);
    const parsed = Number(next);
    if (!Number.isFinite(parsed)) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(parsed), 400);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {assumption.label}
      </Label>
      <Input
        id={id}
        inputMode="decimal"
        value={text}
        onChange={(event) => commit(event.target.value)}
        className="h-9"
      />
    </div>
  );
}

function BreakdownRow({ line }: { line: EstimateLine }) {
  const priced = line.usd_per_min != null || line.usd_per_session != null;
  return (
    <li className="flex flex-col gap-1 p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate text-sm font-medium text-foreground">{line.label}</span>
        <span className="shrink-0 font-mono text-sm tabular-nums text-foreground">
          {priced
            ? line.usd_per_min != null
              ? `${formatUsd(line.usd_per_min)}/min`
              : formatUsd(line.usd_per_session)
            : (line.note ?? "no price")}
        </span>
      </div>
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>{line.provider_id}{line.model ? ` · ${line.model}` : ""}</span>
        {line.quote ? (
          <span>
            {line.quote.source === "workspace" ? "Your price" : line.quote.source === "live" ? "Live price" : "List price"}, as
            of {line.quote.as_of}
          </span>
        ) : null}
      </div>
      <TechnicalUnitDisclosure line={line} />
    </li>
  );
}

/**
 * The one place in this dialog a technical unit is spelled out (R-V4-50,
 * D-V4-47: the vendor-facing vocabulary stays inside this one expandable
 * control) — a quiet "Details" toggle per breakdown row, off by default.
 */
function TechnicalUnitDisclosure({ line }: { line: EstimateLine }) {
  // Plain-English words for the priced units — kept local to this one disclosure (R-V4-50, D-V4-47).
  const unitWords: Record<EstimateLine["unit"], string> = {
    tokens_in: "language-model tokens in",
    tokens_out: "language-model tokens out",
    text_tokens_in: "text tokens in",
    text_tokens_out: "text tokens out",
    audio_tokens_in: "audio tokens in",
    audio_tokens_out: "audio tokens out",
    cached_tokens_in: "cached tokens in",
    audio_s_in: "seconds of audio in",
    audio_s_out: "seconds of audio out",
    chars: "characters",
    minutes: "minutes",
    images: "images",
    requests: "requests",
  };
  const [open, setOpen] = React.useState(false);
  const quantity = line.quantity_per_min ?? line.quantity_per_session ?? null;
  const unitWord = unitWords[line.unit] ?? line.unit;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex w-fit items-center gap-1 text-xs text-muted-foreground underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon as={ChevronRightIcon} size="sm" className={cn("transition-transform", open && "rotate-90")} />
          Details
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <p className="mt-1 text-xs text-muted-foreground">
          {quantity != null ? `${quantity} ${unitWord}${line.quantity_per_min != null ? " per minute" : " per call"}` : "No quantity yet."}
          {line.quote ? ` · $${line.quote.usd_per_unit} per ${unitWord.replace(/s$/, "")}` : null}
        </p>
      </CollapsibleContent>
    </Collapsible>
  );
}
