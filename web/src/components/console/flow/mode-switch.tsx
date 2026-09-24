"use client";

import * as React from "react";
import { ChevronDownIcon, MessageSquareTextIcon, WorkflowIcon } from "lucide-react";
import { useFormContext, useWatch } from "react-hook-form";

import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AgentOut, FlowSpec } from "@/contracts/lkap-contracts";

import { normalizeFlow, seedFlow } from "./flow-model";

/**
 * Which agent's switch dialog is open. Module state (like
 * `test-chat/add-to-website-dialog.tsx`) so the Flow section's empty state can
 * open the one dialog the header chip renders instead of duplicating it.
 */
type Listener = () => void;
let openFor: string | null = null;
const listeners = new Set<Listener>();

function setOpenFor(agentId: string | null) {
  openFor = agentId;
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Opens `agentId`'s prompt ↔ flow switch dialog (rendered by `ModeSwitchChip`). */
export function openModeSwitch(agentId: string) {
  setOpenFor(agentId);
}

/**
 * The header's mode chip with the prompt ↔ flow switch (V2-16, UI_UX_SPEC-V2
 * §2.3). The api derives `mode` from `config.flow` (R-V2-12), so the switch
 * always changes both: to flow it restores the agent's saved flow or seeds a
 * start node and a first step; to prompt it clears `config.flow` (`null`).
 * Nothing is saved until the author presses Save; the instructions stay the
 * base prompt of every step (R-V2-13).
 */
export function ModeSwitchChip({ agent }: { agent: AgentOut }) {
  const { control, getValues, setValue } = useFormContext<AgentEditorForm>();
  const mode = useWatch({ control, name: "mode" }) ?? "prompt";
  const editor = useEditorContext();
  const open = React.useSyncExternalStore(
    subscribe,
    () => openFor === agent.id,
    () => false,
  );
  const setOpen = React.useCallback((next: boolean) => setOpenFor(next ? agent.id : null), [agent.id]);
  React.useEffect(() => () => {
    if (openFor === agent.id) openFor = null;
  }, [agent.id]);
  const toFlow = mode !== "flow";
  const stepCount = normalizeFlow(getValues("config.flow") ?? null).nodes.length;

  function confirm() {
    if (toFlow) {
      const saved = normalizeFlow(agent.config.flow ?? null);
      const flow = saved.nodes.length > 0 ? saved : seedFlow();
      setValue("config.flow", flow as FlowSpec, { shouldDirty: true });
      setValue("mode", "flow", { shouldDirty: true });
      setOpen(false);
      window.setTimeout(() => editor?.goToSection("flow"), 0);
    } else {
      setValue("config.flow", null, { shouldDirty: true });
      setValue("mode", "prompt", { shouldDirty: true });
      setOpen(false);
      window.setTimeout(() => editor?.goToSection("instructions"), 0);
    }
  }

  const isFlow = mode === "flow";
  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="xs"
        data-slot="mode-chip"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="font-medium text-muted-foreground"
      >
        <Icon as={isFlow ? WorkflowIcon : MessageSquareTextIcon} size="sm" />
        <span className="sr-only">Mode: </span>
        {isFlow ? "Flow" : "Prompt"}
        <Icon as={ChevronDownIcon} size="sm" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{toFlow ? "Switch to a flow?" : "Switch back to a single prompt?"}</DialogTitle>
            <DialogDescription>
              {toFlow
                ? "Calls will follow a graph of steps with their own instructions, tools and paths. Your instructions stay as the base prompt for every step. We'll start you with a start node and one step."
                : `Calls will use the instructions alone. The flow (${stepCount} ${stepCount === 1 ? "node" : "nodes"}) is removed when you save — you can bring it back from version history.`}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">Nothing changes until you save.</p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={confirm}>
              {toFlow ? "Use a flow" : "Use a prompt"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
