import type { MetadataRoute } from "next";

import { BROWSER_COLORS } from "@/lib/browser-colors";

/**
 * Web manifest (docs/ui/DESIGN-SYSTEM.md section 2.4). A manifest cannot read
 * CSS variables, so it mirrors the light `--background` and `--brand` tokens
 * as literals from `lib/browser-colors.ts`. The icons are the LKAP mark,
 * written by `scripts/gen-app-icons.mjs` (rounded tile with transparent
 * corners, so not declared `maskable`).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LKAP console",
    short_name: "LKAP",
    description: "LiveKit Agent Platform. Configure and run real-time voice and video agents.",
    start_url: "/console",
    display: "standalone",
    background_color: BROWSER_COLORS.light.background,
    theme_color: BROWSER_COLORS.light.brand,
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
