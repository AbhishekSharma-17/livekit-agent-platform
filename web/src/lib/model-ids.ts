/**
 * The console's mirror of the model-id rule (`lkap_contracts.providers`
 * `validate_model_id` / `validate_id_value`; docs/v4/CUSTOM-MODELS.md D-V4-23,
 * rulings R-V4-21, R-V4-31, R-V4-32).
 *
 * Why the console checks at all: the api refuses a bad id too, but the id of
 * `GET/PUT /v1/providers/{id}/models/{model_id}` travels in the URL path, so a
 * pasted key would reach the server's access log before the api could answer
 * 422. The console therefore runs this rule first and issues **no** request
 * (record read, catalog search, Test) for a value that fails it.
 *
 * Two field kinds (R-V4-31):
 * - `"model"` — a model id (`ProviderRef.model`, a `type="model"` field): a key
 *   prefix or a bare token is an error, then the syntax rule.
 * - `"id"` — a voice/avatar/catalog id (`ID_LIKE_FIELD_NAMES`, `type="catalog"`):
 *   a key prefix is an error, the syntax rule is an error, and a bare token is
 *   only a warning (a vendor's real id may be 32 hex characters).
 *
 * Every reason is value-free: nothing here ever repeats the value.
 */
import type { ModelIdRules } from "@/contracts/lkap-contracts";

/** The contract constants (`ModelIdRules()`'s defaults); `tests/model-ids.test.ts` pins them to `providers.json`. */
export const DEFAULT_MODEL_ID_RULES: Required<ModelIdRules> = {
  bare_token_min_len: 32,
  max_len: 200,
  pattern: "^(?![Hh][Tt][Tt][Pp])(?!.*://)[!$%()*+,\\-./0-9:;@A-Z\\[\\\\\\]^_a-z{|}~]{1,200}$",
  secret_prefixes: [
    "sk-",
    "sk_",
    "sk-or-",
    "sk-ant-",
    "sk_car_",
    "AIza",
    "xai-",
    "gsk_",
    "hf_",
    "AKIA",
    "ghp_",
    "github_pat_",
    "ya29.",
    "xi-",
  ],
};

/**
 * Field names whose values are ids (`lkap_contracts.providers.ID_LIKE_FIELD_NAMES`).
 * Not part of `ModelIdRules`, so it is copied here; dotted names
 * (`simli_config.face_id`) match on their last segment.
 */
export const ID_LIKE_FIELD_NAMES: ReadonlySet<string> = new Set([
  "voice",
  "voice_id",
  "avatar_id",
  "face_id",
  "pal_id",
  "persona_id",
  "voice_name",
  "emotion_id",
]);

/** The one reason a secret-looking model id gets (`SECRET_LOOKING_REASON`). */
export const SECRET_LOOKING_REASON = "looks like an API key, not a model id";
/** The warning an id-like field gets for a bare token (`BARE_TOKEN_ID_REASON`). */
export const BARE_TOKEN_ID_REASON = "looks like an API key; if it is the vendor's id, ignore this";

export type IdFieldKind = "model" | "id";

export interface IdIssue {
  severity: "error" | "warning";
  /** Value-free, lower-case sentence fragment ("looks like an API key, not a model id"). */
  reason: string;
}

const BARE_TOKEN_RE = /^[A-Za-z0-9+]+={0,2}$/;
const FORBIDDEN_CHARS = new Set(["?", "#", "&", "=", "<", ">", '"', "'", "`"]);

function rulesOf(rules: ModelIdRules | null | undefined): Required<ModelIdRules> {
  return {
    bare_token_min_len: rules?.bare_token_min_len ?? DEFAULT_MODEL_ID_RULES.bare_token_min_len,
    max_len: rules?.max_len ?? DEFAULT_MODEL_ID_RULES.max_len,
    pattern: rules?.pattern ?? DEFAULT_MODEL_ID_RULES.pattern,
    secret_prefixes: rules?.secret_prefixes ?? DEFAULT_MODEL_ID_RULES.secret_prefixes,
  };
}

/** Python's `str.isspace()` set, so `strip` and the whitespace check match the contract exactly. */
const PY_WHITESPACE = "\\t\\n\\u000b\\u000c\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_SPACE_RE = new RegExp(`[${PY_WHITESPACE}]`);
const PY_STRIP_RE = new RegExp(`^[${PY_WHITESPACE}]+|[${PY_WHITESPACE}]+$`, "g");

/** Python's `str.strip()`. */
function strip(value: string): string {
  return value.replace(PY_STRIP_RE, "");
}

function hasSecretPrefix(value: string, rules: Required<ModelIdRules>): boolean {
  const text = strip(value);
  return rules.secret_prefixes.some((prefix) => text.startsWith(prefix));
}

function isBareToken(value: string, rules: Required<ModelIdRules>): boolean {
  const text = strip(value);
  return Array.from(text).length >= rules.bare_token_min_len && BARE_TOKEN_RE.test(text);
}

function isWhitespaceOrControl(ch: string): boolean {
  const code = ch.charCodeAt(0);
  return PY_SPACE_RE.test(ch) || code < 0x20 || code === 0x7f;
}

/** Why `value` breaks the id syntax rule, or `null` (no secret check; value-free). */
function syntaxReason(value: string, rules: Required<ModelIdRules>): string | null {
  const chars = Array.from(value); // code points, like Python's `len`
  if (chars.length === 0) return "is empty";
  if (chars.length > rules.max_len) return `is longer than ${rules.max_len} characters`;
  if (chars.some(isWhitespaceOrControl)) return "contains whitespace or a control character";
  if (chars.some((ch) => (ch.codePointAt(0) ?? 0) > 0x7e)) return "contains a non-ASCII character";
  if (value.includes("://") || chars.slice(0, 4).join("").toLowerCase() === "http") return "looks like a URL, not a model id";
  if (chars.some((ch) => FORBIDDEN_CHARS.has(ch))) {
    return "contains a character model ids never use (one of ? # & = < > quotes or backtick)";
  }
  if (!new RegExp(rules.pattern).test(value)) return "is not a valid model id";
  return null;
}

/**
 * What is wrong with `value` as a model id (`field: "model"`) or as an id-like
 * field's value (`field: "id"`), or `null` when it passes.
 *
 * `rules` is `ProvidersResponse.model_id_rules` when the caller has it; the
 * contract defaults otherwise.
 */
export function validateModelId(
  value: string,
  { field = "model", rules }: { field?: IdFieldKind; rules?: ModelIdRules | null } = {},
): IdIssue | null {
  const r = rulesOf(rules);
  if (field === "model") {
    if (hasSecretPrefix(value, r) || isBareToken(value, r)) return { severity: "error", reason: SECRET_LOOKING_REASON };
    const reason = syntaxReason(value, r);
    return reason ? { severity: "error", reason } : null;
  }
  if (hasSecretPrefix(value, r)) return { severity: "error", reason: SECRET_LOOKING_REASON };
  const reason = syntaxReason(value, r);
  if (reason) return { severity: "error", reason };
  if (isBareToken(value, r)) return { severity: "warning", reason: BARE_TOKEN_ID_REASON };
  return null;
}

/** Whether `value` may be sent to the api as a model id (no error; R-V4-32). */
export function isSendableModelId(value: string | null | undefined, rules?: ModelIdRules | null): value is string {
  return typeof value === "string" && value !== "" && validateModelId(value, { field: "model", rules }) === null;
}

/** Whether a (possibly dotted) field name carries an id (`id_like_field`). */
export function isIdLikeField(name: string): boolean {
  const last = name.split(".").pop() ?? name;
  return ID_LIKE_FIELD_NAMES.has(last);
}

/**
 * A passing model id as URL path segments for `…/models/{model_id:path}`:
 * each `/`-separated part is encoded on its own, so `openai/gpt-4.1-mini`
 * keeps its slash (the console proxy re-joins segments) while `%`, `@`, `:`
 * and friends are escaped. Callers must check `isSendableModelId` first.
 */
export function modelIdPath(modelId: string): string {
  return modelId
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

/** "Can't use this id: it looks like an API key, not a model id." — the sentence the UI shows. */
export function idIssueSentence(issue: IdIssue, subject = "this id"): string {
  return `${issue.severity === "error" ? "Can't use" : "Check"} ${subject}: it ${issue.reason}.`;
}
