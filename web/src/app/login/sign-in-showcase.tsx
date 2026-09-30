import { CircleCheckIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { StatusPill } from "@/components/shared/status-chip";

/**
 * The sign-in showcase (docs/ui/DESIGN-SYSTEM.md section 7.4, docs/ui/AUDIT.md
 * D10): a quiet panel on the sidebar colour with a dotted-grid texture, a soft
 * blurred accent glow and a small picture of what the product produces, a live
 * agent session that ends with its details captured. No marketing copy.
 *
 * Purely decorative (`aria-hidden`), hidden at 1080 px and below (section 10).
 * Its only motion is ambient: the speaking meter, the live dot and a slow
 * breathing glow, all of which stop under `prefers-reduced-motion` (the global
 * rule in `globals.css` plus `motion-safe:` here).
 */
export function SignInShowcase() {
  return (
    <div
      aria-hidden="true"
      data-slot="sign-in-showcase"
      className="relative hidden min-w-0 items-center justify-center overflow-hidden border-l border-border bg-sidebar p-12 min-[1081px]:flex"
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(var(--border-strong)_1px,transparent_1px)] bg-[size:20px_20px] opacity-35 [mask-image:radial-gradient(ellipse_at_center,var(--foreground)_30%,transparent_75%)]" />
      <div className="pointer-events-none absolute top-1/2 left-1/2 size-[440px] -translate-x-1/2 -translate-y-1/2 opacity-15 blur-[96px]">
        {/* Reuses the live dot's opacity keyframes, slowed right down. */}
        <div className="size-full rounded-pill bg-brand motion-safe:animate-[lkap-pulse-dot_9s_ease-in-out_infinite]" />
      </div>
      <SessionIllustration />
    </div>
  );
}

function Bar({ className }: { className: string }) {
  return <span className={`block h-2 rounded-pill ${className}`} />;
}

/** A live session card: the agent speaking, a short exchange, then the details it captured. */
function SessionIllustration() {
  return (
    <div className="relative flex w-full max-w-[360px] flex-col gap-4 rounded-lg border border-border bg-card p-5">
      <div className="flex items-center gap-3">
        <span className="flex size-9 items-center justify-center rounded-pill bg-brand-subtle text-brand">
          <StateMeter state="speaking" size="sm" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Bar className="w-28 bg-muted-strong" />
          <Bar className="w-16 bg-muted" />
        </div>
        <StatusPill tone="live">Live</StatusPill>
      </div>

      <div className="flex flex-col gap-2.5 border-t border-border pt-4">
        <div className="flex w-4/5 flex-col gap-1.5 rounded-lg bg-muted px-3 py-2.5">
          <Bar className="w-full bg-muted-strong" />
          <Bar className="w-3/5 bg-muted-strong" />
        </div>
        <div className="ml-auto flex w-3/5 flex-col gap-1.5 rounded-lg border border-brand-border bg-brand-subtle px-3 py-2.5">
          <Bar className="w-full bg-brand-border" />
        </div>
        <div className="flex w-2/3 items-center gap-2 rounded-lg bg-muted px-3 py-2.5">
          <Bar className="flex-1 bg-muted-strong" />
          <StateMeter state="speaking" size="xs" />
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded border border-border px-3 py-3">
        {["w-32", "w-24", "w-28"].map((width) => (
          <div key={width} className="flex items-center gap-2 text-success-text">
            <Icon as={CircleCheckIcon} size="sm" />
            <Bar className={`${width} bg-muted-strong`} />
          </div>
        ))}
      </div>
    </div>
  );
}
