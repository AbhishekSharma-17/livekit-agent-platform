"use client";

import Link from "next/link";
import { ArrowRightIcon, BookOpenIcon, KeyRoundIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";

const LINKS = [
  { href: "/console/providers", label: "Add credential", icon: KeyRoundIcon },
  { href: "/console/knowledge", label: "New knowledge base", icon: BookOpenIcon },
];

/**
 * The Overview aside's shortcuts. "New agent" is the page header's primary
 * action (decision D6), so it isn't repeated here.
 */
export function QuickActions() {
  return (
    <Section id="quick-actions" title="Quick actions">
      {LINKS.map((action) => (
        <SectionRow key={action.href} compact>
          <Link
            href={action.href}
            className="group flex w-full items-center gap-3 rounded-sm text-control font-medium text-foreground hover:text-brand"
          >
            <Icon as={action.icon} size="md" className="text-text-secondary group-hover:text-brand" />
            <span className="flex-1">{action.label}</span>
            <Icon as={ArrowRightIcon} size="sm" className="text-text-tertiary" />
          </Link>
        </SectionRow>
      ))}
    </Section>
  );
}
