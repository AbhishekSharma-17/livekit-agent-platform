import * as React from "react";

import { CircleAlertIcon, CircleCheckIcon, TriangleAlertIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { ValidationResult } from "@/contracts/lkap-contracts";

/**
 * Validation result list (docs/ui/DESIGN-SYSTEM.md section 6.5 alert tones). WP-3 replaces
 * the banner with per-section issue lists; this keeps the old call sites
 * working and on-palette meanwhile.
 */
export function ValidationBanner({ result }: { result: ValidationResult }) {
  const errors = result.errors ?? [];
  const warnings = result.warnings ?? [];

  if (result.ok && warnings.length === 0) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 rounded border border-success-border bg-success-subtle px-3 py-2.5 text-label text-success-text"
      >
        <Icon as={CircleCheckIcon} size="md" />
        Configuration looks good
      </div>
    );
  }

  return (
    <div role="status" className="flex flex-col gap-2">
      {errors.length > 0 ? (
        <div className="flex gap-2 rounded border border-destructive-border bg-destructive-subtle px-3 py-2.5 text-label text-destructive-text">
          <Icon as={CircleAlertIcon} size="md" className="mt-0.5" />
          <ul className="list-inside list-disc space-y-0.5">
            {errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className="flex gap-2 rounded border border-warning-border bg-warning-subtle px-3 py-2.5 text-label text-warning-text">
          <Icon as={TriangleAlertIcon} size="md" className="mt-0.5" />
          <ul className="list-inside list-disc space-y-0.5">
            {warnings.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
