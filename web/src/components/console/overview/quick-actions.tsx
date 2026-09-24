"use client";

import * as React from "react";
import Link from "next/link";
import { BookOpenIcon, BotIcon, KeyRoundIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { CreateAgentDialog } from "@/components/console/agents/create/create-agent-dialog";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { cn } from "@/lib/utils";

const ROW_CLASS =
  "flex w-full items-center gap-3 rounded-xs text-left text-sm font-medium text-foreground outline-none hover:text-brand-text focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";

const LINKS = [
  { href: "/console/providers", label: "Add credential", icon: KeyRoundIcon },
  { href: "/console/knowledge", label: "New knowledge base", icon: BookOpenIcon },
];

/**
 * docs/UI_UX_SPEC.md §4.1: "Quick actions": New agent · Add credential · New
 * knowledge base. "New agent" opens the New agent dialog in place (R-V4-2);
 * a viewer sees it disabled with the reason.
 */
export function QuickActions() {
  const { canWrite } = useWriteAccess();
  const [open, setOpen] = React.useState(false);
  return (
    <Section id="quick-actions" title="Quick actions">
      <SectionRow>
        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={!canWrite}
          title={canWrite ? undefined : writeAccessReason()}
          className={cn(ROW_CLASS, "disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:text-foreground")}
        >
          <Icon as={BotIcon} size="md" className="text-muted-foreground" />
          New agent
        </button>
        <CreateAgentDialog open={open} onOpenChange={setOpen} />
      </SectionRow>
      {LINKS.map((action) => (
        <SectionRow key={action.href}>
          <Link href={action.href} className={ROW_CLASS}>
            <Icon as={action.icon} size="md" className="text-muted-foreground" />
            {action.label}
          </Link>
        </SectionRow>
      ))}
    </Section>
  );
}
