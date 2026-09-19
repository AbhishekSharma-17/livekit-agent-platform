import * as React from "react";

import { CircleCheckIcon, CircleXIcon, TriangleAlertIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { ValidationResult } from "@/contracts/lkap-contracts";

/**
 * Validation result list (tokens per docs/UI_UX_SPEC.md §2.2). WP-3 replaces
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
        className="flex items-center gap-2 rounded-md bg-success-soft px-3 py-2.5 text-sm text-success-text"
      >
        <Icon as={CircleCheckIcon} size="md" />
        Configuration looks good
      </div>
    );
  }

  return (
    <div role="status" className="flex flex-col gap-2">
      {errors.length > 0 ? (
        <div className="flex gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-sm text-danger-text">
          <Icon as={CircleXIcon} size="md" className="mt-0.5" />
          <ul className="list-inside list-disc space-y-0.5">
            {errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className="flex gap-2 rounded-md bg-warning-soft px-3 py-2.5 text-sm text-warning-text">
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
