"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { GatedButton } from "@/components/shared/gated-button";
import { useWriteAccess, writeAccessReason, type Role } from "@/components/console/lib/roles";

export interface NewResourceButtonProps {
  href: string;
  children: React.ReactNode;
  /** Role floor to create this resource; defaults to `"builder"`. */
  min?: Role;
}

/**
 * A page-header/empty-state "New X" nav button, gated by role
 * (docs/v2/_asks.md V2-20-5). A next/link Link cannot be disabled the way a
 * button can, so below the role floor this renders a plain disabled button
 * with a tooltip instead of the link — a viewer never lands on a create form
 * they can't submit. Used from server-component pages (app/console/*\/page.tsx)
 * as a client child passed into PageHeader's actions prop.
 */
export function NewResourceButton({ href, children, min = "builder" }: NewResourceButtonProps) {
  const { canWrite } = useWriteAccess(min);
  if (canWrite) {
    return (
      <Button asChild>
        <Link href={href}>{children}</Link>
      </Button>
    );
  }
  return (
    <GatedButton allowed={false} reason={writeAccessReason(min)}>
      {children}
    </GatedButton>
  );
}
