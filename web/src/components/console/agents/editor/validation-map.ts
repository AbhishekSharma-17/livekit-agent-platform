import { ApiError } from "@/lib/api";
import type { ValidationResult } from "@/contracts/lkap-contracts";

import type { EditorSectionDef } from "./types";

/**
 * Distributes validation results to editor sections (docs/UI_UX_SPEC.md §4.3,
 * §6, §7.14; CONTRACTS-V2 `ValidationResult.issues[]`).
 *
 * Sources, in order of precision:
 * 1. `ValidationResult.issues[]` — `{path, message, severity}`; the path decides the section.
 * 2. `errors[]` / `warnings[]` strings — the api prefixes most of them with a
 *    config path (`"pipeline.stt: unknown provider 'x'"`,
 *    `"pipeline.realtime is required when …"`); that leading token is used as the path.
 * 3. The keyword heuristic of §7.14 for strings without a usable path.
 * 4. Anything else lands in `"general"` (shown in every section's issue list).
 *
 * Pure functions only; the React side lives in `editor-context.tsx`.
 */

export const GENERAL_SECTION = "general";

export type IssueSeverity = "error" | "warning";

export interface EditorIssue {
  /** Stable key for lists. */
  key: string;
  /** Config-relative path (`pipeline.stt`), a top-level agent field (`name`, `limits.rate_per_ip_per_min`), or null. */
  path: string | null;
  message: string;
  severity: IssueSeverity;
  /** Section id, or `GENERAL_SECTION`. */
  section: string;
  source: "server" | "client";
}

export type SectionIssueRule = Pick<EditorSectionDef, "id" | "order" | "issuePaths" | "issueKeywords" | "issueKeywordPriority">;

/** `config.pipeline.stt` → `pipeline.stt`; array indices become dotted (`tools.tool_ids.0`). */
export function normalizeIssuePath(path: string): string {
  const dotted = path.replace(/\[(\d+)\]/g, ".$1").replace(/^\.+/, "");
  return dotted.startsWith("config.") ? dotted.slice("config.".length) : dotted;
}

function matchesPrefix(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}.`);
}

/** The section whose longest `issuePaths` prefix matches, or null. */
export function sectionForPath(path: string, rules: readonly SectionIssueRule[]): string | null {
  const normalized = normalizeIssuePath(path);
  let best: { id: string; length: number } | null = null;
  for (const rule of rules) {
    for (const prefix of rule.issuePaths ?? []) {
      if (matchesPrefix(normalized, prefix) && (!best || prefix.length > best.length)) {
        best = { id: rule.id, length: prefix.length };
      }
    }
  }
  return best?.id ?? null;
}

/** The first rule (by keyword priority) whose `issueKeywords` match the message, or null. */
export function sectionForKeywords(message: string, rules: readonly SectionIssueRule[]): string | null {
  const ordered = [...rules]
    .filter((rule) => rule.issueKeywords)
    .sort((a, b) => (a.issueKeywordPriority ?? a.order) - (b.issueKeywordPriority ?? b.order));
  for (const rule of ordered) {
    const pattern = rule.issueKeywords as RegExp;
    // A global/sticky regex keeps `lastIndex` between calls; reset so matching is stateless.
    pattern.lastIndex = 0;
    if (pattern.test(message)) return rule.id;
  }
  return null;
}

const LEADING_PATH = /^([A-Za-z_][\w]*(?:(?:\.[\w]+)|(?:\[\d+\]))*)(?=:|\s|$)/;

/** The dotted path a plain api message starts with (`"pipeline.stt: …"` → `pipeline.stt`), if any. */
export function leadingPath(message: string): string | null {
  const match = LEADING_PATH.exec(message.trim());
  if (!match) return null;
  // A single bare word ("Instructions are empty") is only a path when followed by ":".
  const candidate = match[1];
  const rest = message.trim().slice(candidate.length);
  if (!candidate.includes(".") && !candidate.includes("[") && !rest.startsWith(":")) return null;
  return candidate;
}

/** Section for one issue: path first, then keywords, else general. */
export function classifyIssue(
  path: string | null,
  message: string,
  rules: readonly SectionIssueRule[],
): string {
  if (path) {
    const byPath = sectionForPath(path, rules);
    if (byPath) return byPath;
  }
  return sectionForKeywords(message, rules) ?? GENERAL_SECTION;
}

/** Drops a redundant `"<path>: "` prefix so the issue list can show the path separately. */
export function displayMessage(issue: Pick<EditorIssue, "path" | "message">): string {
  if (!issue.path) return issue.message;
  const prefix = `${issue.path}:`;
  const trimmed = issue.message.trim();
  if (trimmed.startsWith(prefix)) {
    const rest = trimmed.slice(prefix.length).trim();
    return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : trimmed;
  }
  return trimmed;
}

function stringIssues(
  messages: readonly string[] | undefined,
  severity: IssueSeverity,
  rules: readonly SectionIssueRule[],
  offset: number,
): EditorIssue[] {
  return (messages ?? []).map((message, index) => {
    const path = leadingPath(message);
    return {
      key: `server-${severity}-${offset + index}`,
      path,
      message,
      severity,
      section: classifyIssue(path, message, rules),
      source: "server" as const,
    };
  });
}

/** Issues from `POST /v1/agents/{id}/validate` (or a 422's `details`). */
export function issuesFromValidation(
  result: Partial<ValidationResult> | null | undefined,
  rules: readonly SectionIssueRule[],
): EditorIssue[] {
  if (!result) return [];
  if (result.issues && result.issues.length > 0) {
    return result.issues.map((issue, index) => {
      const path = issue.path ? normalizeIssuePath(issue.path) : null;
      return {
        key: `server-issue-${index}`,
        path,
        message: issue.message,
        severity: issue.severity ?? "error",
        section: classifyIssue(path, issue.message, rules),
        source: "server" as const,
      };
    });
  }
  return [
    ...stringIssues(result.errors, "error", rules, 0),
    ...stringIssues(result.warnings, "warning", rules, result.errors?.length ?? 0),
  ];
}

/**
 * Issues carried by a failed save: the api answers an invalid config with
 * 422 `{error: {details: {errors, warnings, issues?}}}`. Returns null when
 * the error is not of that shape (the caller then shows a toast).
 */
export function issuesFromApiError(error: unknown, rules: readonly SectionIssueRule[]): EditorIssue[] | null {
  if (!(error instanceof ApiError)) return null;
  const details = error.details;
  if (typeof details !== "object" || details === null) return null;
  const record = details as Partial<ValidationResult>;
  const hasAny =
    (Array.isArray(record.errors) && record.errors.length > 0) ||
    (Array.isArray(record.warnings) && record.warnings.length > 0) ||
    (Array.isArray(record.issues) && record.issues.length > 0);
  if (!hasAny) return null;
  return issuesFromValidation(record, rules);
}

/** Flattens a react-hook-form error tree into `{path, message}` leaves in field order. */
export function flattenFieldErrors(node: unknown, prefix: string[] = []): { path: string; message: string }[] {
  if (node === null || typeof node !== "object") return [];
  const record = node as Record<string, unknown>;
  if (typeof record.message === "string" && (typeof record.type === "string" || prefix.length > 0)) {
    return [{ path: prefix.join("."), message: record.message }];
  }
  const out: { path: string; message: string }[] = [];
  for (const [key, value] of Object.entries(record)) {
    if (key === "ref") continue;
    out.push(...flattenFieldErrors(value, key === "root" && prefix.length === 0 ? prefix : [...prefix, key]));
  }
  return out;
}

/** Client-side (zod) errors as issues, so they get the same dots and lists as server issues. */
export function issuesFromFieldErrors(errors: unknown, rules: readonly SectionIssueRule[]): EditorIssue[] {
  return flattenFieldErrors(errors).map(({ path, message }, index) => {
    const normalized = path ? normalizeIssuePath(path) : null;
    return {
      key: `client-${index}-${path}`,
      path: normalized,
      message,
      severity: "error" as const,
      section: normalized ? (sectionForPath(normalized, rules) ?? GENERAL_SECTION) : GENERAL_SECTION,
      source: "client" as const,
    };
  });
}

export interface SectionIssueSummary {
  errors: number;
  warnings: number;
}

/** Counts per section id (general included). */
export function summarizeIssues(issues: readonly EditorIssue[]): Record<string, SectionIssueSummary> {
  const out: Record<string, SectionIssueSummary> = {};
  for (const issue of issues) {
    const entry = (out[issue.section] ??= { errors: 0, warnings: 0 });
    if (issue.severity === "error") entry.errors += 1;
    else entry.warnings += 1;
  }
  return out;
}

/** The nav dot for a section: errors beat warnings. */
export function sectionTone(summary: SectionIssueSummary | undefined): "error" | "warning" | null {
  if (!summary) return null;
  if (summary.errors > 0) return "error";
  if (summary.warnings > 0) return "warning";
  return null;
}

/** First section (in nav order) that has an error, then one with a warning. */
export function firstSectionWithIssues(
  issues: readonly EditorIssue[],
  sectionOrder: readonly string[],
  severity: IssueSeverity = "error",
): string | null {
  for (const id of sectionOrder) {
    if (issues.some((issue) => issue.section === id && issue.severity === severity)) return id;
  }
  return null;
}

/**
 * The form field an issue points at (`pipeline.stt` → `config.pipeline.stt`), for focusing.
 *
 * V5-48: `tools.apps.mode`, `tools.apps.router.manage_connections` and
 * `tools[i].definition.*` (`apps_issues`, docs/v5/COMPOSIO.md §4) need no
 * entry in the allowlist below — their top segment is `tools`, not the
 * top-level `AgentOut.mode`, so they already fall through to the
 * `config.`-prefixed branch and land on `config.tools.apps.mode` etc., the
 * exact field name the Connected apps card binds
 * (`connected-apps-card.tsx`'s `data-issue-path="tools.apps.mode"` is the
 * DOM fallback `focusFieldFor` uses when `setFocus` can't reach a
 * `Select`). `sectionForPath` also needs no new rule: `builtin-sections.tsx`
 * already declares `"tools"` as an issue-path prefix for the Tools tab, and
 * `tools.apps.mode` starts with `"tools."`.
 */
export function formPathForIssue(path: string | null): string | null {
  if (!path) return null;
  const top = path.split(".")[0];
  if (["name", "description", "ui_panel_id", "mode", "limits", "allowed_origins", "connection_id"].includes(top)) {
    return path;
  }
  return `config.${path}`;
}

/** Error and warning messages of a validation result, from `issues[]` when present. */
export function validationMessages(result: Partial<ValidationResult> | null | undefined): {
  errors: string[];
  warnings: string[];
} {
  if (!result) return { errors: [], warnings: [] };
  if (result.issues && result.issues.length > 0) {
    return {
      errors: result.issues.filter((issue) => (issue.severity ?? "error") === "error").map((issue) => issue.message),
      warnings: result.issues.filter((issue) => issue.severity === "warning").map((issue) => issue.message),
    };
  }
  return { errors: result.errors ?? [], warnings: result.warnings ?? [] };
}
