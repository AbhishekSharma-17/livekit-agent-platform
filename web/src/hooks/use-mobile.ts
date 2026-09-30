import * as React from "react";

/**
 * Shell breakpoints (docs/ui/DESIGN-SYSTEM.md sections 7.2 and 10). Both are
 * inclusive: at exactly 820 px the sidebar is already gone, and at exactly
 * 640 px the phone tab bar is already showing. The CSS side uses the same
 * numbers (`max-[820px]:` / `min-[821px]:` and `max-[640px]:`).
 */
export const MOBILE_BREAKPOINT = 820;
export const PHONE_BREAKPOINT = 640;

/**
 * `true` while the viewport is at most `maxWidth` px wide. It reads
 * `window.innerWidth` (and listens to the matching media query and to
 * resizes), so it also works where `matchMedia` is a stub. SSR and the first
 * hydration pass report `false`.
 */
export function useMaxWidth(maxWidth: number): boolean {
  const subscribe = React.useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined") return () => {};
      window.addEventListener("resize", onChange);
      const mql = typeof window.matchMedia === "function" ? window.matchMedia(`(max-width: ${maxWidth}px)`) : null;
      mql?.addEventListener?.("change", onChange);
      return () => {
        window.removeEventListener("resize", onChange);
        mql?.removeEventListener?.("change", onChange);
      };
    },
    [maxWidth],
  );
  const getSnapshot = React.useCallback(
    () => typeof window !== "undefined" && window.innerWidth <= maxWidth,
    [maxWidth],
  );
  return React.useSyncExternalStore(subscribe, getSnapshot, () => false);
}

/** At most 820 px: the sidebar becomes the Menu dialog and the main panel goes full-bleed. */
export function useIsMobile(): boolean {
  return useMaxWidth(MOBILE_BREAKPOINT);
}

/** At most 640 px: the phone bottom tab bar shows. */
export function useIsPhone(): boolean {
  return useMaxWidth(PHONE_BREAKPOINT);
}
