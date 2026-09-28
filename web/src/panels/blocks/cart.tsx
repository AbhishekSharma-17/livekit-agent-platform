"use client";

/**
 * `cart` block — lines and totals the agent shows, such as an order to
 * confirm (`cart_set`, V6-23, D-V6-20, ask #215). Display data only: this
 * block places no order.
 *
 * Money renders with `Intl.NumberFormat` and the state's own `currency` —
 * never a hardcoded `USD`, unlike `details.tsx`'s money rows. Totals are
 * shown exactly as the worker computed them (`cart_totals`, to the cent);
 * this renderer never recomputes a subtotal or total itself.
 */
import * as React from "react";

import type { CartAdjustment, CartBlockState, CartLine } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

/** `Intl.NumberFormat` with the cart's own currency; falls back to a plain number + code for a currency `Intl` does not recognise. */
function formatMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

function CartLineRow({ line, currency }: { line: CartLine; currency: string }) {
  const quantity = line.quantity ?? 1;
  const lineTotal = line.line_total ?? 0;
  return (
    <li data-slot="cart-line" className="flex items-start justify-between gap-3 py-1.5 text-sm">
      <div className="min-w-0">
        <p className="truncate">
          {quantity > 1 && <span className="text-muted-foreground mr-1 tabular-nums">{quantity}×</span>}
          {line.name}
        </p>
        {line.note && <p className="text-muted-foreground text-[0.8125rem] break-words">{line.note}</p>}
      </div>
      <span className="shrink-0 tabular-nums">{formatMoney(lineTotal, currency)}</span>
    </li>
  );
}

function AdjustmentRow({ adjustment, currency }: { adjustment: CartAdjustment; currency: string }) {
  return (
    <div data-slot="cart-adjustment" className="text-muted-foreground flex items-center justify-between gap-3 py-1 text-[0.8125rem]">
      <span>{adjustment.label}</span>
      <span className="tabular-nums">{formatMoney(adjustment.amount, currency)}</span>
    </div>
  );
}

export function CartBlock({ spec, data, title, highlighted }: BlockRenderProps<CartBlockState>) {
  const currency = typeof data.currency === "string" && data.currency ? data.currency : "USD";
  const lines = Array.isArray(data.lines) ? data.lines : [];
  const adjustments = Array.isArray(data.adjustments) ? data.adjustments : [];

  return (
    <BlockFrame spec={spec} title={title} count={lines.length} highlighted={highlighted}>
      {lines.length === 0 ? (
        <PanelEmpty>Nothing in the cart yet.</PanelEmpty>
      ) : (
        <div data-slot="block-cart" className="flex flex-col gap-1">
          <ul className="divide-border flex flex-col divide-y">
            {lines.map((line) => (
              <CartLineRow key={line.id} line={line} currency={currency} />
            ))}
          </ul>
          <div className="border-border mt-1 flex flex-col border-t pt-1.5">
            {adjustments.length > 0 && (
              <>
                <div className="text-muted-foreground flex items-center justify-between gap-3 py-1 text-[0.8125rem]">
                  <span>Subtotal</span>
                  <span className="tabular-nums">{formatMoney(data.subtotal ?? 0, currency)}</span>
                </div>
                {adjustments.map((adjustment, index) => (
                  <AdjustmentRow key={index} adjustment={adjustment} currency={currency} />
                ))}
              </>
            )}
            <div className="flex items-center justify-between gap-3 pt-1 text-base font-semibold">
              <span>Total</span>
              <span className="tabular-nums">{formatMoney(data.total ?? 0, currency)}</span>
            </div>
          </div>
          {typeof data.updated_at === "number" && (
            <span className="text-muted-foreground text-[0.6875rem]">Updated {formatTime(data.updated_at)}</span>
          )}
        </div>
      )}
    </BlockFrame>
  );
}

export default CartBlock;
