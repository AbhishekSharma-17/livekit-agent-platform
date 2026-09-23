import { fieldIds } from "@/components/shared/field";

/** `aria-describedby` for a control wrapped in a `Controller` (which `Field` can't reach to wire itself). */
export function describedBy(id: string, hasError = false): string {
  const ids = fieldIds(id);
  return hasError ? `${ids.hint} ${ids.error}` : ids.hint;
}
