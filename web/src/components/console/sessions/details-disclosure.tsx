import * as React from "react";
import { ChevronRightIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { cn } from "@/lib/utils";

/**
 * The only place raw JSON appears in the sessions UI (docs/UI_UX_SPEC.md
 * §7.8: "payload JSON behind a Details disclosure"). Native `<details>` so it
 * is keyboard- and screen-reader-friendly without script.
 */
export function DetailsDisclosure({
  label = "Details",
  children,
  className,
}: {
  label?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <details data-slot="details-disclosure" className={cn("group/details mt-1.5", className)}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-xs text-xs font-medium text-muted-foreground outline-none select-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <Icon
          as={ChevronRightIcon}
          size="sm"
          className="transition-transform duration-(--dur-1) ease-out group-open/details:rotate-90 motion-reduce:transition-none"
        />
        {label}
      </summary>
      <div className="mt-2 space-y-2">{children}</div>
    </details>
  );
}

/** A labelled JSON/text block inside a `DetailsDisclosure`. */
export function CodeBlock({ label, value }: { label?: string; value: string }) {
  return (
    <div className="min-w-0">
      {label ? <p className="mb-1 text-[0.6875rem] font-medium tracking-[0.02em] text-muted-foreground">{label}</p> : null}
      <pre tabIndex={0} className="max-h-80 overflow-auto outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-foreground">
        {value}
      </pre>
    </div>
  );
}
