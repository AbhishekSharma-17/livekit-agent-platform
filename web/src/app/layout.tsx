import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import "./globals.css";
import { cn } from "@/lib/utils";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "LKAP",
  description:
    "LiveKit Agent Platform — configure and run real-time voice + video agents.",
};

/**
 * Base theme root layout. Route groups own their own visual theme on top of
 * this: `(session)/layout.tsx` (dark session surface) and
 * `console/layout.tsx` (light "studio" console). Nobody edits this file
 * after W0-SCAFFOLD (docs/IMPLEMENTATION_PLAN.md W0-SCAFFOLD).
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn("font-sans", geist.variable)}
    >
      <body
        className={`${geist.variable} ${geistMono.variable} font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
