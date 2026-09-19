import * as React from "react";

import { Kbd as UiKbd, KbdGroup } from "@/components/ui/kbd";

export interface KbdProps {
  children: React.ReactNode;
  className?: string;
}

/** Keyboard hint (docs/UI_UX_SPEC.md §2.7): wraps shadcn `kbd` (mono, `rounded-xs`). */
export function Kbd({ children, className }: KbdProps) {
  return <UiKbd className={className}>{children}</UiKbd>;
}

export { KbdGroup };
