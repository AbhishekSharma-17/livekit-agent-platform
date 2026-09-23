"use client";

import { WorkflowIcon } from "lucide-react";
import { useFormContext, useWatch } from "react-hook-form";

import { InstructionsTab } from "@/components/console/agents/tabs/instructions-tab";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { Icon } from "@/components/shared/icon";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * The Instructions section in flow mode (R-V2-13): the same editor (WP-5's
 * `InstructionsTab`), introduced as the base instructions every flow node
 * starts from — `instructions` → global node → node, in that order.
 */
export function FlowAwareInstructions({ agent }: { agent: AgentOut }) {
  const { control } = useFormContext<AgentEditorForm>();
  const mode = useWatch({ control, name: "mode" });
  return (
    <div className="flex flex-col gap-4">
      {mode === "flow" ? (
        <Alert variant="info" data-slot="flow-base-instructions">
          <Icon as={WorkflowIcon} />
          <AlertTitle>Base instructions (apply to every node)</AlertTitle>
          <AlertDescription>
            In a flow, these come first in every step&apos;s prompt, followed by the global rules and then the
            step&apos;s own instructions. Keep the persona and guardrails here.
          </AlertDescription>
        </Alert>
      ) : null}
      <InstructionsTab agent={agent} />
    </div>
  );
}
