/**
 * V6-11: a console-side mirror of `contracts/src/lkap_contracts/tool_context.py` (D-V6-22,
 * D-V6-23) — pure functions, no React, so the tool editors can validate `{{ ctx.* }}` /
 * `{{ var.* }}` placeholders and binding targets inline, with the same messages the api's
 * `placeholder_issues` would give at save. The api stays the source of truth (a save still
 * goes through `config_service.py`'s validators); this only saves an author a round trip.
 *
 * `CTX_PLACEHOLDERS`/`CTX_LABELS`/the caps are dated console constants mirroring the contract
 * (no runtime array crosses the `.d.ts` boundary, only the `ToolContextPlaceholder` type) —
 * ask #34's "next plan" analogue: promote to a generated constant if a second surface needs
 * the same list (see `provider-meta.ts`'s `RECOMMENDED_STACK`, ask #42).
 */
import { CTX_PLACEHOLDERS, VARIABLE_NAME_PATTERN } from "@/components/console/flow/flow-model";

/**
 * `lkap_contracts.tool_context.ToolContextPlaceholder`, in the order the console lists them.
 * Defined in `flow-model.ts` (V6-19): a `tool` node's argument templates need the same list,
 * and this module already imports (and re-exports below) `VARIABLE_NAME_PATTERN` from there —
 * keeping the source of truth on one side avoids a cycle between the two modules.
 */
export { CTX_PLACEHOLDERS };
export type CtxPlaceholder = (typeof CTX_PLACEHOLDERS)[number];

/** `lkap_contracts.tool_context.CTX_LABELS` — plain words, shown in the Insert-value menu. */
export const CTX_LABELS: Record<CtxPlaceholder, string> = {
  session_id: "the session reference",
  agent_id: "the agent reference",
  caller_phone: "the caller's phone number",
  caller_identity: "the caller's identity",
  language: "the conversation's language",
  timezone: "the caller's time zone",
  channel: "how the caller reached us",
};

export { VARIABLE_NAME_PATTERN };
/** `lkap_contracts.tool_context._TOOL_ARGUMENT_RE`. */
export const TOOL_ARGUMENT_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/;

/** Caps mirroring `lkap_contracts.tool_context` (D-V6-23, ask #34). */
export const MAX_BINDINGS = 20;
export const MAX_BINDING_VALUE_CHARS = 500;
export const MAX_BINDING_TABLE_ROWS = 100;
export const MAX_REQUIRES_VARS = 20;
export const MAX_CONFIRM_READBACK = 10;
export const MAX_PINNED_ARGUMENTS = 20;

/** The parameter `confirm_readback` adds to a tool's schema. */
export const CONFIRMED_PARAMETER = "confirmed";

/** `lkap_contracts.tool_context.BINDING_TARGET_FORMS`. */
export const BINDING_TARGET_FORMS = [
  "details:<block_id>.<key>",
  "table:<block_id>",
  "checklist:<item_id>",
  "status",
  "note",
  "var:<name>",
] as const;

export type BindingKind = "details" | "table" | "checklist" | "status" | "note" | "var";

const BLOCK_ID = "[A-Za-z0-9_-]{1,64}";
const TARGET_RES: [BindingKind, RegExp][] = [
  ["details", new RegExp(`^details:(${BLOCK_ID})\\.([A-Za-z0-9_-]{1,64})$`)],
  ["table", new RegExp(`^table:(${BLOCK_ID})$`)],
  ["checklist", /^checklist:([A-Za-z0-9_.:-]{1,64})$/],
  ["status", /^status$/],
  ["note", /^note$/],
  ["var", /^var:([a-z][a-z0-9_]{0,63})$/],
];

export interface BindingTarget {
  kind: BindingKind;
  blockId?: string;
  key?: string;
}

/** Parse one `ToolBinding.to` (`lkap_contracts.tool_context.parse_binding_target`). */
export function parseBindingTarget(to: string): BindingTarget | null {
  const text = to.trim();
  for (const [kind, pattern] of TARGET_RES) {
    const match = pattern.exec(text);
    if (!match) continue;
    if (kind === "details") return { kind, blockId: match[1], key: match[2] };
    if (kind === "table") return { kind, blockId: match[1] };
    if (kind === "checklist" || kind === "var") return { kind, key: match[1] };
    return { kind };
  }
  return null;
}

/** The message `parse_binding_target` raises for a target none of `BINDING_TARGET_FORMS`. */
export function bindingTargetIssue(to: string): string | null {
  if (parseBindingTarget(to)) return null;
  return `binding target '${to}' must be one of: ${BINDING_TARGET_FORMS.join(", ")}`;
}

/** Build a `to` string from a kind and its parts — the inverse of `parseBindingTarget`. */
export function bindingTargetToString(target: BindingTarget): string {
  switch (target.kind) {
    case "details":
      return `details:${target.blockId ?? ""}.${target.key ?? ""}`;
    case "table":
      return `table:${target.blockId ?? ""}`;
    case "checklist":
      return `checklist:${target.key ?? ""}`;
    case "var":
      return `var:${target.key ?? ""}`;
    default:
      return target.kind;
  }
}

// --------------------------------------------------------------- placeholders

/** `lkap_contracts.tool_context.PLACEHOLDER_PATTERN` — group 1 is the namespace, group 2 the name. */
const PLACEHOLDER_RE = /\{\{\s*(?:(ctx|var)\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;
/** Any `{{ ctx.… }}` / `{{ var.… }}` spelling, even a malformed one, for the "never here" checks. */
const ANY_CONTEXT_RE = /\{\{\s*(?:ctx|var)\s*\./;
const ANY_CONTEXT_RE_G = new RegExp(ANY_CONTEXT_RE.source, "g");

export interface PlaceholderRef {
  namespace: "ctx" | "var";
  name: string;
}

/** The `ctx`/`var` placeholders `text` names, in order (arguments are left out). */
export function contextPlaceholders(text: string | null | undefined): PlaceholderRef[] {
  if (!text) return [];
  const refs: PlaceholderRef[] = [];
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    if (match[1] === "ctx" || match[1] === "var") refs.push({ namespace: match[1], name: match[2] });
  }
  return refs;
}

/** `{{ ctx.<name> }}` or `{{ var.<name> }}` — the token an "Insert value" pick writes. */
export function placeholderToken(namespace: "ctx" | "var", name: string): string {
  return `{{ ${namespace}.${name} }}`;
}

/**
 * Issues `text` has as a templated field (url or body_template): an unknown `ctx` name, a
 * malformed spelling, or a variable name that doesn't fit `VARIABLE_NAME_PATTERN`. Mirrors
 * `lkap_contracts.tool_context._names_issues`.
 */
export function placeholderFieldIssues(text: string | null | undefined): string[] {
  const issues: string[] = [];
  if (!text) return issues;
  const refs = contextPlaceholders(text);
  const anyCount = (text.match(ANY_CONTEXT_RE_G) ?? []).length;
  if (refs.length < anyCount) {
    issues.push("write a value as {{ ctx.<name> }} or {{ var.<name> }} (letters, digits, _)");
  }
  const seen = new Set<string>();
  for (const { namespace, name } of refs) {
    const key = `${namespace}.${name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (namespace === "ctx" && !(CTX_PLACEHOLDERS as readonly string[]).includes(name)) {
      issues.push(`'{{ ctx.${name} }}' is not a session value; use one of: ${CTX_PLACEHOLDERS.join(", ")}`);
    } else if (namespace === "var" && !VARIABLE_NAME_PATTERN.test(name)) {
      issues.push(`'{{ var.${name} }}': a variable name is lower case letters, digits and _`);
    }
  }
  return issues;
}

/** A field where session values and variables are never allowed (mirrors `_never_here`). */
export function neverHereIssue(text: string | null | undefined, where: string): string | null {
  if (text && ANY_CONTEXT_RE.test(text)) {
    return `session values and variables may not be used in ${where}`;
  }
  return null;
}

/** The exact message for a placeholder in a URL's scheme, host or port. */
export const URL_AUTHORITY_MESSAGE =
  "session values and variables may not be placed in the url's scheme, host or port; use the path or the query";

/**
 * The index in `url` up to which a caret counts as "in the scheme or authority" — mirrors
 * `lkap_contracts.tool_context.url_authority`: the text before `://` (if any) plus everything
 * up to the first `/`, `?` or `#`.
 */
export function urlAuthorityEnd(url: string): number {
  const sepIndex = url.indexOf("://");
  const start = sepIndex === -1 ? 0 : sepIndex + 3;
  let end = url.length;
  for (const stop of ["/", "?", "#"]) {
    const idx = url.indexOf(stop, start);
    if (idx !== -1 && idx < end) end = idx;
  }
  return end;
}

/** Whether inserting at `caret` in `url` would land in the scheme, host or port. */
export function caretInUrlAuthority(url: string, caret: number): boolean {
  return caret <= urlAuthorityEnd(url);
}

/** Whether `url`'s scheme/authority (as written, regardless of any cursor) already names a placeholder — the save-time check. */
export function urlAuthorityHasPlaceholder(url: string): boolean {
  return ANY_CONTEXT_RE.test(url.slice(0, urlAuthorityEnd(url)));
}

// --------------------------------------------------------------- requires_vars / confirm_readback

/** `lkap_contracts.tool_context._var_list_issues`, one message per bad name. */
export function requiresVarsIssues(names: readonly string[]): string[] {
  return names
    .filter((name) => !VARIABLE_NAME_PATTERN.test(name))
    .map((name) => `'${name}' is not a variable name (lower case, digits, _)`);
}

/**
 * `lkap_contracts.tool_context._readback_issues`: `names` must be the tool's own arguments
 * (when `properties` is known), not repeated, not pinned, and not the reserved `confirmed`.
 */
export function readbackIssues(
  names: readonly string[],
  properties: ReadonlySet<string> | null,
  pinned: ReadonlySet<string> = new Set(),
): string[] {
  const issues: string[] = [];
  const seen = new Set<string>();
  for (const name of names) {
    if (!TOOL_ARGUMENT_PATTERN.test(name)) {
      issues.push(`'${name}' is not an argument name`);
    } else if (name === CONFIRMED_PARAMETER) {
      issues.push(`'${CONFIRMED_PARAMETER}' is added by the read-back itself`);
    } else if (seen.has(name)) {
      issues.push(`'${name}' is listed twice`);
    } else if (pinned.has(name)) {
      issues.push(`'${name}' is pinned, so the model never says it`);
    } else if (properties && !properties.has(name)) {
      issues.push(`'${name}' is not one of the tool's arguments`);
    }
    seen.add(name);
  }
  if (names.length > 0 && properties?.has(CONFIRMED_PARAMETER)) {
    issues.push(`the tool already has an argument named '${CONFIRMED_PARAMETER}'; read-back needs that name`);
  }
  return issues;
}

// --------------------------------------------------------------- pinned arguments

/** `lkap_contracts.tool_context._pinned_issues` (the name check; string values also get placeholder checks). */
export function pinnedArgumentIssues(pinned: Record<string, unknown>): string[] {
  const issues: string[] = [];
  for (const [name, value] of Object.entries(pinned)) {
    if (!TOOL_ARGUMENT_PATTERN.test(name)) {
      issues.push(`'${name}' is not an argument name`);
    } else if (typeof value === "string") {
      issues.push(...placeholderFieldIssues(value).map((message) => `${name}: ${message}`));
    }
  }
  return issues;
}

/** How a variable reads when a field names it in plain words (`variable_label`). */
export function variableLabel(name: string): string {
  return name.replace(/_/g, " ").trim() || name;
}

// --------------------------------------------------------------- schema properties

/** The top-level property names of a JSON Schema object, or `null` when it declares none. */
export function schemaProperties(parameters: unknown): Set<string> | null {
  if (!parameters || typeof parameters !== "object") return null;
  const properties = (parameters as { properties?: unknown }).properties;
  if (!properties || typeof properties !== "object") return null;
  return new Set(Object.keys(properties as Record<string, unknown>));
}

// --------------------------------------------------------------- cap messages

export function capMessage(count: number, limit: number, what: string): string | null {
  return count > limit ? `at most ${limit} ${what}` : null;
}

/** `ToolBinding._pointer`: empty, or starting with `/`. */
export function bindingPathIssue(path: string): string | null {
  return path && !path.startsWith("/") ? "empty, or starting with '/'" : null;
}
