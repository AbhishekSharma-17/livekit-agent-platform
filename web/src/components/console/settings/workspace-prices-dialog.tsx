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
import { Skeleton } from "@/components/ui/skeleton";
import { Icon } from "@/components/shared/icon";
import { LoadingRegion } from "@/components/shared/loading-state";
import { IfCan } from "@/components/console/shared/permission";
import { useUpdateWorkspacePrices, useWorkspacePrices } from "@/components/console/lib/cost-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
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
  const { data, isLoading, isError, error, refetch } = useWorkspacePrices();
  const update = useUpdateWorkspacePrices();
  const [rows, setRows] = React.useState<DraftRow[]>([]);
  const [errors, setErrors] = React.useState<Record<string, RowError>>({});
  const [saveError, setSaveError] = React.useState<unknown>(null);
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
    setSaveError(null);
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
    setSaveError(null);
    try {
      await update.mutateAsync(rows.map(toWorkspacePrice));
      toast.success("Your prices were saved");
      onOpenChange(false);
    } catch (err) {
      // Keep every edited row; say what happened above the table.
      setSaveError(err);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Your prices</DialogTitle>
          <DialogDescription>
            Prices you enter here are used by every estimate and cost line before OpenRouter&apos;s live sheet and
            the list-price table. Use them for vendors billed by plan, where no published per-unit price exists.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {isLoading ? (
            <LoadingRegion label="Loading your prices" className="flex flex-col gap-2">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-11 w-full" />
              <Skeleton className="h-11 w-full" />
            </LoadingRegion>
          ) : isError ? (
            <ErrorBanner error={error} context={{ action: "load your prices" }} onRetry={() => void refetch()} />
          ) : (
            <form id="workspace-prices-form" onSubmit={onSave} className="flex flex-col gap-3">
              {saveError ? <ErrorBanner error={saveError} context={{ action: "save your prices" }} /> : null}
              {/* A table from 640 px; below that each row stacks into a small card (one DOM, CSS only). */}
              <div className="rounded border border-border sm:overflow-x-auto">
                <table className="w-full border-collapse text-label max-sm:block sm:min-w-[640px]">
                  <thead className="max-sm:sr-only">
                    <tr className="h-9 border-b border-border bg-muted text-left text-caption font-medium text-text-secondary">
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
                  <tbody className="max-sm:block">
                    {rows.length === 0 ? (
                      <tr className="max-sm:block">
                        <td colSpan={7} className="px-2 py-4 text-center text-text-secondary max-sm:block">
                          No prices set yet. Add one for each plan-billed vendor.
                        </td>
                      </tr>
                    ) : (
                      rows.map((row) => {
                        const rowError = errors[row.key];
                        return (
                          <tr
                            key={row.key}
                            className="border-b border-border align-top last:border-0 max-sm:grid max-sm:grid-cols-2 max-sm:gap-x-3 max-sm:gap-y-2 max-sm:p-3"
                          >
                            <td className="px-2 py-2 max-sm:col-span-2 max-sm:p-0">
                              <MobileLabel>Provider</MobileLabel>
                              <Input
                                aria-label="Provider id"
                                value={row.provider_id}
                                onChange={(e) => updateRow(row.key, { provider_id: e.target.value })}
                                placeholder="bey-avatar"
                                className="font-mono"
                                aria-invalid={Boolean(rowError?.provider_id)}
                              />
                              <CellError>{rowError?.provider_id}</CellError>
                            </td>
                            <td className="px-2 py-2 max-sm:col-span-2 max-sm:p-0">
                              <MobileLabel>Model</MobileLabel>
                              <Input
                                aria-label="Model"
                                value={row.model}
                                onChange={(e) => updateRow(row.key, { model: e.target.value })}
                                placeholder="Optional"
                                className="font-mono"
                                aria-invalid={Boolean(rowError?.model)}
                              />
                              <CellError>{rowError?.model}</CellError>
                            </td>
                            <td className="px-2 py-2 max-sm:p-0">
                              <MobileLabel>Unit</MobileLabel>
                              <Select value={row.unit} onValueChange={(value) => updateRow(row.key, { unit: value as Unit })}>
                                <SelectTrigger aria-label="Unit" className="w-full">
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
                            <td className="px-2 py-2 max-sm:p-0">
                              <MobileLabel>USD per unit</MobileLabel>
                              <Input
                                aria-label="USD per unit"
                                value={row.usdPerUnit}
                                onChange={(e) => updateRow(row.key, { usdPerUnit: e.target.value })}
                                inputMode="decimal"
                                placeholder="0.10"
                                className="font-mono tabular-nums sm:w-24"
                                aria-invalid={Boolean(rowError?.usdPerUnit)}
                              />
                              <CellError>{rowError?.usdPerUnit}</CellError>
                            </td>
                            <td className="px-2 py-2 max-sm:col-span-2 max-sm:p-0">
                              <MobileLabel>Note</MobileLabel>
                              <Input
                                aria-label="Note"
                                value={row.note}
                                onChange={(e) => updateRow(row.key, { note: e.target.value })}
                                placeholder="Starter plan"
                                aria-invalid={Boolean(rowError?.note)}
                              />
                              <CellError>{rowError?.note}</CellError>
                            </td>
                            <td className="px-2 py-2 whitespace-nowrap text-caption text-text-secondary tabular-nums max-sm:flex max-sm:items-center max-sm:p-0 sm:pt-4">
                              <span className="sm:hidden">As of&nbsp;</span>
                              {row.as_of}
                            </td>
                            <td className="px-2 py-2 max-sm:flex max-sm:justify-end max-sm:p-0">
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
              <Button type="button" size="sm" className="self-start" onClick={addRow}>
                <Icon as={PlusIcon} size="sm" />
                Add a price
              </Button>
            </form>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" onClick={() => onOpenChange(false)} disabled={update.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="workspace-prices-form"
            variant="primary"
            disabled={isLoading || isError}
            busy={update.isPending}
            busyLabel="Saving prices…"
          >
            Save prices
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The Providers page's entry point (docs/v4/COSTS.md §5 item 2): a plain
 * button that owns its own dialog state, so the (server-component) page just
 * drops this in. Admin-only, and — per the permission pattern (D12) — not
 * rendered at all for anyone else, rather than shown disabled. Never a
 * security boundary on its own: the route checks the role too.
 */
export function WorkspacePricesButton() {
  const [open, setOpen] = React.useState(false);
  return (
    <IfCan min="admin">
      <Button type="button" onClick={() => setOpen(true)}>
        Your prices
      </Button>
      <WorkspacePricesDialog open={open} onOpenChange={setOpen} />
    </IfCan>
  );
}

/** A cell's field name, shown only while the row is stacked on a phone (the input keeps its own `aria-label`). */
function MobileLabel({ children }: { children: React.ReactNode }) {
  return (
    <span aria-hidden="true" className="mb-1 block text-caption font-medium text-text-secondary sm:hidden">
      {children}
    </span>
  );
}

function CellError({ children }: { children?: string }) {
  if (!children) return null;
  return <p className="mt-1 text-caption text-destructive-text">{children}</p>;
}
