"use client";

import dynamic from "next/dynamic";
import type { LucideIcon } from "lucide-react";
import { BracesIcon, GitForkIcon, SquareStackIcon, WorkflowIcon } from "lucide-react";
import { useFormContext, useWatch } from "react-hook-form";

import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { AgentOut } from "@/contracts/lkap-contracts";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { LoadingRegion } from "@/components/shared/loading-state";

import { openModeSwitch } from "./mode-switch";

/**
 * The agent editor's `flow` section (V2-16; always in the nav, full width).
 * In prompt mode it explains flows and offers the header chip's switch dialog
 * (`openModeSwitch`); once the form's `mode` is `flow` the canvas renders in
 * place. The canvas — `@xyflow/react`, its stylesheet and dagre — is a
 * separate chunk loaded on demand here, so neither the editor's first load nor
 * the `/s/[slug]` session bundle carries it (PLAN-V2 V2-16, R-V2-18).
 */
const FlowCanvas = dynamic(() => import("./flow-canvas").then((mod) => mod.FlowCanvas), {
  ssr: false,
  loading: () => (
    <LoadingRegion label="Loading the flow canvas" className="flex flex-col gap-3">
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-[520px] w-full" />
    </LoadingRegion>
  ),
});

const FLOW_PARTS: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: SquareStackIcon,
    title: "Nodes",
    body: "Each step has its own instructions and tools, on top of the base instructions.",
  },
  {
    icon: GitForkIcon,
    title: "Transitions",
    body: "Paths between steps decide where the call goes next, based on what the caller said.",
  },
  {
    icon: BracesIcon,
    title: "Variables",
    body: "Values collected along the way are saved on the session and sent with its webhooks.",
  },
];

/** Prompt mode: what a flow is, and the way in. */
function FlowEmptyState({ agent }: { agent: AgentOut }) {
  return (
    <div data-slot="flow-empty" className="mx-auto w-full max-w-3xl overflow-hidden rounded-lg border border-border bg-card">
      <EmptyState
        icon={WorkflowIcon}
        title="This agent runs on a single prompt"
        description="Switch to a flow to run calls as a series of steps, for intake, qualification or anything that follows a set order."
        action={
          <Button type="button" onClick={() => openModeSwitch(agent.id)}>
            <Icon as={WorkflowIcon} size="sm" />
            Switch to flow
          </Button>
        }
        className="py-10"
      />
      <ul className="grid border-t border-border sm:grid-cols-3">
        {FLOW_PARTS.map((part) => (
          <li
            key={part.title}
            className="flex gap-3 border-border p-4 not-last:border-b sm:not-last:border-r sm:not-last:border-b-0"
          >
            <span className="mt-0.5 text-muted-foreground">
              <Icon as={part.icon} size="sm" />
            </span>
            <span className="flex flex-col gap-0.5">
              <span className="text-sm font-medium text-foreground">{part.title}</span>
              <span className="text-[0.8125rem] text-muted-foreground">{part.body}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FlowSection({ agent }: { agent: AgentOut }) {
  const editor = useEditorContext();
  const { control } = useFormContext<AgentEditorForm>();
  const mode = useWatch({ control, name: "mode" }) ?? "prompt";
  if (mode !== "flow") return <FlowEmptyState agent={agent} />;
  return (
    <div className="flex flex-col gap-3" data-slot="flow-section">
      <p className="text-sm text-muted-foreground">
        Each step runs with the agent&apos;s{" "}
        <button
          type="button"
          className="font-medium text-foreground underline underline-offset-2"
          onClick={() => editor?.goToSection("instructions")}
        >
          base instructions (apply to every node)
        </button>
        , then the global rules, then the step&apos;s own instructions. Click a node or path to edit it; drag
        from a node&apos;s bottom handle to connect it.
      </p>
      <FlowCanvas agent={agent} />
    </div>
  );
}
