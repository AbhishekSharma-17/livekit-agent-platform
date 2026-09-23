"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { apiHealthEvents } from "@/components/console/lib/query-provider";

/**
 * docs/UI_UX_SPEC.md §6 "Offline / API down": one top `Alert` when three
 * consecutive queries fail with a network error or 5xx — never for a 4xx
 * with an error envelope (that is a real, scoped error, and today it also
 * covers endpoints that legitimately 404 because their package hasn't
 * shipped yet, e.g. `auth/me`, `connections`). Individual sections keep
 * their own inline error handling; this does not replace it.
 */
export function ApiHealthBanner() {
  const queryClient = useQueryClient();
  const [down, setDown] = React.useState(false);

  React.useEffect(() => apiHealthEvents.subscribe(setDown), []);

  if (!down) return null;

  return (
    <div className="px-4 pt-4 md:px-6">
      <Alert variant="danger">
        <AlertTitle>Can&apos;t reach the API</AlertTitle>
        <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
          <span>Some data may be missing or stale until the connection recovers.</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void queryClient.refetchQueries();
            }}
          >
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    </div>
  );
}
