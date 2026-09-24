"use client";

import * as React from "react";
import Link from "next/link";
import { GlobeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/shared/icon";
import { CopyButton } from "@/components/shared/copy-button";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * "Add to website" (UI_UX_SPEC-V2-AMENDMENTS §1: "Widget (stretch) — inside
 * the agent editor header ('Add to website') — Dialog, not a page"). Mirrors
 * `console/telephony/call-number.tsx`'s store-backed
 * menu-item/host split, but as a `headerActions` entry (a button, not a menu
 * item) since it is not part of the Test call menu.
 */
type Listener = () => void;
let openAgentId: string | null = null;
const listeners = new Set<Listener>();

function setOpenAgent(id: string | null) {
  openAgentId = id;
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function useOpenAgent(): string | null {
  return React.useSyncExternalStore(
    subscribe,
    () => openAgentId,
    () => null,
  );
}

/** Header action button: opens the snippet dialog for `agent`. */
export function AddToWebsiteButton({ agent }: { agent: AgentOut }) {
  return (
    <Button type="button" variant="outline" onClick={() => setOpenAgent(agent.id)}>
      <Icon as={GlobeIcon} size="sm" />
      Add to website
    </Button>
  );
}

/** Renders nothing until the button opens it. */
export function AddToWebsiteDialogHost({ agent }: { agent: AgentOut }) {
  const open = useOpenAgent() === agent.id;
  return (
    <AddToWebsiteDialog
      agent={agent}
      open={open}
      onOpenChange={(next) => setOpenAgent(next ? agent.id : null)}
    />
  );
}

/** Exported for `tests/add-to-website-snippet.test.ts`. */
export function widgetSnippet(slug: string, origin: string): string {
  return `<script src="${origin}/widget.js" data-agent="${slug}"></script>`;
}

export function AddToWebsiteDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: AgentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // `window.location.origin` is where *this console* is served from, which is
  // also where `web/public/widget.js` and `/s/[slug]` are served from — the
  // same origin a production deployment would give an operator to paste.
  const [origin, setOrigin] = React.useState("");
  React.useEffect(() => {
    if (open) setOrigin(window.location.origin);
  }, [open]);

  const snippet = widgetSnippet(agent.slug, origin || "https://your-lkap-host");
  const hasOrigins = (agent.allowed_origins ?? []).length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add to website</DialogTitle>
          <DialogDescription>
            Paste this snippet into any page to add {agent.name} as a floating chat widget.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-4">
          {/* The copy button has its own gutter inside the box, so the scrolling
              snippet never slides underneath it. */}
          <div className="flex min-w-0 items-start rounded-md border border-border bg-muted/40">
            <pre
              tabIndex={0}
              aria-label="Widget snippet"
              className="min-w-0 flex-1 overflow-x-auto rounded-l-md py-3 pr-2 pl-3 font-mono text-xs leading-5 whitespace-pre text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
            >
              <code>{snippet}</code>
            </pre>
            <div className="shrink-0 p-1.5">
              <CopyButton value={snippet} label="Copy the widget snippet" />
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            {hasOrigins
              ? "This site is already in the agent's allowed origins."
              : "Add the website's address to this agent's allowed origins first, or the widget won't be able to start a chat."}{" "}
            <Link
              href={`/console/agents/${agent.id}?section=limits`}
              className="font-medium text-foreground underline underline-offset-4"
              onClick={() => onOpenChange(false)}
            >
              Manage allowed origins
            </Link>
          </p>
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}
