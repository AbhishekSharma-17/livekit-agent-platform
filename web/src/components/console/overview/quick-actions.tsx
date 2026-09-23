import Link from "next/link";
import { BookOpenIcon, BotIcon, KeyRoundIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";

const ACTIONS = [
  { href: "/console/agents/new", label: "New agent", icon: BotIcon },
  { href: "/console/providers", label: "Add credential", icon: KeyRoundIcon },
  { href: "/console/knowledge", label: "New knowledge base", icon: BookOpenIcon },
];

/** docs/UI_UX_SPEC.md §4.1: "Quick actions": New agent · Add credential · New knowledge base. */
export function QuickActions() {
  return (
    <Section id="quick-actions" title="Quick actions">
      {ACTIONS.map((action) => (
        <SectionRow key={action.href}>
          <Link
            href={action.href}
            className="flex items-center gap-3 text-sm font-medium text-foreground hover:text-brand-text"
          >
            <Icon as={action.icon} size="md" className="text-muted-foreground" />
            {action.label}
          </Link>
        </SectionRow>
      ))}
    </Section>
  );
}
