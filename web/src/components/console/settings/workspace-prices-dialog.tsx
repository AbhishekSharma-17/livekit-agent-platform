"use client";

import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { GatedButton } from "@/components/shared/gated-button";
import { Icon } from "@/components/shared/icon";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { useUpdateWorkspacePrices, useWorkspacePrices } from "@/components/console/lib/cost-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { WorkspacePrice } from "@/contracts/lkap-contracts";

/**
 * "Your prices" (docs/v4/COSTS.md §5 item 1, `settings/workspace-prices-dialog.tsx`):
 * admin-only, a table of provider · model · unit · USD · note, opened from
 * the Cost estimate dialog's unpriced list ("Set a price", with a
 * `prefill` row) and from the Providers page's own "Your prices" button
 * (`WorkspacePricesButton`, below). `PUT /v1/workspace/prices` replaces the
 * **whole** stored list (the route's own contract), so a save always sends
 * every row — untouched, edited and new — never a subset.
 */

type Unit = WorkspacePrice["unit"];

const UNIT_OPTIONS: { value: Unit; label: string }[] = [
  { value: "minutes", label: "minutes" },
  { value: "requests", label: "requests" },
  { value: "chars", label: "characters" },
  { value: "images", label: "images" },
  { value: "tokens_in", label: "tokens in" },
  { value: "tokens_out", label: "tokens out" },
  { value: "cached_tokens_in", label: "cached tokens in" },
  { value: "text_tokens_in", label: "text tokens in" },
  { value: "text_tokens_out", label: "text tokens out" },
  { value: "audio_tokens_in", label: "audio tokens in" },
  { value: "audio_tokens_out", label: "audio tokens out" },
  { value: "audio_s_in", label: "audio seconds in" },
  { value: "audio_s_out", label: "audio seconds out" },
];

/** A row being edited: `usd_per_unit` stays text so an empty/partial number doesn't get coerced mid-typing. */
interface DraftRow {
  key: string;
  provider_id: string;
  model: string;
  unit: Unit;
  usdPerUnit: string;
  note: string;
  as_of: string;
}

export interface WorkspacePricePrefill {
  provider_id: string;
  model?: string | null;
  unit: Unit;
}

let rowSeq = 0;
function nextKey(): string {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function toDraft(price: WorkspacePrice): DraftRow {
  return {
    key: nextKey(),
    provider_id: price.provider_id,
    model: price.model ?? "",
    unit: price.unit,
    usdPerUnit: String(price.usd_per_unit),
    note: price.note ?? "",
    as_of: price.as_of,
  };
}

function blankDraft(prefill?: WorkspacePricePrefill | null): DraftRow {
  return {
    key: nextKey(),
    provider_id: prefill?.provider_id ?? "",
    model: prefill?.model ?? "",
    unit: prefill?.unit ?? "minutes",
    usdPerUnit: "",
    note: "",
    as_of: today(),
  };
}

interface RowError {
  provider_id?: string;
  model?: string;
  usdPerUnit?: string;
  note?: string;
}

function validateRow(row: DraftRow): RowError {
  const errors: RowError = {};
  const providerId = row.provider_id.trim();
  if (providerId.length === 0) errors.provider_id = "Required";
  else if (providerId.length > 64) errors.provider_id = "64 characters max";
  if (row.model.length > 128) errors.model = "128 characters max";
  if (row.note.length > 200) errors.note = "200 characters max";
  const usd = Number(row.usdPerUnit);
  if (row.usdPerUnit.trim() === "" || !Number.isFinite(usd)) errors.usdPerUnit = "Enter a USD amount";
  else if (usd < 0 || usd > 1000) errors.usdPerUnit = "Between 0 and 1000";
  return errors;
}

function toWorkspacePrice(row: DraftRow): WorkspacePrice {
  return {
    provider_id: row.provider_id.trim(),
    model: row.model.trim() || null,
    unit: row.unit,
    usd_per_unit: Number(row.usdPerUnit),
    note: row.note.trim() || null,
    as_of: row.as_of,
  };
}

export interface WorkspacePricesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A row to start a blank dialog with (from the estimate dialog's unpriced list). */
  prefill?: WorkspacePricePrefill | null;
}

export function WorkspacePricesDialog({ open, onOpenChange, prefill }: WorkspacePricesDialogProps) {
  const { data, isLoading, isError, refetch } = useWorkspacePrices();
  const update = useUpdateWorkspacePrices();
  const [rows, setRows] = React.useState<DraftRow[]>([]);
  const [errors, setErrors] = React.useState<Record<string, RowError>>({});
  const seededFor = React.useRef<string | null>(null);

  // Seed the draft once per open, from the stored prices plus an optional prefilled blank row.
  React.useEffect(() => {
    if (!open) {
      seededFor.current = null;
      return;
    }
    if (!data || seededFor.current === "seeded") return;
    seededFor.current = "seeded";
    const stored = (data.prices ?? []).map(toDraft);
    const prefillMatch = prefill
      ? stored.find((row) => row.provider_id === prefill.provider_id && (row.model || null) === (prefill.model ?? null) && row.unit === prefill.unit)
      : undefined;
    setRows(prefillMatch ? stored : prefill ? [...stored, blankDraft(prefill)] : stored);
    setErrors({});
  }, [open, data, prefill]);

  function updateRow(key: string, patch: Partial<DraftRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function removeRow(key: string) {
    setRows((current) => current.filter((row) => row.key !== key));
    setErrors((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function addRow() {
    setRows((current) => [...current, blankDraft()]);
  }

  async function onSave(event: React.FormEvent) {
    event.preventDefault();
    const nextErrors: Record<string, RowError> = {};
    for (const row of rows) {
      const rowErrors = validateRow(row);
      if (Object.keys(rowErrors).length > 0) nextErrors[row.key] = rowErrors;
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    try {
      await update.mutateAsync(rows.map(toWorkspacePrice));
      toast.success("Your prices were saved");
      onOpenChange(false);
    } catch (err) {
      toast.error(`Couldn't save your prices — ${errorMessage(err)}`);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Your prices</DialogTitle>
          <DialogDescription>
            Prices you enter here are used by every estimate and cost line before OpenRouter&apos;s live sheet and
            the list-price table — for vendors billed by plan, where no published per-unit price exists.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading your prices…</p>
          ) : isError ? (
            <p className="text-sm text-danger-text">
              Couldn&apos;t load your prices.{" "}
              <button type="button" className="underline underline-offset-2" onClick={() => refetch()}>
                Try again
              </button>
            </p>
          ) : (
            <form id="workspace-prices-form" onSubmit={onSave} className="flex flex-col gap-3">
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full min-w-[640px] border-collapse text-[0.8125rem]">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-left text-xs font-medium text-muted-foreground">
                      <th className="px-2 py-2">Provider</th>
                      <th className="px-2 py-2">Model</th>
                      <th className="px-2 py-2">Unit</th>
                      <th className="px-2 py-2">USD per unit</th>
                      <th className="px-2 py-2">Note</th>
                      <th className="px-2 py-2">As of</th>
                      <th className="px-2 py-2">
                        <span className="sr-only">Remove</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-2 py-4 text-center text-muted-foreground">
                          No prices set yet.
                        </td>
                      </tr>
                    ) : (
                      rows.map((row) => {
                        const rowError = errors[row.key];
                        return (
                          <tr key={row.key} className="border-b border-border last:border-0 align-top">
                            <td className="px-2 py-2">
                              <Input
                                aria-label="Provider id"
                                value={row.provider_id}
                                onChange={(e) => updateRow(row.key, { provider_id: e.target.value })}
                                placeholder="bey-avatar"
                                className="h-8 font-mono text-xs"
                                aria-invalid={Boolean(rowError?.provider_id)}
                              />
                              {rowError?.provider_id ? <p className="mt-1 text-xs text-danger-text">{rowError.provider_id}</p> : null}
                            </td>
                            <td className="px-2 py-2">
                              <Input
                                aria-label="Model"
                                value={row.model}
                                onChange={(e) => updateRow(row.key, { model: e.target.value })}
                                placeholder="Optional"
                                className="h-8 font-mono text-xs"
                                aria-invalid={Boolean(rowError?.model)}
                              />
                              {rowError?.model ? <p className="mt-1 text-xs text-danger-text">{rowError.model}</p> : null}
                            </td>
                            <td className="px-2 py-2">
                              <Select value={row.unit} onValueChange={(value) => updateRow(row.key, { unit: value as Unit })}>
                                <SelectTrigger aria-label="Unit" className="h-8 w-full text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {UNIT_OPTIONS.map((option) => (
                                    <SelectItem key={option.value} value={option.value}>
                                      {option.label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </td>
                            <td className="px-2 py-2">
                              <Input
                                aria-label="USD per unit"
                                value={row.usdPerUnit}
                                onChange={(e) => updateRow(row.key, { usdPerUnit: e.target.value })}
                                inputMode="decimal"
                                placeholder="0.10"
                                className="h-8 w-24 font-mono text-xs"
                                aria-invalid={Boolean(rowError?.usdPerUnit)}
                              />
                              {rowError?.usdPerUnit ? <p className="mt-1 text-xs text-danger-text">{rowError.usdPerUnit}</p> : null}
                            </td>
                            <td className="px-2 py-2">
                              <Input
                                aria-label="Note"
                                value={row.note}
                                onChange={(e) => updateRow(row.key, { note: e.target.value })}
                                placeholder="Starter plan"
                                className="h-8 text-xs"
                                aria-invalid={Boolean(rowError?.note)}
                              />
                            </td>
                            <td className="px-2 py-2 text-xs text-muted-foreground whitespace-nowrap">{row.as_of}</td>
                            <td className="px-2 py-2">
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                aria-label="Remove row"
                                onClick={() => removeRow(row.key)}
                              >
                                <Icon as={Trash2Icon} size="sm" />
                              </Button>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
              <Button type="button" variant="outline" size="sm" className="self-start" onClick={addRow}>
                <Icon as={PlusIcon} size="sm" />
                Add a price
              </Button>
            </form>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="workspace-prices-form" disabled={update.isPending || isLoading}>
            {update.isPending ? "Saving…" : "Save prices"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The Providers page's entry point (docs/v4/COSTS.md §5 item 2): a plain
 * button that owns its own dialog state, so the (server-component) page just
 * drops this in — admin-gated with the console's usual pattern (R-V2-20-5),
 * never a security boundary on its own.
 */
export function WorkspacePricesButton() {
  const [open, setOpen] = React.useState(false);
  const { canWrite } = useWriteAccess("admin");
  return (
    <>
      <GatedButton allowed={canWrite} reason={writeAccessReason("admin")} variant="outline" onClick={() => setOpen(true)}>
        Your prices
      </GatedButton>
      <WorkspacePricesDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
