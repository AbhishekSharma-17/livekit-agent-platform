"use client";

import * as React from "react";
import { MoreHorizontalIcon, type LucideIcon } from "lucide-react";

import { IconButton } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface RowMenuAction {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  disabled?: boolean;
}

export interface RowMenuProps {
  /** Accessible name of the trigger, e.g. "More actions for Front desk". */
  label: string;
  /** Ordinary items, top to bottom. */
  actions?: RowMenuAction[];
  /** Extra menu content rendered after `actions` (groups with labels …). */
  children?: React.ReactNode;
  /** The destructive item; always rendered last, after a separator, in destructive text. */
  destructive?: RowMenuAction;
  size?: "default" | "sm";
}

/**
 * Row "…" menu (docs/ui/DESIGN-SYSTEM.md section 6.4): an icon-button
 * trigger (`MoreHorizontal`), the popup aligned to the trigger's end at a
 * 4 px offset, 34 px items with 16 px secondary icons. The destructive item
 * always comes last, after a separator. Renders nothing when there is
 * nothing the person can do (decision D12: hide actions they can't use).
 */
export function RowMenu({ label, actions = [], children, destructive, size = "default" }: RowMenuProps) {
  if (actions.length === 0 && !children && !destructive) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton label={label} size={size}>
          <MoreHorizontalIcon />
        </IconButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={4} className="w-auto">
        {actions.map((action) => (
          <DropdownMenuItem key={action.label} disabled={action.disabled} onSelect={action.onSelect}>
            {action.icon ? <action.icon aria-hidden="true" /> : null}
            {action.label}
          </DropdownMenuItem>
        ))}
        {children}
        {destructive ? (
          <>
            {actions.length > 0 || children ? <DropdownMenuSeparator /> : null}
            <DropdownMenuItem variant="destructive" disabled={destructive.disabled} onSelect={destructive.onSelect}>
              {destructive.icon ? <destructive.icon aria-hidden="true" /> : null}
              {destructive.label}
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
