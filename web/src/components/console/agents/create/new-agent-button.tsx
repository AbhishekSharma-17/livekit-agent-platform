"use client";

import * as React from "react";

import { NewResourceButton, type NewResourceButtonProps } from "@/components/shared/new-resource-button";

import { CreateAgentDialog } from "./create-agent-dialog";

export type NewAgentButtonProps = Omit<Extract<NewResourceButtonProps, { onClick: () => void }>, "onClick" | "children"> & {
  children?: React.ReactNode;
};

/**
 * "New agent" (every trigger: the agents page header, the agents table's
 * empty state, the overview's setup checklist): opens the New agent dialog
 * in place (R-V4-2) instead of navigating. The role gate stays — a viewer
 * gets the disabled button with its tooltip, and the dialog never mounts.
 */
export function NewAgentButton({ children = "New agent", ...props }: NewAgentButtonProps) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <NewResourceButton {...props} onClick={() => setOpen(true)}>
        {children}
      </NewResourceButton>
      <CreateAgentDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
