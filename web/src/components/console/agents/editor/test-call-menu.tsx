"use client";

import * as React from "react";
import { toast } from "sonner";
import { ChevronDownIcon, PhoneIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { publicUrl, type SaveOutcome } from "./publish-popover";
import type { ResolvedEditorSlots } from "./registry";

export function testCallPath(slug: string): string {
  return `/s/${slug}?mode=test`;
}

/**
 * Opens a tab synchronously (inside the click, so popup blockers allow it),
 * then points it at the test page once the save resolves — or closes it.
 */
async function saveThenOpen(saveNow: () => Promise<SaveOutcome | null>, path: string) {
  const tab = window.open("about:blank", "_blank");
  if (tab) tab.opener = null;
  const outcome = await saveNow();
  if (!outcome) {
    tab?.close();
    return;
  }
  if (tab) tab.location.href = path;
  else window.open(path, "_blank", "noopener,noreferrer");
}

export interface TestCallMenuProps {
  agent: AgentOut;
  dirty: boolean;
  saveNow: () => Promise<SaveOutcome | null>;
  extraItems?: ResolvedEditorSlots["testCallItems"];
}

/**
 * "Test call" split button (docs/UI_UX_SPEC.md §4.3, §4.8). Primary: opens
 * `/s/<slug>?mode=test` in a new tab; when the form is dirty it asks first
 * ("Save and test" / "Test the saved version"), because the test page runs
 * the saved config. Menu: Save and test (dirty only), Copy test link, Open
 * public page (live only), then extension items (Test chat, Call a number).
 */
export function TestCallMenu({ agent, dirty, saveNow, extraItems = [] }: TestCallMenuProps) {
  const [askOpen, setAskOpen] = React.useState(false);
  const path = testCallPath(agent.slug);

  async function copyTestLink() {
    try {
      await navigator.clipboard.writeText(publicUrl(agent.slug, true));
      toast.success("Test link copied");
    } catch {
      toast.error("Couldn't copy the link");
    }
  }

  const primary = dirty ? (
    <Popover open={askOpen} onOpenChange={setAskOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="rounded-r-none">
          <Icon as={PhoneIcon} size="md" />
          Test call
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(20rem,calc(100vw-2rem))]">
        <div className="flex flex-col gap-3">
          <p className="text-sm font-semibold">You have unsaved changes</p>
          <p className="text-[0.8125rem] text-pretty text-muted-foreground">
            The test call runs the saved configuration.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button asChild variant="outline" size="sm">
              <a href={path} target="_blank" rel="noopener noreferrer" onClick={() => setAskOpen(false)}>
                Test the saved version
              </a>
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setAskOpen(false);
                void saveThenOpen(saveNow, path);
              }}
            >
              Save and test
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  ) : (
    <Button asChild variant="outline" className="rounded-r-none">
      <a href={path} target="_blank" rel="noopener noreferrer">
        <Icon as={PhoneIcon} size="md" />
        Test call
      </a>
    </Button>
  );

  return (
    <div className="inline-flex" data-slot="test-call">
      {primary}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" size="icon" className="-ml-px rounded-l-none" aria-label="More test options">
            <Icon as={ChevronDownIcon} size="md" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-52">
          {dirty ? (
            <DropdownMenuItem onSelect={() => void saveThenOpen(saveNow, path)}>Save and test</DropdownMenuItem>
          ) : null}
          <DropdownMenuItem onSelect={() => void copyTestLink()}>Copy test link</DropdownMenuItem>
          {agent.published ? (
            <DropdownMenuItem asChild>
              <a href={`/s/${agent.slug}`} target="_blank" rel="noopener noreferrer">
                Open public page
              </a>
            </DropdownMenuItem>
          ) : null}
          {extraItems.length > 0 ? <DropdownMenuSeparator /> : null}
          {extraItems.map((Item, index) => (
            <Item key={index} agent={agent} dirty={dirty} />
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
