import * as React from "react";

/**
 * Editable tables in the tool editors (bindings, fixed values) stack on
 * phones instead of scrolling sideways (docs/ui/AUDIT.md M3; spec section 10:
 * no horizontal scroll at 390 px). At `sm` and up they are ordinary tables;
 * below it each row becomes a card-like block, the header row is kept for
 * screen readers only, every cell takes the full width, and `PhoneLabel`
 * shows the column name above its control.
 *
 * Put `STACKED_TABLE` on `<Table className>` and `STACKED_CONTROL` on the
 * fixed-width controls inside it.
 */
export const STACKED_TABLE =
  "max-sm:block max-sm:[&_thead]:sr-only max-sm:[&_tbody]:block max-sm:[&_tbody_tr]:flex max-sm:[&_tbody_tr]:flex-col max-sm:[&_tbody_tr]:gap-3 max-sm:[&_tbody_tr]:py-3 max-sm:[&_tbody_td]:block max-sm:[&_tbody_td]:p-0";

/** Fixed desktop widths become full width when the row stacks. */
export const STACKED_CONTROL = "max-sm:w-full";

/** The column name above a control, only while the row is stacked (the control keeps its own aria-label). */
export function PhoneLabel({ children }: { children: React.ReactNode }) {
  return (
    <span aria-hidden="true" className="mb-1.5 block text-caption font-medium text-text-secondary sm:hidden">
      {children}
    </span>
  );
}
