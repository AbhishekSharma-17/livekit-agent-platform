import * as React from "react";

import { friendlyError, type FriendlyErrorContext } from "@/lib/friendly-error";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * Section or page error (docs/ui/DESIGN-SYSTEM.md sections 6.5 and 8.4): a
 * danger alert at the top of the affected region that says what happened and
 * what to do next, with Retry when the action is retryable. Pass `error` (any
 * thrown value, mapped through `friendlyError`) or a ready `message`.
 *
 * The console must degrade gracefully when the api isn't running, so every
 * data-driven page renders this instead of crashing.
 */
export function ErrorBanner({
  message,
  error,
  context,
  title,
  onRetry,
  retryLabel = "Retry",
  className,
}: {
  /** Ready-made copy. Prefer `error`, which is mapped to plain words. */
  message?: string;
  /** Any thrown value; mapped through `friendlyError`. */
  error?: unknown;
  context?: FriendlyErrorContext;
  /** Optional bold title, e.g. "Couldn't load agents". Defaults from `context.action`. */
  title?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) {
  const friendly = error !== undefined ? friendlyError(error, context) : null;
  const text = message ?? friendly?.message ?? "Something went wrong. Try again in a moment.";
  const heading = title ?? (context?.action && friendly ? friendly.title : undefined);
  return (
    <Alert
      tone="danger"
      title={heading}
      className={className}
      actions={
        onRetry ? (
          <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : undefined
      }
    >
      {text}
    </Alert>
  );
}

/**
 * The person-facing text for any thrown value: plain words plus a next step,
 * never vendor text, status phrases, stack traces or ids (see
 * `src/lib/friendly-error.ts`). The raw detail is on `friendlyError(e).raw`
 * for logs and development only.
 */
export function errorMessage(error: unknown): string {
  return friendlyError(error).message;
}
