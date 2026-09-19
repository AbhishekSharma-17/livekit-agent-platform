import * as React from "react";

import { CircleAlertIcon } from "lucide-react";

import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/shared/icon";

/**
 * Section/page error (docs/UI_UX_SPEC.md §6, channel 2): a `danger-soft`
 * alert at the top of the affected region, with a retry when the action is
 * retryable. The console must degrade gracefully when the api isn't running,
 * so every data-driven page renders this instead of crashing.
 */
export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="danger" className={onRetry ? "items-center" : undefined}>
      <Icon as={CircleAlertIcon} size="md" />
      <AlertDescription>{message}</AlertDescription>
      {onRetry ? (
        <AlertAction className="top-1/2 -translate-y-1/2">
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Try again
          </Button>
        </AlertAction>
      ) : null}
    </Alert>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
