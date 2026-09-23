/**
 * Pure layout model for the in-call shell (docs/UI_UX_SPEC.md §5.3).
 *
 * The shell renders **one** DOM tree for every viewport; the breakpoint work
 * is done by these class strings (no `matchMedia`, so the layout is testable
 * and SSR-stable). Visual order below `lg` is expressed with `order-*`,
 * placement at `lg` with explicit grid rows/columns.
 *
 * | region     | mobile (<768)            | tablet (768–1023)        | desktop (≥1024)            |
 * |------------|--------------------------|--------------------------|----------------------------|
 * | side       | stage 45vh → panel       | stage → panel            | stage over transcript,     |
 * |            |                          |                          | panel 400 px right         |
 * | wide       | stage strip 72 px → panel| panel → stage            | 340 px rail (stage over    |
 * |            |                          |                          | transcript), panel right   |
 *
 * The transcript is the same element everywhere: a bottom sheet below `lg`,
 * an in-column region at `lg`. The controls are `fixed` (safe-area aware)
 * below `lg` and sit under the stage column at `lg`.
 */
export type SessionLayout = "side" | "wide";

/** Reserved space under the mobile scroll region for the fixed control bar. */
export const MOBILE_CONTROLS_SPACE =
  "calc(88px + env(safe-area-inset-bottom, 0px))";

export interface SessionLayoutModel {
  /** The regions in DOM order, with their visual order per breakpoint. */
  order: Record<"stage" | "panel", { mobile: number; tablet: number }>;
  root: string;
  grid: string;
  stage: string;
  panel: string;
  transcript: string;
  controls: string;
}

const ROOT_BASE =
  "relative flex min-h-[100dvh] w-full flex-col lg:h-dvh lg:min-h-0 lg:overflow-hidden";

const GRID_BASE =
  "flex min-h-0 flex-1 flex-col gap-3 p-3 lg:grid lg:gap-4 lg:p-4";

const REGION_BASE = "border-border bg-card flex min-h-0 flex-col rounded-xl border";

/** Bottom sheet below `lg`, plain column region at `lg` (§5.3). */
const TRANSCRIPT_BASE = [
  REGION_BASE,
  "overflow-hidden",
  // sheet
  "fixed inset-x-3 bottom-[calc(88px+env(safe-area-inset-bottom,0px))] z-40 h-[60vh]",
  "shadow-lg transition-[transform,opacity] duration-(--dur-4) ease-(--ease-drawer) motion-reduce:transition-none",
  "data-[open=false]:pointer-events-none data-[open=false]:translate-y-[calc(100%+96px)] data-[open=false]:opacity-0",
  // in-column
  "lg:static lg:inset-auto lg:z-auto lg:h-auto lg:translate-y-0 lg:opacity-100 lg:shadow-none",
  "lg:data-[open=false]:pointer-events-auto lg:data-[open=false]:translate-y-0 lg:data-[open=false]:opacity-100",
  "lg:col-start-1 lg:row-start-2",
].join(" ");

const CONTROLS_BASE = [
  "z-50 shrink-0",
  "fixed inset-x-0 bottom-0 px-3 pb-[env(safe-area-inset-bottom,0px)]",
  "lg:static lg:inset-auto lg:px-0 lg:pb-0",
  "lg:col-start-1 lg:row-start-3 lg:justify-self-center lg:w-full lg:max-w-2xl",
].join(" ");

const PANEL_BASE = [
  REGION_BASE,
  "overflow-hidden lg:col-start-2 lg:row-start-1 lg:row-span-3",
].join(" ");

/**
 * Class strings for one panel layout. Kept as data (not inline conditionals)
 * so `tests/session-shell.test.tsx` can assert the model without a browser.
 */
export function sessionLayoutModel(layout: SessionLayout): SessionLayoutModel {
  const isWide = layout === "wide";
  return {
    order: isWide
      ? { stage: { mobile: 1, tablet: 2 }, panel: { mobile: 2, tablet: 1 } }
      : { stage: { mobile: 1, tablet: 1 }, panel: { mobile: 2, tablet: 2 } },
    root: ROOT_BASE,
    grid: [
      GRID_BASE,
      isWide
        ? "lg:grid-cols-[340px_minmax(0,1fr)] lg:grid-rows-[200px_minmax(0,1fr)_auto]"
        : "lg:grid-cols-[minmax(0,1fr)_400px] lg:grid-rows-[minmax(320px,1fr)_minmax(200px,1fr)_auto]",
    ].join(" "),
    stage: [
      "bg-stage text-stage-foreground relative flex min-h-0 shrink-0 items-center justify-center overflow-hidden rounded-xl",
      "order-1 lg:order-none lg:col-start-1 lg:row-start-1 lg:h-full",
      isWide
        ? "min-h-[72px] md:order-2 md:min-h-[200px] lg:min-h-0"
        : "h-[45vh] md:order-1 lg:h-full",
    ].join(" "),
    panel: [
      PANEL_BASE,
      "order-2 lg:order-none",
      isWide ? "min-h-[60vh] md:order-1" : "min-h-[50vh] md:order-2 lg:min-h-0",
    ].join(" "),
    transcript: TRANSCRIPT_BASE,
    controls: CONTROLS_BASE,
  };
}
