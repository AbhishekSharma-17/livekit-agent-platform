"use client";

import { MessageSquareIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { TextSessionView } from "@/components/session/embed/text-session-view";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { setTestChatOpenAgent, useTestChatOpenAgent } from "./store";

/** Test call menu item: opens the drawer for `agent`. */
export function TestChatMenuItem({ agent }: { agent: AgentOut; dirty: boolean }) {
  return (
    <DropdownMenuItem onSelect={() => setTestChatOpenAgent(agent.id)}>
      <Icon as={MessageSquareIcon} size="sm" />
      Test chat…
    </DropdownMenuItem>
  );
}

/** Renders nothing until the menu item opens it (mirrors `CallNumberDialogHost`). */
export function TestChatDrawerHost({ agent }: { agent: AgentOut }) {
  const open = useTestChatOpenAgent() === agent.id;
  return (
    <TestChatDrawer agent={agent} open={open} onOpenChange={(next) => setTestChatOpenAgent(next ? agent.id : null)} />
  );
}

export function TestChatDrawer({
  agent,
  open,
  onOpenChange,
}: {
  agent: AgentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-border border-b">
          <SheetTitle>Test chat</SheetTitle>
          <SheetDescription>
            {agent.name} — text only, no audio. Edit a past message or replay a reply, using its saved
            configuration.
          </SheetDescription>
        </SheetHeader>
        {/* Mounted only while `open` (Radix unmounts `SheetContent`'s children when
            closed, but this is explicit so the text session never connects — and never
            starts a `POST .../text-sessions`, per-open session row — while the drawer
            is hidden): the room connects on mount and ends on unmount, exactly like
            `LiveSession`'s own effect. */}
        {open ? (
          <TextSessionView
            slug={agent.slug}
            viaConsole
            fallbackAgentName={agent.name}
            editable
            className="min-h-0"
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
