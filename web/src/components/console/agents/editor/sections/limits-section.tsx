"use client";

import * as React from "react";
import { Controller, useFormContext, type FieldPath } from "react-hook-form";
import { XIcon } from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { ORIGIN_PATTERN, type AgentEditorForm } from "@/components/console/lib/schemas";
import { formatDuration } from "@/lib/format";

import { describedBy } from "./field-aria";

type LimitKey = keyof AgentEditorForm["limits"];

interface LimitFieldSpec {
  key: LimitKey;
  label: string;
  hint: string;
  unit?: string;
  min: number;
}

const LIMIT_FIELDS: LimitFieldSpec[] = [
  {
    key: "max_concurrent_sessions",
    label: "Calls at the same time",
    hint: "Further callers are turned away until a call ends.",
    min: 1,
  },
  {
    key: "max_session_duration_s",
    label: "Longest call",
    hint: "The call ends when it reaches this length.",
    unit: "seconds",
    min: 60,
  },
  {
    key: "rate_per_ip_per_min",
    label: "New calls per minute from one address",
    hint: "Counted per caller IP address.",
    unit: "per min",
    min: 1,
  },
  {
    key: "rate_per_agent_per_min",
    label: "New calls per minute in total",
    hint: "Across every caller of this agent.",
    unit: "per min",
    min: 1,
  },
];

/**
 * Limits section (UI_UX_SPEC-V2-AMENDMENTS §2.3): concurrency, max duration,
 * rate limits, allowed origins (chips). Edits the top-level `limits`
 * (`AgentLimits`) and `allowed_origins` of the agent (CONTRACTS-V2 §3.3).
 */
export function LimitsSection() {
  const { control, formState, watch } = useFormContext<AgentEditorForm>();
  const limitErrors = formState.errors.limits;
  const duration = watch("limits.max_session_duration_s");

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="limits-settings"
        title="Limits"
        description="Guard rails applied when a call starts. They protect your provider quotas from a shared link."
      >
        <SectionRow className="grid gap-5 md:grid-cols-2">
          {LIMIT_FIELDS.map((spec) => {
            const id = `limits-${spec.key}`;
            const error = limitErrors?.[spec.key]?.message;
            const hint =
              spec.key === "max_session_duration_s" && Number.isFinite(duration) && duration > 0
                ? `${spec.hint} (${formatDuration(duration * 1000)})`
                : spec.hint;
            return (
              <Field key={spec.key} label={spec.label} htmlFor={id} hint={hint} error={error}>
                <Controller
                  control={control}
                  name={`limits.${spec.key}` as FieldPath<AgentEditorForm>}
                  render={({ field }) => (
                    <InputGroup className="max-w-56">
                      <InputGroupInput
                        id={id}
                        type="number"
                        inputMode="numeric"
                        min={spec.min}
                        value={typeof field.value === "number" && Number.isFinite(field.value) ? field.value : ""}
                        onBlur={field.onBlur}
                        onChange={(event) =>
                          field.onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))
                        }
                        aria-invalid={error ? true : undefined}
                        aria-describedby={describedBy(id, Boolean(error))}
                        data-issue-path={`limits.${spec.key}`}
                      />
                      {spec.unit ? (
                        <InputGroupAddon align="inline-end">
                          <InputGroupText>{spec.unit}</InputGroupText>
                        </InputGroupAddon>
                      ) : null}
                    </InputGroup>
                  )}
                />
              </Field>
            );
          })}
        </SectionRow>
      </Section>

      <Section
        id="allowed-origins"
        title="Websites that can start calls"
        description="Other websites allowed to start calls with this agent."
      >
        <SectionRow>
          <Controller
            control={control}
            name="allowed_origins"
            render={({ field }) => (
              <OriginsEditor
                value={field.value}
                onChange={field.onChange}
                error={formState.errors.allowed_origins?.message ?? firstItemError(formState.errors.allowed_origins)}
              />
            )}
          />
        </SectionRow>
      </Section>
    </div>
  );
}

function firstItemError(node: unknown): string | undefined {
  if (!Array.isArray(node)) return undefined;
  for (const item of node) {
    if (item && typeof item === "object" && typeof (item as { message?: unknown }).message === "string") {
      return (item as { message: string }).message;
    }
  }
  return undefined;
}

/** Chips + an input; Enter or "Add" appends a normalised origin. */
export function OriginsEditor({
  value,
  onChange,
  error,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  error?: string;
}) {
  const [draft, setDraft] = React.useState("");
  const [draftError, setDraftError] = React.useState<string | null>(null);
  const id = "allowed-origins-input";

  function add() {
    const candidate = draft.trim().replace(/\/+$/, "");
    if (!candidate) return;
    if (!ORIGIN_PATTERN.test(candidate)) {
      setDraftError("Use an origin like https://example.com (no path), or * for any site.");
      return;
    }
    const normalized = candidate === "*" ? "*" : candidate.toLowerCase();
    if (!value.includes(normalized)) onChange([...value, normalized]);
    setDraft("");
    setDraftError(null);
  }

  const shownError = draftError ?? error;

  return (
    <div className="flex flex-col gap-3">
      <Field
        label="Add a website"
        htmlFor={id}
        hint="A page on another site must be listed here to start calls, for example through an embedded widget. Add * to allow any site."
        error={shownError ?? undefined}
      >
        <div className="flex max-w-md gap-2">
          <Input
            id={id}
            value={draft}
            placeholder="https://example.com"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-[0.8125rem]"
            onChange={(event) => {
              setDraft(event.target.value);
              if (draftError) setDraftError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
            aria-invalid={shownError ? true : undefined}
            aria-describedby={describedBy(id, Boolean(shownError))}
            data-issue-path="allowed_origins"
          />
          <Button type="button" variant="outline" onClick={add} disabled={draft.trim() === ""}>
            Add
          </Button>
        </div>
      </Field>
      {value.length > 0 ? (
        <ul aria-label="Allowed websites" className="flex flex-wrap gap-2">
          {value.map((origin) => (
            <li
              key={origin}
              className="inline-flex h-7 items-center gap-1 rounded-xs border border-border bg-muted pr-0.5 pl-2 font-mono text-xs"
            >
              <span className="max-w-64 truncate">{origin === "*" ? "* (any site)" : origin}</span>
              <button
                type="button"
                onClick={() => onChange(value.filter((item) => item !== origin))}
                aria-label={`Remove ${origin}`}
                className="inline-flex size-6 items-center justify-center rounded-xs text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon as={XIcon} size="sm" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[0.8125rem] text-muted-foreground">No other websites can start calls.</p>
      )}
    </div>
  );
}
