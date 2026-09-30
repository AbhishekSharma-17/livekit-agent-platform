"use client";
import Link from "next/link";

import { readOnlyCopy } from "@/components/console/shared/permission";
import { useWriteAccess, type Role } from "@/components/console/lib/roles";
import { Button } from "@/components/ui/button";

import { ReadOnlyNote } from "./read-only-note";

interface NewResourceButtonBaseProps {
  children: React.ReactNode;
  /** Role floor to create this resource; defaults to `"builder"`. */
  min?: Role;
  size?: React.ComponentProps<typeof Button>["size"];
  /** Defaults to `primary`: a "New …" button is the page's one main action. */
  variant?: React.ComponentProps<typeof Button>["variant"];
  /** What a person below the floor reads instead, e.g. "Ask an admin to add connections." */
  readOnlyNote?: string;
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
 * A page-header or empty-state "New X" button, gated by role (decision D12):
 * below the role floor it is **replaced by a read-only note** that names the
 * next step, never a disabled button behind a tooltip. While the role is
 * still loading the button renders disabled, so nothing flashes. `href`
 * navigates to a create page; `onClick` opens a create dialog. Used from
 * server-component pages as a client child of PageHeader's `actions`.
 */
export function NewResourceButton({
  children,
  min = "builder",
  size,
  variant = "primary",
  readOnlyNote,
  className,
  ...target
}: NewResourceButtonProps) {
  const { canWrite, isLoading } = useWriteAccess(min);
  if (isLoading) {
    return (
      <Button type="button" size={size} variant={variant} className={className} disabled>
        {children}
      </Button>
    );
  }
  if (!canWrite) {
    return <ReadOnlyNote className={className}>{readOnlyNote ?? readOnlyCopy(min)}</ReadOnlyNote>;
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
