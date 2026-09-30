import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SessionLoadError, SessionSkeleton } from "@/components/session/session-skeleton";

afterEach(cleanup);

/**
 * S7 (docs/ui/AUDIT.md §6): the embed's loading placeholder is a skeleton that
 * mirrors the layout it stands in for, in a labelled status region, not an
 * empty `h-dvh` div; a chunk that fails to load offers a Reload.
 */
describe("SessionSkeleton", () => {
  it.each([
    ["text", "Loading the chat"],
    ["voice", "Loading the call"],
  ] as const)("announces the %s embed's loading state and draws skeleton blocks", (channel, label) => {
    const { container } = render(<SessionSkeleton channel={channel} />);
    const region = screen.getByRole("status");
    expect(region.textContent).toBe(label);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(2);
  });

  it("keeps the embed frame full height", () => {
    render(<SessionSkeleton channel="text" />);
    expect(screen.getByRole("status").className).toContain("h-dvh");
  });
});

describe("SessionLoadError", () => {
  it("says what happened and offers a reload", () => {
    render(<SessionLoadError />);
    expect(screen.getByRole("alert").textContent).toMatch(/didn't load/);
    expect(screen.getByRole("button", { name: /reload/i })).toBeTruthy();
  });
});
