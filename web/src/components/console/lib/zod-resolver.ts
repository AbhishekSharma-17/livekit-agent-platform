import type { FieldErrors, FieldValues, Resolver } from "react-hook-form";
import type { ZodType } from "zod";

/**
 * Minimal zod v4 resolver for react-hook-form.
 *
 * `@hookform/resolvers` isn't installed (W0-SCAFFOLD pinned `zod@^4`, and
 * `@hookform/resolvers@<5` mis-handles zod v4's `error.issues` shape); adding
 * a dependency also means touching the shared `package.json`, which
 * W1-WEB-CONSOLE doesn't own. This mirrors `@hookform/resolvers/zod`'s
 * contract: parse, and on failure map every zod issue onto a **nested**
 * error object shaped like `T` (RHF's `errors.config.instructions.message`
 * access pattern, used throughout the tab components, needs real nesting —
 * a flat `"config.instructions"` key only satisfies RHF's internal
 * `handleSubmit` gate, not field-level error rendering).
 */
export function zodResolver<T extends FieldValues>(schema: ZodType<T>): Resolver<T> {
  return async (values) => {
    const result = schema.safeParse(values);
    if (result.success) {
      return { values: result.data, errors: {} };
    }

    const errors: Record<string, unknown> = {};
    for (const issue of result.error.issues) {
      setNestedError(errors, issue.path, { type: issue.code, message: issue.message });
    }

    return { values: {}, errors: errors as FieldErrors<T> };
  };
}

function setNestedError(root: Record<string, unknown>, path: PropertyKey[], leaf: { type: string; message: string }): void {
  if (path.length === 0) {
    // A root-level (object-wide) issue; surface it under a synthetic key
    // rather than dropping it.
    if (!("root" in root)) root.root = leaf;
    return;
  }

  let node: Record<string, unknown> = root;
  for (let i = 0; i < path.length - 1; i++) {
    const key = String(path[i]);
    const existing = node[key];
    if (typeof existing !== "object" || existing === null) {
      node[key] = {};
    }
    node = node[key] as Record<string, unknown>;
  }

  const leafKey = String(path[path.length - 1]);
  if (!(leafKey in node)) {
    node[leafKey] = leaf;
  }
}
