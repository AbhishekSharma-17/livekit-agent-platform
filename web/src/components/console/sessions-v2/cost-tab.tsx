"use client";

import { CoinsIcon } from "lucide-react";

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/shared/empty-state";
import { driverSentence, formatUsd } from "@/components/console/lib/cost-hooks";
import type { CostDriver, CostLine } from "@/contracts/lkap-contracts";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";

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
  return formatted ?? "—";
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
  if (usd === null) return "—";
  const sign = Number(variance) >= 0 ? "+" : "−";
  const pctText = pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%)` : "";
  return `${sign}${usd}${pctText}`;
}

function CostTile({ label, value, tone }: { label: string; value: string; tone?: "danger" | "success" }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p
        className={
          tone === "danger"
            ? "mt-1 font-mono text-lg font-semibold tabular-nums text-danger-text"
            : tone === "success"
              ? "mt-1 font-mono text-lg font-semibold tabular-nums text-success-text"
              : "mt-1 font-mono text-lg font-semibold tabular-nums text-foreground"
        }
      >
        {value}
      </p>
    </div>
  );
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
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <CostTile label="Estimated" value={formatUsd(cost.estimated_usd) ?? "no estimate"} />
        <CostTile label="Actual" value={formatUsd(cost.total_usd) ?? "—"} />
        <CostTile
          label="Difference"
          value={cost.estimated_usd != null && cost.total_usd != null ? varianceText(cost.variance_usd, cost.variance_pct) : "—"}
          tone={cost.variance_usd != null ? (Number(cost.variance_usd) > 0 ? "danger" : "success") : undefined}
        />
        {cost.reconciled_usd != null ? <CostTile label="Vendor charged" value={formatUsd(cost.reconciled_usd) ?? "—"} /> : null}
      </div>

      {lines.length > 0 ? (
        <Table aria-label="Cost lines">
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Unit</TableHead>
              <TableHead className="text-right">Quantity</TableHead>
              <TableHead className="text-right">Unit price</TableHead>
              <TableHead className="text-right">Estimated</TableHead>
              {hasVendorCharge ? <TableHead className="text-right">Vendor charged</TableHead> : null}
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lines.map((line, index) => (
              <TableRow key={`${line.provider_id}-${line.unit}-${index}`}>
                <TableCell className="font-medium text-foreground">{line.provider_id}</TableCell>
                <TableCell className="text-muted-foreground">{line.model ?? "—"}</TableCell>
                <TableCell className="text-muted-foreground">{line.unit}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{String(line.quantity)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                  {line.unit_price_usd != null ? (formatUsd(line.unit_price_usd) ?? "—") : "—"}
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                  {estimatedCellFor(line, drivers)}
                </TableCell>
                {hasVendorCharge ? (
                  <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                    {line.vendor_usd != null ? (formatUsd(line.vendor_usd) ?? "—") : "—"}
                  </TableCell>
                ) : null}
                <TableCell className="text-right font-mono tabular-nums">{costCell(line)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell colSpan={hasVendorCharge ? 7 : 6} className="font-medium text-foreground">
                Total
              </TableCell>
              <TableCell className="text-right font-mono font-medium tabular-nums text-foreground">
                {formatUsd(cost.total_usd) ?? "—"}
              </TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      ) : null}

      {notableDrivers.length > 0 ? (
        <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-muted/30 p-3">
          <h3 className="text-sm font-semibold text-foreground">Why it differs</h3>
          <ul className="flex flex-col gap-1 text-[0.8125rem] text-muted-foreground">
            {notableDrivers.map((driver, index) => (
              <li key={`${driver.slot}-${driver.unit}-${index}`}>{driverSentence(driver)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {cost.estimate_as_of ? <p className="text-xs text-muted-foreground">Prices as of {cost.estimate_as_of}.</p> : null}
    </div>
  );
}
