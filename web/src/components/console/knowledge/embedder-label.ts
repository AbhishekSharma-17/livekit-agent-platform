/**
 * Human labels for `KbOut.embedder_id` (docs/UI_UX_SPEC.md §1.5, §7.7: "Raw
 * embedder id `fastembed-embedding` as a column value" is an H-severity
 * finding; the list/detail columns must show "Local (fastembed)" / "OpenAI").
 *
 * `embedder_id` isn't looked up from `GET /v1/providers` here on purpose: the
 * knowledge pages must render a readable label even when that request hasn't
 * resolved yet (or fails), and the embedding provider catalogue only ever
 * has these two ids (`api/src/lkap_api/kb/seed.py`,
 * `api/src/lkap_api/routers/credentials.py`). Unknown ids (a future
 * embedder) fall back to the raw id rather than crashing.
 */
const EMBEDDER_LABELS: Record<string, string> = {
  "fastembed-embedding": "Local (fastembed)",
  "openai-embedding": "OpenAI",
};

const EMBEDDER_HELP: Record<string, string> = {
  "fastembed-embedding": "Runs on the api, no key needed. The first upload downloads a small local model.",
  "openai-embedding": "Uses your OpenAI credential; embeddings are sent to OpenAI.",
};

export function embedderLabel(embedderId: string): string {
  return EMBEDDER_LABELS[embedderId] ?? embedderId;
}

export function embedderHelp(embedderId: string): string | undefined {
  return EMBEDDER_HELP[embedderId];
}

/** Ordered choices for the create-knowledge-base radio cards. */
export const EMBEDDER_CHOICES = Object.keys(EMBEDDER_LABELS);
