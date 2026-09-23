"use client";

import * as React from "react";
import { useFormContext, useWatch } from "react-hook-form";

import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { FlowSpec } from "@/contracts/lkap-contracts";

import { normalizeFlow, type FlowDraft } from "./flow-model";

/**
 * The flow draft in the agent editor's form (`config.flow`, V2-16). The shell
 * owns Save/dirty state; every write here marks the form dirty.
 */
export function useFlowDraft() {
  const { control, getValues, setValue } = useFormContext<AgentEditorForm>();
  const stored = useWatch({ control, name: "config.flow" });
  const draft = React.useMemo(() => normalizeFlow(stored ?? null), [stored]);

  const update = React.useCallback(
    (next: FlowDraft | ((current: FlowDraft) => FlowDraft)) => {
      const current = normalizeFlow(getValues("config.flow") ?? null);
      const value = typeof next === "function" ? next(current) : next;
      setValue("config.flow", value as FlowSpec, { shouldDirty: true });
    },
    [getValues, setValue],
  );

  return { draft, update };
}
