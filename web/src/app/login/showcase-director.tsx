"use client";

import * as React from "react";

import { useMaxWidth } from "@/hooks/use-mobile";
import { useMediaQuery } from "@/hooks/use-media-query";

/**
 * The sign-in showcase's director: the only client code in the scene. The
 * markup itself is server-rendered (`sign-in-showcase.tsx`) and passed in as
 * children, so `/login` ships just this timer loop. It sets three attributes
 * on the scene root and the CSS module does the rest:
 *
 * - `data-step` walks the story (0 a fresh call, 1 to 3 the exchange, 4 the
 *   note, 5 to 7 the checklist, 8 the chart bar, 9 the composed frame, held,
 *   10 the outro, then round again);
 * - `data-motion` is `on`, or `reduce` when motion isn't welcome (reduced
 *   motion, or no way to ask, as on the server);
 * - `data-paused` while the tab is hidden or the panel isn't shown (1080 px
 *   and below), which pauses the ambient loops and stops the timers.
 *
 * The first render is the composed frame (step 9) with motion off, the same
 * on the server and on the client, so nothing flashes on hydration. With
 * motion welcome the story picks up from the held frame.
 */

/** The composed frame: everything shown. Also the reduced-motion frame. */
export const SHOWCASE_HOLD_STEP = 9;

/** How long each step lasts, in ms (index = step). */
export const SHOWCASE_STEP_MS = [900, 1900, 2300, 1700, 1500, 700, 700, 800, 1400, 4200, 900] as const;

/** The call timer's starting point on the composed frame (01:24). */
const CALL_SECONDS_AT_HOLD = 84;

const RunningContext = React.createContext(false);

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

/** `false` while the tab is hidden. */
function useDocumentVisible(): boolean {
  return React.useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState !== "hidden",
    () => true,
  );
}

export function ShowcaseDirector({ className, children }: { className?: string; children: React.ReactNode }) {
  // `false` on the server and wherever matchMedia is missing: the static frame.
  const motionOk = useMediaQuery("(prefers-reduced-motion: no-preference)");
  const visible = useDocumentVisible();
  // The showcase hides at 1080 px and below (docs/ui/DESIGN-SYSTEM.md section 10).
  const narrow = useMaxWidth(1080);
  const running = motionOk && visible && !narrow;
  const [step, setStep] = React.useState(SHOWCASE_HOLD_STEP);

  React.useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(
      () => setStep((current) => (current + 1) % SHOWCASE_STEP_MS.length),
      SHOWCASE_STEP_MS[step],
    );
    return () => window.clearTimeout(timer);
  }, [running, step]);

  return (
    <div
      className={className}
      data-slot="showcase-scene"
      data-motion={motionOk ? "on" : "reduce"}
      data-step={motionOk ? step : SHOWCASE_HOLD_STEP}
      data-paused={running ? undefined : ""}
    >
      <RunningContext.Provider value={running}>{children}</RunningContext.Provider>
    </div>
  );
}

/** The live call's elapsed time. Ticks only while the scene runs. */
export function CallTimer() {
  const running = React.useContext(RunningContext);
  const [seconds, setSeconds] = React.useState(CALL_SECONDS_AT_HOLD);

  React.useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const minutes = String(Math.floor(seconds / 60) % 100).padStart(2, "0");
  const rest = String(seconds % 60).padStart(2, "0");
  return (
    <span data-slot="call-timer" className="tabular-nums">
      {minutes}:{rest}
    </span>
  );
}
