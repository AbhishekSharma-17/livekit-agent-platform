"use client";

import * as React from "react";
import Link from "next/link";
import { PlusIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { IfCan, ReadOnlyNote } from "@/components/console/shared/permission";
import { useAgents } from "@/components/console/lib/api-hooks";
import { useConnections } from "@/hooks/useConnections";

import { CallsSection } from "./calls-section";
import { DialingPolicyCard } from "./dialing-policy";
import { sipEnabled } from "./model";
import { NumbersSection } from "./numbers-section";
import { RulesSection } from "./rules-section";
import { TrunkDialog, TrunksSection } from "./trunks-section";

/**
 * `/console/telephony` (PLAN-V2 V2-17): trunks, numbers with the inbound
 * agent picker, dispatch rules and the calls log. Everything is mirrored to
 * the LiveKit SIP service of the object's connection; a connection whose
 * capability probe did not find SIP cannot hold trunks. The outbound dialing
 * policy (R-V2-23, default deny) sits between the trunks and the numbers.
 *
 * Page template (docs/ui/DESIGN-SYSTEM.md section 7.3): the header's one
 * primary action is "Add trunk", the first step of connecting a carrier.
 * Telephony writes need `admin` server-side (`auth/roles.py::ROUTE_POLICY`),
 * so below that the primary becomes a read-only note (decision D12) and every
 * section hides its own write controls.
 */
export function TelephonyPage() {
  const connectionsQuery = useConnections();
  const connections = React.useMemo(() => connectionsQuery.data?.items ?? [], [connectionsQuery.data]);
  const agentsData = useAgents().data;
  const agents = React.useMemo(() => (agentsData?.items ?? []).filter((agent) => !agent.archived_at), [agentsData]);
  const anySip = connections.some(sipEnabled);
  const [creatingTrunk, setCreatingTrunk] = React.useState(false);

  return (
    <>
      <PageHeader
        title="Telephony"
        description="Give agents phone numbers: connect a SIP carrier, route numbers to agents and follow every call."
        actions={
          <IfCan
            min="admin"
            fallback={<ReadOnlyNote>You can view telephony. Ask an admin to change trunks, numbers or rules.</ReadOnlyNote>}
          >
            <Button type="button" variant="primary" disabled={!anySip} onClick={() => setCreatingTrunk(true)}>
              <PlusIcon aria-hidden="true" />
              Add trunk
            </Button>
          </IfCan>
        }
      />
      <div className="flex flex-col gap-8">
        {connectionsQuery.isError ? (
          <ErrorBanner
            error={connectionsQuery.error}
            context={{ action: "load connections" }}
            onRetry={() => void connectionsQuery.refetch()}
          />
        ) : connections.length > 0 && !anySip ? (
          <Alert tone="warning">
            None of your connections reports SIP. Run <strong>Test</strong> on a connection in{" "}
            <Link href="/console/connections" className="font-medium underline underline-offset-4">
              Connections
            </Link>
            ; a self-hosted server also needs the LiveKit SIP service deployed. Until then trunks can&apos;t be added.
          </Alert>
        ) : null}
        <TrunksSection connections={connections} />
        <DialingPolicyCard />
        <NumbersSection agents={agents} />
        <RulesSection agents={agents} />
        <CallsSection />
      </div>
      <TrunkDialog open={creatingTrunk} onOpenChange={setCreatingTrunk} connections={connections} />
    </>
  );
}
