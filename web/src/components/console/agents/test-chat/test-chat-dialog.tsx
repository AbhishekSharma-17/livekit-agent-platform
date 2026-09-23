"use client";

import { MessageSquareIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TextSessionView } from "@/components/session/embed/text-session-view";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { setTestChatOpenAgent, useTestChatOpenAgent } from "./store";

/** Test call menu item: opens the Test chat dialog for `agent`. */
export function TestChatMenuItem({ agent }: { agent: AgentOut; dirty: boolean }) {
  return (
    <DropdownMenuItem onSelect={() => setTestChatOpenAgent(agent.id)}>
      <Icon as={MessageSquareIcon} size="sm" />
      Test chat…
    </DropdownMenuItem>
  );
}

/** Renders nothing until the menu item opens it (mirrors `CallNumberDialogHost`). */
export function TestChatDialogHost({ agent }: { agent: AgentOut }) {
  const open = useTestChatOpenAgent() === agent.id;
  return (
    <TestChatDialog agent={agent} open={open} onOpenChange={(next) => setTestChatOpenAgent(next ? agent.id : null)} />
  );
}

/**
 * The console's Test chat: a large modal (side drawers are not allowed —
 * UI_UX_SPEC-V2-AMENDMENTS §5). A fixed height, not just a cap, so the
 * transcript scrolls inside a stable frame and the composer stays put while
 * replies stream in; full-screen on phones.
 */
export function TestChatDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: AgentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg" className="sm:h-[min(85dvh,56rem)]">
        <DialogHeader>
          <DialogTitle>Test chat</DialogTitle>
          <DialogDescription>
            {agent.name} — text only, no audio. Edit a past message or replay a reply, using its saved
            configuration.
          </DialogDescription>
        </DialogHeader>
        {/* Mounted only while `open` (Radix unmounts `DialogContent`'s children when
            closed, but this is explicit so the text session never connects — and never
            starts a `POST .../text-sessions`, per-open session row — while the dialog
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
      </DialogContent>
    </Dialog>
  );
}
