import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import "./globals.css";
import { ThemeProvider } from "@/components/theme/theme-provider";
import { THEME_COLOR } from "@/lib/browser-colors";

/**
 * Inter (docs/ui/DESIGN-SYSTEM.md section 3), self-hosted by `next/font` with
 * `font-display: swap`. It is exposed as `--font-inter`; `globals.css` builds
 * the `--font-sans` stack (Inter, then the system fallbacks) on top of it.
 */
const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });

export const metadata: Metadata = {
  title: "LKAP",
  description:
    "LiveKit Agent Platform. Configure and run real-time voice and video agents.",
};

/** Browser chrome follows the page background in each theme (literal mirrors of the tokens). */
export const viewport: Viewport = { themeColor: THEME_COLOR };

/**
 * Root layout. UI-1 (docs/ui/AUDIT.md) owns this file: it loads the font and
 * the token stylesheet and mounts the one app-wide `ThemeProvider`, so every
 * route (console, `/`, `/login`, not-found) gets class-based theming with no
 * flash on load. `suppressHydrationWarning` is required because next-themes
 * sets the `<html>` class before React hydrates. Route groups add their own
 * surface on top: `(session)/layout.tsx` is fixed dark.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="font-sans antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
