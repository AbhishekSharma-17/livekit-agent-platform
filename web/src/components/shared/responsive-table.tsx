import * as React from "react";

import Link from "next/link";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface ResponsiveTableColumn<T> {
  id: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  /** Applied to both the `th` and the `td`. */
  className?: string;
  headerClassName?: string;
  /** Right-align (numbers, row actions). */
  align?: "start" | "end";
  /**
   * The cell holds its own buttons/links (e.g. a row actions menu); it is
   * lifted above the row's link overlay so it stays clickable.
   */
  interactive?: boolean;
}

export interface ResponsiveTableProps<T> {
  columns: ResponsiveTableColumn<T>[];
  rows: T[];
  /** Card rendering used below 768 px. */
  renderCard: (row: T) => React.ReactNode;
  /** Makes the whole row (and card) a link target with a focus ring. */
  rowHref?: (row: T) => string | undefined;
  getRowKey?: (row: T, index: number) => React.Key;
  /** Accessible name for the table / list. */
  label?: string;
  /** Rendered instead of the table when `rows` is empty. */
  empty?: React.ReactNode;
  className?: string;
}

const LIFTED = "relative z-10";
const ROW_LINK =
  "outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-md focus-visible:after:ring-2 focus-visible:after:ring-ring focus-visible:after:ring-inset";

/**
 * Table ≥ 768 px, card list below (docs/UI_UX_SPEC.md §2.7). Both renderings
 * are in the DOM and switched with CSS (`hidden md:block` / `md:hidden`), so
 * it is SSR-safe and never reads `matchMedia`. With `rowHref`, the first cell
 * becomes a stretched link (table) and each card gets a full-card link named
 * by the card's text; mark columns with buttons as `interactive`, and keep
 * card controls as real `<a>`/`<button>` elements (they stay clickable).
 */
export function ResponsiveTable<T>({
  columns,
  rows,
  renderCard,
  rowHref,
  getRowKey,
  label,
  empty,
  className,
}: ResponsiveTableProps<T>) {
  const baseId = React.useId();

  if (rows.length === 0 && empty !== undefined) {
    return <div data-slot="responsive-table-empty">{empty}</div>;
  }

  const keyFor = (row: T, index: number) => (getRowKey ? getRowKey(row, index) : index);

  return (
    <div data-slot="responsive-table" className={className}>
      <div data-slot="responsive-table-table" className="hidden md:block">
        <Table aria-label={label}>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {columns.map((column) => (
                <TableHead
                  key={column.id}
                  className={cn(
                    "h-9 text-xs font-medium text-muted-foreground",
                    column.align === "end" && "text-right",
                    column.className,
                    column.headerClassName,
                  )}
                >
                  {column.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => {
              const href = rowHref?.(row);
              return (
                <TableRow
                  key={keyFor(row, index)}
                  data-href={href}
                  className={cn(href && "relative cursor-pointer focus-within:bg-muted/50")}
                >
                  {columns.map((column, columnIndex) => {
                    const content = column.cell(row);
                    return (
                      <TableCell
                        key={column.id}
                        className={cn(
                          "py-3 text-[0.8125rem]",
                          column.align === "end" && "text-right",
                          href && column.interactive && LIFTED,
                          column.className,
                        )}
                      >
                        {href && columnIndex === 0 ? (
                          <Link href={href} className={ROW_LINK}>
                            {content}
                          </Link>
                        ) : (
                          content
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <ul data-slot="responsive-table-cards" aria-label={label} className="flex flex-col gap-2 md:hidden">
        {rows.map((row, index) => {
          const href = rowHref?.(row);
          const contentId = `${baseId}-card-${index}`;
          return (
            <li
              key={keyFor(row, index)}
              className="relative rounded-lg border border-border bg-card p-4 text-card-foreground"
            >
              {href ? (
                <Link
                  href={href}
                  aria-labelledby={contentId}
                  className="absolute inset-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              ) : null}
              <div
                id={contentId}
                className={cn(
                  href &&
                    "pointer-events-none relative [&_a]:pointer-events-auto [&_button]:pointer-events-auto [&_[role=button]]:pointer-events-auto",
                )}
              >
                {renderCard(row)}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
