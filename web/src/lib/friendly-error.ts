/**
 * Friendly errors (docs/ui/DESIGN-SYSTEM.md sections 1, 3 and 8.4; docs/ui/AUDIT.md finding S1).
 *
 * Every error a person sees goes through `friendlyError()`: it says what
 * happened in plain words and what to do next. It never shows vendor text,
 * stack traces, HTTP status phrases or internal ids. The raw detail stays on
 * the returned object (`raw`) for logs and development only.
 *
 * Resolution order:
 *   1. network failures and aborted or timed-out requests;
 *   2. the api's own error codes (`CODE_COPY`), written here in plain words;
 *   3. other 4xx answers (validation, conflicts): the api's authored sentence
 *      when it reads as plain copy (`isPresentable`), otherwise a status sentence;
 *   4. auth, not-found and server statuses: fixed copy;
 *   5. errors thrown by console code (plain `Error`): the message when it reads
 *      as plain copy, otherwise a generic sentence.
 *
 * Pure, and free of console code, so both surfaces can import it: the console
 * and sign-in use `friendlyError()`; the caller page (`/s/[slug]`, whose
 * bundle must not reach `components/console/**`, `tests/flow-bundle-split.test.ts`)
 * borrows single sentences through `codeMessage()` / `statusMessage()` where
 * they fit a caller (`components/session/caller-error.ts`).
 */
import { ApiError } from "@/lib/api";

/** What the person can do next; screens use it to pick an action (Retry, Sign in …). */
export type ErrorNextAction = "retry" | "check-connection" | "sign-in" | "ask-admin" | "fix-input" | "refresh" | "none";

export interface FriendlyError {
  /** A short heading: "Couldn't load agents" (with `context.action`) or the plain summary. */
  title: string;
  /** What happened, one plain sentence. */
  detail: string;
  /** What to do next, one sentence (or `null` when there is nothing to do). */
  nextStep: string | null;
  /** `detail` and `nextStep` joined: the text to show. */
  message: string;
  action: ErrorNextAction;
  /** Whether a Retry button makes sense. */
  retryable: boolean;
  status?: number;
  code?: string;
  /** The raw error text, for logs and development only. Never render it. */
  raw: string;
}

export interface FriendlyErrorContext {
  /** The verb phrase that failed, e.g. "load agents" → title "Couldn't load agents". */
  action?: string;
}

interface Copy {
  detail: string;
  nextStep: string | null;
  action: ErrorNextAction;
}

const RETRY_SOON = "Try again in a moment.";

const NETWORK: Copy = {
  detail: "We couldn't reach the server.",
  nextStep: "Check your connection and try again.",
  action: "check-connection",
};

const GENERIC: Copy = { detail: "Something went wrong.", nextStep: RETRY_SOON, action: "retry" };

/** The api's specific error codes (api/src/lkap_api), in plain words. */
const CODE_COPY: Record<string, Copy> = {
  network_error: NETWORK,
  rate_limited: { detail: "There are too many requests right now.", nextStep: "Wait a moment and try again.", action: "retry" },
  payload_too_large: { detail: "That is too large to upload.", nextStep: "Try a smaller file.", action: "fix-input" },
  quota_exceeded: {
    detail: "This workspace has reached its limit.",
    nextStep: "Remove something you no longer need, or ask an admin to raise the limit.",
    action: "ask-admin",
  },
  not_live: { detail: "This session isn't live any more.", nextStep: "Refresh to see its latest state.", action: "refresh" },
  no_agent: { detail: "No agent is in the room yet.", nextStep: "Wait for the agent to join, then try again.", action: "retry" },
  agent_name_in_use: { detail: "That name is already taken.", nextStep: "Choose a different name.", action: "fix-input" },
  agent_busy: { detail: "This agent is busy with another change.", nextStep: RETRY_SOON, action: "retry" },
  calls_busy: { detail: "All lines are busy right now.", nextStep: RETRY_SOON, action: "retry" },
  no_worker_running: {
    detail: "No worker is running for this agent.",
    nextStep: "Start a worker on the agent's connection, then try again.",
    action: "none",
  },
  token_unavailable: { detail: "We couldn't open access for this session.", nextStep: RETRY_SOON, action: "retry" },
  livekit_error: {
    detail: "LiveKit didn't accept the request.",
    nextStep: "Check the connection's settings and try again.",
    action: "retry",
  },
  tool_provider_unauthorized: {
    detail: "The connected app didn't accept our sign-in.",
    nextStep: "Reconnect the app and try again.",
    action: "none",
  },
  tool_provider_error: { detail: "The connected app returned an error.", nextStep: RETRY_SOON, action: "retry" },
  destination_not_allowed: {
    detail: "That address isn't allowed.",
    nextStep: "Use a public web address.",
    action: "fix-input",
  },
  apps_not_enabled: {
    detail: "Connected apps aren't turned on for this workspace.",
    nextStep: "Ask an admin to turn them on.",
    action: "ask-admin",
  },
  phone_numbers_unavailable: {
    detail: "Phone numbers aren't available on this connection.",
    nextStep: "Check the connection's telephony settings.",
    action: "none",
  },
  memory_unavailable: { detail: "Memory isn't available right now.", nextStep: RETRY_SOON, action: "retry" },
  qa_judge_unavailable: { detail: "Automatic review isn't available right now.", nextStep: "Try again later.", action: "retry" },
  vector_store_misconfigured: {
    detail: "Knowledge search isn't set up correctly.",
    nextStep: "Ask an admin to check the storage settings.",
    action: "ask-admin",
  },
  kb_embedder_mismatch: {
    detail: "This knowledge base was built with a different embedding model.",
    nextStep: "Re-index the knowledge base, then try again.",
    action: "none",
  },
  kb_error: { detail: "Knowledge search didn't work this time.", nextStep: RETRY_SOON, action: "retry" },
  kb_timeout: { detail: "Knowledge search took too long.", nextStep: RETRY_SOON, action: "retry" },
  kb_not_found: { detail: "We couldn't find that knowledge base.", nextStep: "It may have been deleted.", action: "refresh" },
  rerank_failed: { detail: "Re-ranking the results didn't work.", nextStep: RETRY_SOON, action: "retry" },
  rerank_refused: { detail: "The re-ranking service refused the request.", nextStep: "Check its key and try again.", action: "none" },
  lexical_unavailable: { detail: "Keyword search isn't available right now.", nextStep: RETRY_SOON, action: "retry" },
  vault_error: { detail: "We couldn't read the stored secret.", nextStep: "Enter the key again, then retry.", action: "fix-input" },
  unsupported_media_type: { detail: "That file type isn't supported.", nextStep: "Choose a different file.", action: "fix-input" },
  invalid_dataset: { detail: "That dataset can't be used.", nextStep: "Check the file's columns and try again.", action: "fix-input" },
  tests_failing: { detail: "Some tests are failing.", nextStep: "Fix the failing tests, then try again.", action: "none" },
  not_implemented: { detail: "This isn't available yet.", nextStep: null, action: "none" },
};

const STATUS_COPY: Record<number, Copy> = {
  400: { detail: "Some of the details aren't valid.", nextStep: "Check them and try again.", action: "fix-input" },
  401: { detail: "Your session has ended.", nextStep: "Sign in again.", action: "sign-in" },
  403: { detail: "You don't have permission to do this.", nextStep: "Ask an admin for access.", action: "ask-admin" },
  404: { detail: "We couldn't find that.", nextStep: "It may have been moved or deleted.", action: "refresh" },
  408: { detail: "The request took too long.", nextStep: RETRY_SOON, action: "retry" },
  409: { detail: "That conflicts with a recent change.", nextStep: "Refresh and try again.", action: "refresh" },
  413: CODE_COPY.payload_too_large,
  422: { detail: "Some of the details aren't valid.", nextStep: "Check them and try again.", action: "fix-input" },
  429: CODE_COPY.rate_limited,
  500: { detail: "Something went wrong on our side.", nextStep: RETRY_SOON, action: "retry" },
  502: { detail: "The service is unavailable right now.", nextStep: RETRY_SOON, action: "retry" },
  503: { detail: "The service is unavailable right now.", nextStep: RETRY_SOON, action: "retry" },
  504: { detail: "The service took too long to answer.", nextStep: RETRY_SOON, action: "retry" },
};

/**
 * Statuses whose api message is authored for the person (validation and
 * state conflicts). It is shown when it reads as plain copy; every other
 * status gets fixed copy.
 */
const FIXED_COPY_STATUSES = new Set([401, 403, 404, 408, 413, 429]);

const NETWORK_MESSAGE = /failed to fetch|fetch failed|networkerror|network error|network request failed|load failed/i;
/** Status phrases, vendor-ish and technical text that must never reach a person. */
const TECHNICAL = [
  /\b(?:internal server error|bad gateway|service unavailable|gateway time-?out|bad request|unprocessable entity|not found)\s*$/i,
  /^\s*[A-Za-z]*(?:Error|Exception)\b\s*[:(]/,
  /\bTraceback\b|\bstack\b|^\s*at\s|\n\s+at\s/i,
  /\bstatus(?: code)? \d{3}\b|\bHTTP\s?\d{3}\b|\b[45]\d\d\b(?=\s*[:-])/i,
  /[{}<>]|\[object /,
  /https?:\/\/|wss?:\/\//i,
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
  /\b[0-9a-f]{16,}\b/i,
  /\b[a-z]{2,8}_[A-Za-z0-9]{10,}\b/,
  /\b(?:undefined|null|NaN)\b/,
  /\b(?:sqlalchemy|psycopg|asyncpg|pydantic|aiohttp|httpx|grpc|errno|ECONNREFUSED|ETIMEDOUT|ENOTFOUND)\b/i,
];

/**
 * Whether `text` reads as plain, person-facing copy: short, one or two
 * sentences, with no ids, URLs, markup, status phrases or stack traces.
 */
export function isPresentable(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.length < 2 || trimmed.length > 240) return false;
  if (trimmed.split("\n").length > 2) return false;
  return !TECHNICAL.some((re) => re.test(trimmed));
}

/** Capitalise and end with a full stop, so an authored fragment reads as a sentence. */
export function asSentence(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (!trimmed) return trimmed;
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?…]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

function errorName(error: unknown): string | undefined {
  if (typeof error === "object" && error !== null && "name" in error) {
    const name = (error as { name?: unknown }).name;
    return typeof name === "string" ? name : undefined;
  }
  return undefined;
}

/** The raw text of any thrown value, for logs and development only. */
export function rawErrorDetail(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.details === undefined ? "" : ` ${safeJson(error.details)}`;
    return `ApiError ${error.status} ${error.code}: ${error.message}${details}`;
  }
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (typeof error === "string") return error;
  return safeJson(error);
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function sentence(copy: Copy): string {
  return copy.nextStep ? `${copy.detail} ${copy.nextStep}` : copy.detail;
}

/** The plain sentence (what happened, then what to do) for one of the api's error codes, or `null`. */
export function codeMessage(code: string): string | null {
  const copy = CODE_COPY[code];
  return copy ? sentence(copy) : null;
}

/** The plain sentence for an HTTP status with fixed copy (401, 404, 500 …), or `null`. */
export function statusMessage(status: number): string | null {
  const copy = STATUS_COPY[status];
  return copy ? sentence(copy) : null;
}

function copyFor(error: unknown): Copy {
  const name = errorName(error);
  if (name === "AbortError") return { detail: "The request was cancelled.", nextStep: "Try again.", action: "retry" };
  if (name === "TimeoutError") return STATUS_COPY[408];

  if (error instanceof ApiError) {
    if (error.status === 0) return NETWORK;
    const coded = CODE_COPY[error.code];
    if (coded) return coded;
    const authored = error.status >= 400 && error.status < 500 && !FIXED_COPY_STATUSES.has(error.status);
    if (authored && isPresentable(error.message)) {
      return { detail: asSentence(error.message), nextStep: null, action: error.status === 409 ? "refresh" : "fix-input" };
    }
    const byStatus = STATUS_COPY[error.status];
    if (byStatus) return byStatus;
    if (error.status >= 500) return STATUS_COPY[500];
    if (error.status >= 400) return STATUS_COPY[400];
    return GENERIC;
  }

  if (error instanceof TypeError && NETWORK_MESSAGE.test(error.message)) return NETWORK;
  if (error instanceof Error) {
    if (NETWORK_MESSAGE.test(error.message)) return NETWORK;
    if (isPresentable(error.message)) return { detail: asSentence(error.message), nextStep: null, action: "none" };
    return GENERIC;
  }
  if (typeof error === "string" && isPresentable(error)) {
    return { detail: asSentence(error), nextStep: null, action: "none" };
  }
  return GENERIC;
}

/**
 * Map any thrown value to plain copy plus a next step. `context.action` names
 * what failed ("load agents") and becomes the title ("Couldn't load agents").
 */
export function friendlyError(error: unknown, context: FriendlyErrorContext = {}): FriendlyError {
  const copy = copyFor(error);
  const message = sentence(copy);
  const title = context.action ? `Couldn't ${context.action}` : copy.detail.replace(/\.$/, "");
  return {
    title,
    detail: copy.detail,
    nextStep: copy.nextStep,
    message,
    action: copy.action,
    retryable: copy.action === "retry" || copy.action === "check-connection",
    status: error instanceof ApiError ? error.status : undefined,
    code: error instanceof ApiError ? error.code : undefined,
    raw: rawErrorDetail(error),
  };
}
