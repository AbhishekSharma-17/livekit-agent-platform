import * as React from "react";

/**
 * `SessionOut.usage` rendered as label/value pairs, never raw JSON
 * (docs/UI_UX_SPEC.md §4.10). `usage` is an untyped dict (CONTRACTS §7): the
 * worker stores the SDK's `AgentSessionUsage` dump, which today is
 * `{model_usage: [{type, provider, model, …counters}]}` and used to be a flat
 * dict of counters. Scalars become pairs; a list of objects becomes a
 * sub-list of provider/model rows; zero-valued counters are left out (most of
 * the SDK's ~17 counters are 0 for any one model).
 */

const ACRONYMS: Record<string, string> = {
  llm: "LLM",
  tts: "TTS",
  stt: "STT",
  vad: "VAD",
  eou: "EOU",
  eot: "EOT",
  ttft: "TTFT",
  ttfb: "TTFB",
  id: "ID",
  api: "API",
  usd: "USD",
  ms: "ms",
};

/** `llm_completion_tokens` → "LLM completion tokens" (sentence case, acronyms kept). */
export function formatUsageLabel(key: string): string {
  const words = key
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (ACRONYMS[lower]) return ACRONYMS[lower];
      return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
    });
  return words.join(" ");
}

/** Scalars only: integers grouped, decimals to 2 places, `—` for null; nested values are the caller's job. */
export function formatUsageValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(2);
  }
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}

/** Durations carry seconds in the SDK dump (`audio_duration`, `session_duration`). */
function formatUsageField(key: string, value: unknown): string {
  if (typeof value === "number" && /duration$/.test(key)) return `${value.toFixed(1)} s`;
  return formatUsageValue(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function isEmptyValue(value: unknown): boolean {
  return value === 0 || value === null || value === undefined || value === "";
}

export interface UsagePair {
  key: string;
  label: string;
  value: string;
}

export interface UsageGroup {
  key: string;
  label: string;
  rows: { key: string; title: string; kind: string | null; pairs: UsagePair[] }[];
}

const IDENTITY_KEYS = new Set(["type", "provider", "model"]);

function pairsOf(record: Record<string, unknown>, skip: ReadonlySet<string> = IDENTITY_KEYS): UsagePair[] {
  const pairs: UsagePair[] = [];
  for (const [key, value] of Object.entries(record)) {
    if (skip.has(key) || isEmptyValue(value)) continue;
    if (isScalar(value)) {
      pairs.push({ key, label: formatUsageLabel(key), value: formatUsageField(key, value) });
    } else if (isRecord(value)) {
      for (const [inner, innerValue] of Object.entries(value)) {
        if (isScalar(innerValue) && !isEmptyValue(innerValue)) {
          pairs.push({
            key: `${key}.${inner}`,
            label: `${formatUsageLabel(key)} · ${formatUsageLabel(inner).toLowerCase()}`,
            value: formatUsageField(inner, innerValue),
          });
        }
      }
    }
  }
  return pairs;
}

/** "llm_usage" → "LLM". */
function usageKindLabel(type: unknown): string | null {
  if (typeof type !== "string" || type.length === 0) return null;
  return formatUsageLabel(type.replace(/_usage$/, ""));
}

/** Split a usage dict into top-level pairs and list groups (e.g. `model_usage`). */
export function describeUsage(usage: Record<string, unknown> | null | undefined): {
  pairs: UsagePair[];
  groups: UsageGroup[];
} {
  const pairs: UsagePair[] = [];
  const groups: UsageGroup[] = [];
  if (!usage) return { pairs, groups };

  for (const [key, value] of Object.entries(usage)) {
    if (Array.isArray(value)) {
      const rows = value.filter(isRecord).map((item, index) => {
        const identity = [item.provider, item.model].filter((part): part is string => typeof part === "string" && part.length > 0);
        return {
          key: `${key}-${index}`,
          title: identity.join(" · ") || `Entry ${index + 1}`,
          kind: usageKindLabel(item.type),
          pairs: pairsOf(item),
        };
      });
      if (rows.length > 0) groups.push({ key, label: formatUsageLabel(key), rows });
    } else if (isRecord(value)) {
      const nested = pairsOf(value, new Set());
      if (nested.length > 0) {
        groups.push({ key, label: formatUsageLabel(key), rows: [{ key, title: formatUsageLabel(key), kind: null, pairs: nested }] });
      }
    } else if (isScalar(value) && !isEmptyValue(value)) {
      pairs.push({ key, label: formatUsageLabel(key), value: formatUsageField(key, value) });
    }
  }
  return { pairs, groups };
}

/** The list groups of `describeUsage` (the stats strip renders the scalar pairs itself). */
export function UsageGroups({ groups }: { groups: UsageGroup[] }) {
  if (groups.length === 0) return null;
  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <div key={group.key}>
          <h3 className="mb-2 text-xs font-medium text-muted-foreground">{group.label}</h3>
          <ul className="divide-y divide-border rounded-md border border-border">
            {group.rows.map((row) => (
              <li key={row.key} className="px-3 py-2.5">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {row.kind ? (
                    <span className="text-[0.6875rem] font-medium tracking-[0.02em] text-muted-foreground">{row.kind}</span>
                  ) : null}
                  <span className="min-w-0 font-mono text-[0.8125rem] break-all text-foreground">{row.title}</span>
                </div>
                {row.pairs.length > 0 ? (
                  <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1">
                    {row.pairs.map((pair) => (
                      <div key={pair.key} className="flex items-baseline gap-1.5">
                        <dt className="text-xs text-muted-foreground">{pair.label}</dt>
                        <dd className="font-mono text-xs tabular-nums text-foreground">{pair.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="mt-1 text-xs text-muted-foreground">No usage counted.</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
