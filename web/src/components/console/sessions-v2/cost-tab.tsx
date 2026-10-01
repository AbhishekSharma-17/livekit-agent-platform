"use client";

import { CoinsIcon } from "lucide-react";

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatCard, StatGrid } from "@/components/shared/data-display";
import { EmptyState } from "@/components/shared/empty-state";
import { driverSentence, formatUsd } from "@/components/console/lib/cost-hooks";
import type { CostDriver, CostLine } from "@/contracts/lkap-contracts";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";
import { EMPTY_VALUE } from "@/lib/format";

/**
 * Cost tab (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.4, extended by V4-16 per
 * docs/v4/COSTS.md §5 item 5): `session.cost` from `GET /v1/sessions/{id}`.
 * A line with no price table entry comes back as `note: "no price"` and
 * `cost_usd: null` — rendered as the literal text "no price", **never**
 * "$0" (the api comment on `CostLine` says the same: "an unknown price is
 * never reported as zero"). Older sessions (before V4-15) have no
 * `estimated_usd` snapshot — every such figure reads "no estimate", never a
 * back-filled guess.
 */
function costCell(line: CostLine): string {
  if (line.note) return line.note;
  const formatted = formatUsd(line.cost_usd);
  return formatted ?? EMPTY_VALUE;
}

/** This line's estimated cost, joined from `cost.drivers` on (provider_id, model, unit). */
function estimatedCellFor(line: CostLine, drivers: CostDriver[]): string {
  const driver = drivers.find(
    (d) => d.provider_id === line.provider_id && (d.model ?? null) === (line.model ?? null) && d.unit === line.unit,
  );
  if (!driver) return "no estimate";
  const formatted = formatUsd(driver.estimated_usd);
  return formatted ?? "no estimate";
}

function varianceText(variance: string | number | null | undefined, pct: number | null | undefined): string {
  const usd = formatUsd(typeof variance === "string" ? Math.abs(Number(variance)) : variance != null ? Math.abs(variance) : null);
  if (usd === null) return EMPTY_VALUE;
  const sign = Number(variance) >= 0 ? "+" : "−";
  const pctText = pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%)` : "";
  return `${sign}${usd}${pctText}`;
}

/**
 * The difference as words beside the figure, never colour alone
 * (docs/ui/DESIGN-SYSTEM.md section 9): over or under the estimate.
 */
function varianceHint(variance: string | number | null | undefined): string | undefined {
  if (variance == null) return undefined;
  const n = Number(variance);
  if (!Number.isFinite(n) || n === 0) return "As estimated";
  return n > 0 ? "Over the estimate" : "Under the estimate";
}

export function CostTab({ session }: SessionTabProps) {
  const cost = session.cost ?? { lines: [] };
  const lines = cost.lines ?? [];
  const drivers = cost.drivers ?? [];
  const hasVendorCharge = lines.some((line) => line.vendor_usd != null);
  const notableDrivers = drivers.filter((driver) => driver.reason !== "as estimated");

  if (lines.length === 0 && cost.estimated_usd == null) {
    return (
      <EmptyState
        icon={CoinsIcon}
        title="No cost data"
        description="Usage-based cost lines appear here once the session posts its final usage."
      />
    );
  }

  return (
    <div className="space-y-4">
      <StatGrid>
        <StatCard label="Estimated" value={formatUsd(cost.estimated_usd) ?? "no estimate"} hint="Snapshot when the call started" />
        <StatCard label="Actual" value={formatUsd(cost.total_usd) ?? EMPTY_VALUE} hint="From the call's final usage" />
        {cost.estimated_usd != null && cost.total_usd != null ? (
          <StatCard
            label="Difference"
            value={varianceText(cost.variance_usd, cost.variance_pct)}
            hint={varianceHint(cost.variance_usd)}
          />
        ) : (
          <StatCard label="Difference" value={EMPTY_VALUE} hint="Needs both figures" />
        )}
        {cost.reconciled_usd != null ? (
          <StatCard label="Vendor charged" value={formatUsd(cost.reconciled_usd) ?? EMPTY_VALUE} hint="Reconciled with the vendor" />
        ) : null}
      </StatGrid>

      {lines.length > 0 ? (
        <Table framed aria-label="Cost lines">
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Unit</TableHead>
              <TableHead numeric>Quantity</TableHead>
              <TableHead numeric>Unit price</TableHead>
              <TableHead numeric>Estimated</TableHead>
              {hasVendorCharge ? <TableHead numeric>Vendor charged</TableHead> : null}
              <TableHead numeric>Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lines.map((line, index) => (
              <TableRow key={`${line.provider_id}-${line.unit}-${index}`}>
                <TableCell className="font-medium text-foreground">{line.provider_id}</TableCell>
                <TableCell className="text-text-secondary">{line.model ?? EMPTY_VALUE}</TableCell>
                <TableCell className="text-text-secondary">{line.unit}</TableCell>
                <TableCell numeric>{String(line.quantity)}</TableCell>
                <TableCell numeric className="text-text-secondary">
                  {line.unit_price_usd != null ? (formatUsd(line.unit_price_usd) ?? EMPTY_VALUE) : EMPTY_VALUE}
                </TableCell>
                <TableCell numeric className="text-text-secondary">
                  {estimatedCellFor(line, drivers)}
                </TableCell>
                {hasVendorCharge ? (
                  <TableCell numeric className="text-text-secondary">
                    {line.vendor_usd != null ? (formatUsd(line.vendor_usd) ?? EMPTY_VALUE) : EMPTY_VALUE}
                  </TableCell>
                ) : null}
                <TableCell numeric>{costCell(line)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell colSpan={hasVendorCharge ? 7 : 6} className="font-medium text-foreground">
                Total
              </TableCell>
              <TableCell numeric className="font-medium text-foreground">
                {formatUsd(cost.total_usd) ?? EMPTY_VALUE}
              </TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      ) : null}

      {notableDrivers.length > 0 ? (
        <div className="flex flex-col gap-1.5 rounded border border-border bg-muted p-3">
          <h3 className="text-body font-semibold text-foreground">Why it differs</h3>
          <ul className="flex flex-col gap-1 text-label text-text-secondary">
            {notableDrivers.map((driver, index) => (
              <li key={`${driver.slot}-${driver.unit}-${index}`}>{driverSentence(driver)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {cost.estimate_as_of ? <p className="text-caption text-text-secondary">Prices as of {cost.estimate_as_of}.</p> : null}
    </div>
  );
}
