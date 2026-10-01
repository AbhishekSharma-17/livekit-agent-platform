import type * as React from "react";
import { ChartNoAxesColumnIcon, CircleCheckIcon, CircleIcon, NotebookPenIcon } from "lucide-react";

import { AGENTS_ICON } from "@/components/console/shell/nav-config";
import { Icon } from "@/components/shared/icon";
import { StatusPill } from "@/components/shared/status-chip";
import { cn } from "@/lib/utils";

import { CallTimer, ShowcaseDirector } from "./showcase-director";
import "./sign-in-showcase.css";

/**
 * The sign-in showcase (docs/ui/DESIGN-SYSTEM.md sections 1, 5 and 7.4): the
 * panel on the sidebar colour to the right of the form, with a drifting
 * dotted grid, two slow indigo glows and a small live scene of what the
 * product does. A voice call plays out (a short exchange types into the
 * transcript while the waveform and the agent's orb follow whoever speaks),
 * and the agent's panel fills in beside it (a note types in, a checklist
 * ticks, a chart bar rises). Then it holds, clears and starts again.
 *
 * - Purely decorative (`aria-hidden`), hidden at 1080 px and below.
 * - Server-rendered. The only client code is `ShowcaseDirector`, a timer
 *   that steps the story by setting attributes, and the call timer.
 * - Only transform, opacity and colour animate (`sign-in-showcase.module.css`).
 * - The server render, reduced motion and a hidden tab all show the
 *   composed frame, with everything in place and nothing moving.
 * - The panel is its own grid column, so the form never moves.
 */
export function SignInShowcase() {
  return (
    <div
      aria-hidden="true"
      data-slot="sign-in-showcase"
      className="relative hidden min-w-0 overflow-hidden border-l border-border bg-sidebar min-[1081px]:block"
    >
      <ShowcaseDirector className="lkap-showcase">
        <div className="lkap-showcase-grid">
          <div className="lkap-showcase-grid-dots" />
        </div>
        <div className="lkap-showcase-glow lkap-showcase-glow-a" />
        <div className="lkap-showcase-glow lkap-showcase-glow-b" />
        <Scene />
      </ShowcaseDirector>
    </div>
  );
}

/* ------------------------------------------------------------------ */

/** Resting heights of the waveform's bars (a spoken phrase), and each bar's own period. */
const WAVE = [
  0.22, 0.3, 0.42, 0.36, 0.55, 0.7, 0.48, 0.62, 0.85, 0.66, 0.5, 0.74, 0.95, 0.8, 0.58, 0.68, 0.9, 1, 0.76, 0.6,
  0.82, 0.92, 0.7, 0.52, 0.64, 0.78, 0.56, 0.44, 0.6, 0.72, 0.5, 0.38, 0.46, 0.34, 0.28, 0.4, 0.3, 0.24, 0.2, 0.16,
];
const WAVE_PERIODS = [0.82, 1.06, 0.74, 0.94, 1.18, 0.68, 0.88, 1.12, 0.78, 0.98];

/** The calls-today chart: earlier hours, then the newest bar, which rises. */
const CHART = [0.42, 0.58, 0.36, 0.7, 0.52, 0.8];
const CHART_NEWEST = 0.94;

function cssVars(vars: Record<string, string | number>): React.CSSProperties {
  return vars as React.CSSProperties;
}

function Scene() {
  return (
    <div className="relative flex w-full max-w-[420px] flex-col gap-4 min-[1600px]:max-w-[460px]">
      <div className="lkap-showcase-float-a">
        <CallCard />
      </div>
      <div className="grid grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] gap-4">
        <div className="lkap-showcase-float-b">
          <NotesCard />
        </div>
        <div className="lkap-showcase-float-c">
          <ChartCard />
        </div>
      </div>
    </div>
  );
}

/** A transcript line. The cover is the bubble's own colour, scaled away as the words "type". */
function Bubble({
  at,
  from,
  children,
  typeMs,
}: {
  at: number;
  from: "caller" | "agent";
  children: React.ReactNode;
  typeMs: number;
}) {
  const agent = from === "agent";
  return (
    <p
      data-at={at}
      className={cn(
        "lkap-showcase-reveal",
        "w-fit max-w-full rounded-lg px-3 py-2 text-label whitespace-nowrap text-foreground",
        agent ? "ml-auto border border-brand-border bg-brand-subtle" : "border border-transparent bg-muted",
      )}
      style={cssVars({ "--type": `${typeMs}ms` })}
    >
      {children}
      <span className={cn("lkap-showcase-cover", agent ? "bg-brand-subtle" : "bg-muted")} />
    </p>
  );
}

function CallCard() {
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5">
      <div className="flex items-center gap-3">
        <span className="relative flex size-10 shrink-0 items-center justify-center">
          <span className="lkap-showcase-orb-ring absolute inset-0 rounded-pill border border-brand-border" />
          <span className="lkap-showcase-orb-ring absolute inset-0 rounded-pill border border-brand-border" />
          <span className="relative flex size-10 items-center justify-center rounded-pill border border-brand-border bg-brand-subtle text-brand">
            <Icon as={AGENTS_ICON} size="tile" />
          </span>
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-label font-medium text-foreground">Front desk agent</span>
          <span className="truncate text-caption text-text-secondary">Voice call</span>
        </div>
        <div className="flex items-center gap-2 text-caption text-text-secondary">
          <CallTimer />
          <StatusPill tone="live">Live</StatusPill>
        </div>
      </div>

      <div className="lkap-showcase-wave flex h-8 items-center justify-between">
        {WAVE.map((height, index) => (
          <span
            key={index}
            className="lkap-showcase-wave-bar block h-full w-[3px] rounded-pill"
            style={cssVars({ "--h": height, "--dur": `${WAVE_PERIODS[index % WAVE_PERIODS.length]}s` })}
          />
        ))}
      </div>

      <div className="flex flex-col gap-2 border-t border-border pt-4">
        <Bubble at={1} from="caller" typeMs={900}>
          Can I move my visit to Thursday?
        </Bubble>
        <Bubble at={2} from="agent" typeMs={1000}>
          Sure. I have 10:30 or 2:15 free.
        </Bubble>
        <Bubble at={3} from="caller" typeMs={700}>
          10:30 works, thanks.
        </Bubble>
      </div>
    </div>
  );
}

function CardTitle({ icon, children }: { icon: typeof NotebookPenIcon; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-caption font-medium text-text-secondary">
      <Icon as={icon} size="sm" />
      {children}
    </div>
  );
}

function CheckItem({ at, children }: { at: number; children: React.ReactNode }) {
  return (
    <li data-at={at} className="flex items-center gap-2 text-label text-foreground">
      <span className="relative flex size-3.5 shrink-0">
        <Icon as={CircleIcon} size="sm" className="lkap-showcase-untick absolute inset-0 text-text-tertiary" />
        <Icon as={CircleCheckIcon} size="sm" className="lkap-showcase-tick absolute inset-0 text-success-text" />
      </span>
      <span className="truncate">{children}</span>
    </li>
  );
}

function NotesCard() {
  return (
    <div className="flex h-full flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <CardTitle icon={NotebookPenIcon}>Call notes</CardTitle>
      <p
        data-at={4}
        className="lkap-showcase-reveal w-fit max-w-full text-label font-medium whitespace-nowrap text-foreground"
        style={cssVars({ "--type": "900ms" })}
      >
        Moved to Thursday, 10:30
        <span className="lkap-showcase-cover bg-card" />
      </p>
      <ul className="flex flex-col gap-1.5">
        <CheckItem at={5}>Caller verified</CheckItem>
        <CheckItem at={6}>New time booked</CheckItem>
        <CheckItem at={7}>Text confirmation sent</CheckItem>
      </ul>
    </div>
  );
}

function ChartCard() {
  return (
    <div className="flex h-full flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <CardTitle icon={ChartNoAxesColumnIcon}>Calls today</CardTitle>
      <span className="text-title font-semibold text-foreground tabular-nums">128</span>
      <div className="mt-auto flex h-14 items-end gap-1.5">
        {CHART.map((height, index) => (
          <span
            key={index}
            className="lkap-showcase-bar block h-full flex-1 rounded-sm bg-muted-strong"
            style={cssVars({ "--h": height })}
          />
        ))}
        <span
          data-at={8}
          className="lkap-showcase-bar lkap-showcase-rise block h-full flex-1 rounded-sm bg-brand"
          style={cssVars({ "--h": CHART_NEWEST })}
        />
      </div>
    </div>
  );
}
