import type { Metadata } from "next";

import { Page } from "@/components/shared/page-header";
import { TelephonyPage } from "@/components/console/telephony/telephony-page";

export const metadata: Metadata = { title: "Telephony" };

/**
 * `/console/telephony` (PLAN-V2 V2-17): SIP trunks, numbers, dispatch rules and calls. The page header and its one
 * primary action ("Add trunk") live in `TelephonyPage`, next to the dialog they open.
 */
export default function ConsoleTelephonyPage() {
  return (
    <Page>
      <TelephonyPage />
    </Page>
  );
}
