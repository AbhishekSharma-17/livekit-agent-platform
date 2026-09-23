"use client";

import * as React from "react";
import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Icon } from "@/components/shared";
import { useAgents } from "@/components/console/lib/api-hooks";
import { useConnections } from "@/hooks/useConnections";

import { CallsSection } from "./calls-section";
import { sipEnabled } from "./model";
import { NumbersSection } from "./numbers-section";
import { RulesSection } from "./rules-section";
import { TrunksSection } from "./trunks-section";

/**
 * `/console/telephony` (PLAN-V2 V2-17): trunks, numbers with the inbound
 * agent picker, dispatch rules and the calls log. Everything is mirrored to
 * the LiveKit SIP service of the object's connection; a connection whose
 * capability probe did not find SIP cannot hold trunks.
 */
export function TelephonyPage() {
  const connections = useConnections().data?.items ?? [];
  const agents = (useAgents().data?.items ?? []).filter((agent) => !agent.archived_at);
  const anySip = connections.some(sipEnabled);

  return (
    <div className="flex flex-col gap-6">
      {connections.length > 0 && !anySip ? (
        <Alert variant="warning">
          <Icon as={TriangleAlertIcon} size="md" />
          <AlertDescription>
            None of your connections reports SIP. Run <strong>Test</strong> on a connection in{" "}
            <Link href="/console/connections" className="underline underline-offset-4">
              Connections
            </Link>
            ; a self-hosted server also needs the LiveKit SIP service deployed.
          </AlertDescription>
        </Alert>
      ) : null}
      <TrunksSection connections={connections} />
      <NumbersSection agents={agents} />
      <RulesSection agents={agents} />
      <CallsSection />
    </div>
  );
}
