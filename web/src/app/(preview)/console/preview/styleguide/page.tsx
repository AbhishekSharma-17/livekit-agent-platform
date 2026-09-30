import type { Metadata } from "next";

import { Styleguide } from "@/components/preview/styleguide";

export const metadata: Metadata = {
  title: "Styleguide",
  robots: { index: false, follow: false },
};

/**
 * `/console/preview/styleguide` (docs/ui/DESIGN-SYSTEM.md section 11): every
 * primitive in both themes, shell-less like the panels preview.
 */
export default function StyleguidePage() {
  return <Styleguide />;
}
