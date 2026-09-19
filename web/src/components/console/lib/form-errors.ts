/**
 * Walks a react-hook-form `FieldErrors` tree (as produced by
 * `zod-resolver.ts`) and returns the first `.message` found, in field
 * order. Used to surface a toast when the user tries to save with an error
 * on a tab that isn't currently active — `TabsContent` unmounts inactive
 * panels, so an inline field error there is otherwise invisible.
 */
export function firstErrorMessage(node: unknown): string | undefined {
  if (node === null || typeof node !== "object") return undefined;

  const record = node as Record<string, unknown>;
  if (typeof record.message === "string") {
    return record.message;
  }

  for (const value of Object.values(record)) {
    const found = firstErrorMessage(value);
    if (found) return found;
  }

  return undefined;
}
