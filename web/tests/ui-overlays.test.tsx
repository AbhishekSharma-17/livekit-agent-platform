import * as React from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { Trash2Icon } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmDialog, TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { busyLabelFor, gerund } from "@/components/shared/busy-label";
import { RowMenu } from "@/components/shared/row-menu";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { TOAST_DURATION_MS, Toaster, applyToastPolicy } from "@/components/ui/sonner";
import { ApiError } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Dialog (spec 6.4)", () => {
  it("uses the spec surface: 14 px radius, modal shadow, 560 px default width, sticky muted footer", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename agent</DialogTitle>
          </DialogHeader>
          <DialogFooter>
            <Button>Cancel</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Rename agent" });
    expect(dialog.className).toContain("rounded-dialog");
    expect(dialog.className).toContain("shadow-modal");
    expect(dialog.className).toContain("sm:w-[min(calc(100vw-32px),560px)]");
    const footer = dialog.querySelector('[data-slot="dialog-footer"]');
    expect(footer?.className).toContain("sticky");
    expect(footer?.className).toContain("bg-muted");
    expect(screen.getByRole("button", { name: "Close" }).className).toContain("size-[30px]");
  });
});

describe("ConfirmDialog (spec 6.4, 9)", () => {
  it("is an alertdialog with a danger button and a gerund busy label", async () => {
    let resolve: () => void = () => {};
    const onConfirm = vi.fn(() => new Promise<void>((r) => (resolve = r)));
    render(
      <ConfirmDialog
        trigger={<Button variant="danger-outline">Delete</Button>}
        title="Delete “Front desk”?"
        description="The agent and its versions are removed. Sessions stay."
        confirmLabel="Delete agent"
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete “Front desk”?" });
    const confirm = within(dialog).getByRole("button", { name: "Delete agent" });
    expect(confirm.getAttribute("data-variant")).toBe("danger");
    fireEvent.click(confirm);
    expect(within(dialog).getByRole("button", { name: "Deleting agent…" })).toBeTruthy();
    await act(async () => resolve());
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  });

  it("stays open and explains a failure in plain words", async () => {
    render(
      <ConfirmDialog
        open
        title="Revoke key?"
        confirmLabel="Revoke key"
        onConfirm={() => Promise.reject(new ApiError(503, "unknown_error", "Service Unavailable"))}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Revoke key" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The service is unavailable right now. Try again in a moment.",
    );
    expect(screen.getByRole("alertdialog")).toBeTruthy();
  });

  it("uses a plain dialog and the primary button when not destructive", () => {
    render(<ConfirmDialog open title="Send this to the agent?" confirmLabel="Send" destructive={false} onConfirm={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Send this to the agent?" });
    expect(within(dialog).getByRole("button", { name: "Send" }).getAttribute("data-variant")).toBe("primary");
  });

  it("typed confirmation keeps the danger button disabled until the text matches", () => {
    render(
      <TypedConfirmDialog open title="Delete workspace?" confirmText="DELETE" confirmLabel="Delete workspace" onConfirm={() => {}}>
        <ul>
          <li>12 agents</li>
        </ul>
      </TypedConfirmDialog>,
    );
    const confirm = screen.getByRole("button", { name: "Delete workspace" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Type DELETE to confirm"), { target: { value: "DELETE" } });
    expect(confirm.disabled).toBe(false);
    expect(screen.getByText("12 agents")).toBeTruthy();
  });
});

describe("busy labels", () => {
  it.each([
    ["Save", "Saving"],
    ["Delete", "Deleting"],
    ["Stop", "Stopping"],
    ["Revoke", "Revoking"],
    ["Send", "Sending"],
    ["Cancel", "Cancelling"],
    ["Hang", "Hanging"],
    ["Run", "Running"],
    ["Free", "Freeing"],
  ])("%s -> %s", (verb, expected) => {
    expect(gerund(verb)).toBe(expected);
  });

  it("changes only the verb and adds an ellipsis", () => {
    expect(busyLabelFor("Delete agent")).toBe("Deleting agent…");
    expect(busyLabelFor("Saving…")).toBe("Saving…");
  });
});

describe("RowMenu (spec 6.4)", () => {
  it("puts the destructive item last, after a separator", () => {
    render(
      <RowMenu
        label="More actions for Front desk"
        actions={[{ label: "Rename", onSelect: () => {} }]}
        destructive={{ label: "Delete", icon: Trash2Icon, onSelect: () => {} }}
      />,
    );
    const trigger = screen.getByRole("button", { name: "More actions for Front desk" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    const items = screen.getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual(["Rename", "Delete"]);
    expect(items[1].getAttribute("data-variant")).toBe("destructive");
    expect(screen.getByRole("separator")).toBeTruthy();
  });

  it("renders nothing when there is nothing the person can do", () => {
    const { container } = render(<RowMenu label="More actions" />);
    expect(container.innerHTML).toBe("");
  });
});

describe("Toaster (spec 6.4)", () => {
  it("keeps errors until dismissed with role=alert, and auto-dismisses success after 6 s with role=status", async () => {
    vi.stubGlobal(
      "matchMedia",
      (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Toaster />);
    act(() => {
      toast.error("Couldn't save the agent.");
      toast.success("Agent saved.");
    });
    const error = await screen.findByText("Couldn't save the agent.");
    const success = await screen.findByText("Agent saved.");
    await waitFor(() => {
      expect(error.closest("[data-sonner-toast]")?.getAttribute("role")).toBe("alert");
      expect(success.closest("[data-sonner-toast]")?.getAttribute("role")).toBe("status");
    });
    await act(async () => {
      vi.advanceTimersByTime(TOAST_DURATION_MS + 1000);
    });
    await waitFor(() => expect(screen.queryByText("Agent saved.")).toBeNull());
    expect(screen.getByText("Couldn't save the agent.")).toBeTruthy();
  });

  it("applies the policy once, and a caller's own duration still wins", () => {
    const error = vi.fn();
    const warning = vi.fn();
    const target = { error, warning } as unknown as typeof toast;
    applyToastPolicy(target);
    applyToastPolicy(target);
    target.error("x");
    target.warning("y", { duration: 1000 });
    expect(error).toHaveBeenCalledWith("x", { duration: Number.POSITIVE_INFINITY, closeButton: true });
    expect(warning).toHaveBeenCalledWith("y", { duration: 1000, closeButton: true });
  });
});
