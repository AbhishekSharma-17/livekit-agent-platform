/**
 * Caller-facing call-start errors (docs/ui/DESIGN-SYSTEM.md sections 1, 3 and
 * 8.4): what happened, in plain words, and what to do next. Never the api's
 * internal text, vendor messages or configuration names.
 *
 * The console's `friendlyError()` (`components/console/lib/friendly-error.ts`)
 * can't be used here: nothing under `components/console/**` may reach the
 * `/s/[slug]` bundle (`tests/flow-bundle-split.test.ts`). This is the session's
 * own, much smaller map over the connect call's `ConnectError`.
 *
 * Two answers pass through on purpose:
 * - `no_worker_running` (V6-27, ask #181): the api already wrote the message
 *   for its audience. A public caller gets "This agent can't take calls right
 *   now…" with no detail; a builder in test mode gets the connection's name.
 * - `session_closed`: "This session has ended." is already plain.
 *
 * In test mode (`?mode=test`, a builder from the console) everything else keeps
 * today's `describeConnectError()` text, which a builder can act on.
 */
import { ConnectError, describeConnectError } from "@/lib/livekit";

export const CALLER_ERROR = {
  cantTakeCalls: "This agent can't take calls right now. Please try again in a few minutes.",
  busy: "All lines are busy right now. Try again in a moment.",
  tooMany: "There are too many calls right now. Wait a moment, then try again.",
  server: "Something went wrong on our side. Try again in a moment.",
  /** Same words as `describeConnectError`'s network failure. */
  unreachable: "Could not reach the agent service. Please try again in a moment.",
  generic: "We couldn't start the call. Try again in a moment.",
} as const;

export interface CallerErrorOptions {
  /** `?mode=test`: the caller is a builder, who gets the api's own text. */
  testMode?: boolean;
}

/** The sentence to show a caller for a failed connect / start. */
export function callerConnectError(cause: unknown, { testMode = false }: CallerErrorOptions = {}): string {
  // Network failures, 403 (not published) and 404 (no agent) already have
  // plain, fixed copy in `describeConnectError`.
  if (!(cause instanceof ConnectError) || cause.isForbidden || cause.isNotFound) {
    return describeConnectError(cause);
  }
  switch (cause.code) {
    case "no_worker_running":
      return cause.message.trim() || CALLER_ERROR.cantTakeCalls;
    case "session_closed":
      return describeConnectError(cause);
    case "calls_busy":
      return CALLER_ERROR.busy;
    case "rate_limited":
      return CALLER_ERROR.tooMany;
  }
  if (testMode) return describeConnectError(cause);
  // `status === 0` is a client-side failure (for example a missing api base
  // URL): the caller can only wait, so it reads like the service being down.
  if (cause.status === 0) return CALLER_ERROR.unreachable;
  if (cause.status === 429) return CALLER_ERROR.tooMany;
  if (cause.status >= 500) return CALLER_ERROR.server;
  return CALLER_ERROR.generic;
}
