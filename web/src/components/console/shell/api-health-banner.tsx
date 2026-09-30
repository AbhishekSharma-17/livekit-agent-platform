"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { apiHealthEvents } from "@/components/console/lib/query-provider";

/**
 * The shell's "API down" alert (docs/ui/DESIGN-SYSTEM.md section 8): one
 * danger alert under the top bar when three consecutive queries fail with a
 * network error or a 5xx, never for a 4xx with an error envelope (a real,
 * scoped error; some endpoints legitimately 404 before their package ships,
 * e.g. `auth/me`). Sections keep their own inline error handling.
 */
export function ApiHealthBanner() {
  const queryClient = useQueryClient();
  const [down, setDown] = React.useState(false);

  React.useEffect(() => apiHealthEvents.subscribe(setDown), []);

  if (!down) return null;

  return (
    <div data-slot="api-health-banner" className="px-8 pt-4 max-[900px]:px-5 max-[640px]:px-4">
      <Alert
        tone="danger"
        title="Can't reach the API"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void queryClient.refetchQueries()}>
            Retry
          </Button>
        }
        className="mx-auto max-w-[1440px]"
      >
        Some data may be missing or out of date until the connection is back.
      </Alert>
    </div>
  );
}
