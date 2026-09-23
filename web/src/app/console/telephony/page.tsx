import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { TelephonyPage } from "@/components/console/telephony/telephony-page";

export const metadata: Metadata = { title: "Telephony" };

/** `/console/telephony` (PLAN-V2 V2-17): SIP trunks, numbers, dispatch rules and calls. */
export default function ConsoleTelephonyPage() {
  return (
    <div>
      <PageHeader
        title="Telephony"
        description="Give agents phone numbers: connect a SIP carrier, route numbers to agents and follow every call."
      />
      <TelephonyPage />
    </div>
  );
}
