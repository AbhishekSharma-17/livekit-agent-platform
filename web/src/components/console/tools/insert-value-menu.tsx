"use client";

import * as React from "react";
import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { CTX_LABELS, CTX_PLACEHOLDERS, VARIABLE_NAME_PATTERN, type CtxPlaceholder } from "@/components/console/tools/tool-context";

/**
 * "Insert value" (V6-11, D-V6-22): a small picker of `{{ ctx.* }}` (session facts) and
 * `{{ var.* }}` (flow/extraction variables) tokens, inserted at the field's cursor. Reused by
 * the HTTP tool's URL and body template, and by pinned-argument values on MCP tools and app
 * actions — never rendered beside a header field or an MCP server's own url (those refuse a
 * placeholder outright; render `neverOfferedHint` there instead of this menu).
 *
 * `disabledReason`, when set, disables the trigger and explains why in its title — used when
 * the field's cursor currently sits in a place a placeholder may not go (the URL's scheme,
 * host or port).
 */
export function InsertValueMenu({
  variableNames,
  onInsert,
  disabledReason,
  label = "Insert value",
}: {
  variableNames: readonly string[];
  onInsert: (token: string) => void;
  disabledReason?: string;
  label?: string;
}) {
  const [customOpen, setCustomOpen] = React.useState(false);
  const [customName, setCustomName] = React.useState("");
  const customValid = VARIABLE_NAME_PATTERN.test(customName.trim());

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (!open) {
          setCustomOpen(false);
          setCustomName("");
        }
      }}
    >
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0 gap-1"
          disabled={Boolean(disabledReason)}
          title={disabledReason}
        >
          {label}
          <ChevronDownIcon className="size-3.5" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Session value</DropdownMenuLabel>
        <DropdownMenuGroup>
          {(CTX_PLACEHOLDERS as readonly CtxPlaceholder[]).map((name) => (
            <DropdownMenuItem key={name} onSelect={() => onInsert(`{{ ctx.${name} }}`)}>
              <div className="flex min-w-0 flex-col">
                <span className="truncate text-sm">{CTX_LABELS[name]}</span>
                <span className="truncate font-mono text-[0.6875rem] text-muted-foreground">{`{{ ctx.${name} }}`}</span>
              </div>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Variable</DropdownMenuLabel>
        <DropdownMenuGroup>
          {variableNames.length === 0 ? (
            <p className="px-2 py-1.5 text-[0.8125rem] text-muted-foreground">No flow variables yet.</p>
          ) : null}
          {variableNames.map((name) => (
            <DropdownMenuItem key={name} onSelect={() => onInsert(`{{ var.${name} }}`)}>
              <span className="truncate font-mono text-xs">{`{{ var.${name} }}`}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        {customOpen ? (
          <div className="flex items-center gap-1.5 p-1.5" onKeyDown={(e) => e.stopPropagation()}>
            <Input
              autoFocus
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              placeholder="variable_name"
              aria-label="Variable name"
              className="h-7 font-mono text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter" && customValid) {
                  e.preventDefault();
                  onInsert(`{{ var.${customName.trim()} }}`);
                }
              }}
            />
            <Button type="button" size="sm" className="h-7 shrink-0 px-2" disabled={!customValid} onClick={() => onInsert(`{{ var.${customName.trim()} }}`)}>
              Insert
            </Button>
          </div>
        ) : (
          <DropdownMenuItem
            onSelect={(e) => {
              e.preventDefault();
              setCustomOpen(true);
            }}
          >
            Another variable…
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The plain-words explanation shown where `InsertValueMenu` is never offered (headers, an MCP server's url). */
export function neverOfferedHint(where: string): string {
  return `Session values and variables can't be used in ${where}.`;
}
