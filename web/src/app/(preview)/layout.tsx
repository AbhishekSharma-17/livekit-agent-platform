import * as React from "react";

import { Caveat, Patrick_Hand } from "next/font/google";
import type { Metadata } from "next";

/**
 * The preview route's own root (docs/UI_UX_SPEC.md §7.11 item 1): unlisted,
 * noindex, no console sidebar. It is a **route group**
 * (`app/(preview)/console/preview/panels/page.tsx` serves `/console/preview/panels`)
 * rather than a nested `app/console/preview/layout.tsx`, because
 * `app/console/layout.tsx` (WP-1) unconditionally wraps every route under
 * `/console/**` in `ConsoleShell` (sidebar, top bar, `ApiHealthBanner` — which
 * would poll a real, possibly-erroring API from behind every scene) with no
 * per-path bypass. A nested layout can only *add* chrome, never remove an
 * ancestor's, so a sibling route-group layout is the only way to get "no
 * sidebar" for a URL that still starts with `/console/`. See
 * `src/components/preview/README.md` and the hand-off note for the read-out.
 *
 * Loads the notebook's two handwriting faces exactly like
 * `app/(session)/layout.tsx` (`src/panels/README.md`: a panel module cannot
 * call `next/font` itself), so `?scene=notebook` matches the real session.
 */
const caveat = Caveat({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-hand",
});

const patrickHand = Patrick_Hand({
  subsets: ["latin"],
  weight: "400",
  display: "swap",
  variable: "--font-hand-label",
});

export const metadata: Metadata = {
  title: "LKAP preview",
  robots: { index: false, follow: false },
};

export default function PreviewLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className={`${caveat.variable} ${patrickHand.variable}`}>
      {children}
    </div>
  );
}
