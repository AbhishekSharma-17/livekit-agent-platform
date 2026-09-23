import * as React from "react";

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * The Dialog primitive that replaced the side sheets (side drawers are not
 * allowed — docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §5): the `size` panel layout,
 * and focus going back to whatever opened the dialog even without a
 * `DialogTrigger` (Radix alone would drop it on `<body>`).
 */

function Panel({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Version history</DialogTitle>
          <DialogDescription>Every save is a version.</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <input aria-label="Filter" />
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

describe("DialogContent size", () => {
  it("renders a labelled panel with a scrolling body", () => {
    render(<Panel open onOpenChange={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Version history" });
    expect(dialog.getAttribute("data-layout")).toBe("panel");
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    expect(dialog.querySelector('[data-slot="dialog-body"]')).not.toBeNull();
  });

  it("keeps the compact layout when no size is given", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Delete agent?</DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog").getAttribute("data-layout")).toBeNull();
  });
});

describe("DialogContent focus return", () => {
  it("returns focus to a button that opened it from state", async () => {
    function Host() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            History
          </button>
          <Panel open={open} onOpenChange={setOpen} />
        </>
      );
    }
    render(<Host />);
    const opener = screen.getByRole("button", { name: "History" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("returns focus to the menu's trigger when a menu item opened it", async () => {
    function Host() {
      const [menuOpen, setMenuOpen] = React.useState(true);
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button type="button" id="more-options">
            More test options
          </button>
          {menuOpen ? (
            <div role="menu" aria-labelledby="more-options">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(true);
                  // Like Radix's dropdown: the item unmounts after the dialog has opened.
                  window.setTimeout(() => setMenuOpen(false), 0);
                }}
              >
                Test chat…
              </button>
            </div>
          ) : null}
          <Panel open={open} onOpenChange={setOpen} />
        </>
      );
    }
    render(<Host />);
    const item = screen.getByRole("menuitem", { name: "Test chat…" });
    item.focus();
    fireEvent.click(item);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "More test options" })));
  });
});
