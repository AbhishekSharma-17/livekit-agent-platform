"use client";

import * as React from "react";
import Link from "next/link";
import { GlobeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add to website</DialogTitle>
          <DialogDescription>
            Paste this on any page to embed {agent.name} as a floating chat widget (`widget.js`,
            V2-18).
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-start gap-2">
          <pre className="bg-muted flex-1 overflow-x-auto rounded-md p-3 font-mono text-xs">
            <code>{snippet}</code>
          </pre>
          <CopyButton value={snippet} label="Copy the widget snippet" />
        </div>
        <p className="text-muted-foreground text-sm">
          {hasOrigins
            ? "This site is already in the agent's allowed origins."
            : "The embedding site must be added to this agent's allowed origins first, or the widget's session will be refused."}{" "}
          <Link
            href={`/console/agents/${agent.id}?section=limits`}
            className="underline underline-offset-4"
            onClick={() => onOpenChange(false)}
          >
            Manage allowed origins
          </Link>
          .
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
