/**
 * Narrowing of `UiState.custom` for the insurance claim pack.
 *
 * `custom` is pack-defined (CONTRACTS §10) and validated server-side against
 * `PackManifest.state_schema`; the panel still parses defensively so a partial
 * or older snapshot degrades field-by-field instead of blanking the paper.
 * Every member is optional and every parse falls back to a default — the panel
 * never throws on state it does not recognise.
 */
import { z } from "zod";

/** One "claim details" row (`custom.fields.*`). */
const fieldSchema = z.object({
  label: z.string().catch(""),
  value: z.string().catch(""),
  status: z.enum(["missing", "complete", "urgent"]).catch("complete"),
  source: z.string().catch("-"),
});

/** One row of `rules.generate_document_checklist`. */
const documentSchema = z.object({
  item: z.string().catch(""),
  reason: z.string().catch(""),
  priority: z.string().catch("required"),
  already_provided: z.boolean().catch(false),
});

/** `custom.sketch` — the pen sketch the agent asks the claimant to confirm. */
const sketchSchema = z.object({
  asset_id: z.string(),
  version: z.number().catch(1),
  brief: z.string().catch(""),
  confirmed: z.boolean().catch(false),
});

/** The verified policy record from the mock directory. */
const policySchema = z.object({
  found: z.boolean().catch(false),
  policy_number: z.string().catch(""),
  policyholder_name: z.string().catch(""),
  policy_line: z.string().catch(""),
  status: z.string().catch(""),
  notes: z.array(z.string()).catch([]),
});

const customSchema = z.object({
  fields: z.record(z.string(), fieldSchema).catch({}),
  route: z.string().nullish().catch(null),
  documents: z.array(documentSchema).catch([]),
  handoff: z.record(z.string(), z.string()).catch({}),
  packet_markdown: z.string().catch(""),
  policy: policySchema.nullish().catch(null),
  sketch: sketchSchema.nullish().catch(null),
  claim_type: z.string().nullish().catch(null),
  severity: z.string().nullish().catch(null),
});

export type NotebookField = z.infer<typeof fieldSchema>;
export type NotebookDocument = z.infer<typeof documentSchema>;
export type NotebookSketch = z.infer<typeof sketchSchema>;
export type NotebookCustom = z.infer<typeof customSchema>;

const EMPTY: NotebookCustom = {
  fields: {},
  route: null,
  documents: [],
  handoff: {},
  packet_markdown: "",
  policy: null,
  sketch: null,
  claim_type: null,
  severity: null,
};

/**
 * Parse the pack-defined half of the envelope.
 *
 * @param custom - `UiState.custom` as delivered by the agent.
 * @returns The recognised slots, with defaults for anything missing or malformed.
 */
export function parseNotebookCustom(
  custom: Record<string, unknown> | null | undefined,
): NotebookCustom {
  if (!custom) return EMPTY;
  const parsed = customSchema.safeParse(custom);
  return parsed.success ? parsed.data : EMPTY;
}

/** Title-cased claim type for the page header, e.g. `auto collision`. */
export function headerLine(custom: NotebookCustom): string {
  const parts = [custom.claim_type, custom.severity].filter(
    (part): part is string => Boolean(part),
  );
  return parts.length > 0 ? parts.join(" · ") : "New claim";
}
