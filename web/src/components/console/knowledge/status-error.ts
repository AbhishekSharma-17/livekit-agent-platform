import { isPresentable } from "@/components/console/lib/friendly-error";

/**
 * The reason an import or an indexing job failed, as stored by the worker
 * (`KbDocumentOut.error`, `DatasetOut.error`, `KbSourceOut.message`). It is
 * shown only when it reads as plain copy ("Could not extract text", "Bad
 * header row."); anything technical — a stack trace, a vendor code, a URL —
 * becomes `fallback` (docs/ui/DESIGN-SYSTEM.md section 1: never show raw
 * errors or vendor messages).
 */
export function plainStatusError(raw: string | null | undefined, fallback: string): string {
  const text = raw?.trim();
  return text && isPresentable(text) ? text : fallback;
}
