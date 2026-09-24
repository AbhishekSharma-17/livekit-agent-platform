"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { GatedButton } from "@/components/shared/gated-button";
import { useWriteAccess, writeAccessReason, type Role } from "@/components/console/lib/roles";

interface NewResourceButtonBaseProps {
  children: React.ReactNode;
  /** Role floor to create this resource; defaults to `"builder"`. */
  min?: Role;
  size?: React.ComponentProps<typeof Button>["size"];
  variant?: React.ComponentProps<typeof Button>["variant"];
  className?: string;
}

/** Navigates to a create page. */
interface NewResourceLinkProps extends NewResourceButtonBaseProps {
  href: string;
  onClick?: never;
}

/** Opens a create dialog in place (v4: the New agent dialog, R-V4-2). */
interface NewResourceActionProps extends NewResourceButtonBaseProps {
  onClick: () => void;
  href?: never;
}

export type NewResourceButtonProps = NewResourceLinkProps | NewResourceActionProps;

/**
 * A page-header/empty-state "New X" button, gated by role
 * (docs/v2/_asks.md V2-20-5). Two forms: `href` navigates to a create page,
 * `onClick` opens a create dialog. A next/link Link cannot be disabled the
 * way a button can, so below the role floor either form renders a plain
 * disabled button with a tooltip instead — a viewer never reaches a create
 * form they can't submit. Used from server-component pages
 * (app/console/*\/page.tsx) as a client child passed into PageHeader's
 * actions prop.
 */
export function NewResourceButton({ children, min = "builder", size, variant, className, ...target }: NewResourceButtonProps) {
  const { canWrite } = useWriteAccess(min);
  if (!canWrite) {
    return (
      <GatedButton allowed={false} reason={writeAccessReason(min)} size={size} variant={variant} className={className}>
        {children}
      </GatedButton>
    );
  }
  if (target.onClick) {
    return (
      <Button type="button" size={size} variant={variant} className={className} onClick={target.onClick}>
        {children}
      </Button>
    );
  }
  return (
    <Button asChild size={size} variant={variant} className={className}>
      <Link href={target.href as string}>{children}</Link>
    </Button>
  );
}
