"use client";

import { CoinsIcon } from "lucide-react";

import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/shared/empty-state";
import type { CostLine } from "@/contracts/lkap-contracts";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";
import { formatUsd } from "@/components/console/sessions/session-model";

/**
 * Cost tab (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.4): `session.cost.lines`
 * from `GET /v1/sessions/{id}` (CONTRACTS-V2 §3.4). A line with no price
 * table entry comes back as `note: "no price"` and `cost_usd: null` — it is
 * rendered as the literal text "no price", **never** "$0" (the task's own
 * instruction; the api comment on `CostLine` says the same: "an unknown
 * price is never reported as zero").
 */
function costCell(line: CostLine): string {
  if (line.note) return line.note;
  const formatted = formatUsd(line.cost_usd);
  return formatted ?? "—";
}

export function CostTab({ session }: SessionTabProps) {
  const cost = session.cost ?? { lines: [] };
  const lines = cost.lines ?? [];

  if (lines.length === 0) {
    return (
      <EmptyState
        icon={CoinsIcon}
        title="No cost data"
        description="Usage-based cost lines appear here once the session posts its final usage."
      />
    );
  }

  return (
    <div className="space-y-3">
      <Table aria-label="Cost lines">
        <TableHeader>
          <TableRow>
            <TableHead>Provider</TableHead>
            <TableHead>Model</TableHead>
            <TableHead>Unit</TableHead>
            <TableHead className="text-right">Quantity</TableHead>
            <TableHead className="text-right">Unit price</TableHead>
            <TableHead className="text-right">Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lines.map((line, index) => (
            <TableRow key={`${line.provider_id}-${line.unit}-${index}`}>
              <TableCell className="font-medium text-foreground">{line.provider_id}</TableCell>
              <TableCell className="text-muted-foreground">{line.model ?? "—"}</TableCell>
              <TableCell className="text-muted-foreground">{line.unit}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {String(line.quantity)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                {line.unit_price_usd != null ? (formatUsd(line.unit_price_usd) ?? "—") : "—"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {costCell(line)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={5} className="font-medium text-foreground">
              Total
            </TableCell>
            <TableCell className="text-right font-mono font-medium tabular-nums text-foreground">
              {formatUsd(cost.total_usd) ?? "—"}
            </TableCell>
          </TableRow>
        </TableFooter>
      </Table>
    </div>
  );
}
