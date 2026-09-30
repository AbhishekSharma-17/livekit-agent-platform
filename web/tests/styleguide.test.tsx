import * as React from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Styleguide } from "@/components/preview/styleguide";

/**
 * `/console/preview/styleguide` (docs/ui/DESIGN-SYSTEM.md section 11): a
 * render smoke test, since this package never starts a dev server. It renders
 * every specimen without throwing, and its theme preview toggles the `dark`
 * class on <html> and restores it on unmount.
 */
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", resolvedTheme: "light", setTheme: vi.fn() }),
}));

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("dark");
});

describe("Styleguide", () => {
  it("renders every specimen section", () => {
    render(<Styleguide />);
    expect(screen.getByRole("heading", { level: 1, name: "Styleguide" })).toBeTruthy();
    for (const name of [
      "Colour tokens",
      "Type scale",
      "Buttons",
      "Status, badges and tags",
      "Fields",
      "Select and combobox",
      "Segmented control and tabs",
      "Alerts",
      "Stats, cards, list cards and tables",
      "Empty, no matches, loading and progress",
      "Dialogs, menus, popovers, tooltips and toasts",
    ]) {
      expect(screen.getByRole("region", { name }), name).toBeTruthy();
    }
    expect(screen.getAllByRole("button", { name: /Primary|Secondary/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole("table", { name: "Agent costs" })).toBeTruthy();
  });

  it("previews dark mode on <html> and restores the original class on leave", () => {
    const { unmount } = render(<Styleguide />);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    const preview = screen.getByRole("radiogroup", { name: "Preview theme" });
    fireEvent.click(within(preview).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    unmount();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
