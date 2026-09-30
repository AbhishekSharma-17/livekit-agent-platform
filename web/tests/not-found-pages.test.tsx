import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ConsoleNotFound from "@/app/console/not-found";
import NotFound from "@/app/not-found";

/**
 * Both not-found pages (docs/ui/DESIGN-SYSTEM.md sections 6.1 and 9, AUDIT
 * P4): exactly one primary action, placed last, after the secondary links.
 */
function actionOrder() {
  return screen.getAllByRole("link").map((link) => ({
    name: link.textContent,
    variant: link.getAttribute("data-variant"),
  }));
}

describe("not-found pages", () => {
  it("root: one h1, Open console first and Go home as the one primary, last", () => {
    render(<NotFound />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(actionOrder()).toEqual([
      { name: "Open console", variant: "secondary" },
      { name: "Go home", variant: "primary" },
    ]);
    expect(screen.getByRole("link", { name: "Go home" }).getAttribute("href")).toBe("/");
  });

  it("console: Agents and Sessions first, Overview as the one primary, last", () => {
    render(<ConsoleNotFound />);

    expect(actionOrder()).toEqual([
      { name: "Agents", variant: "secondary" },
      { name: "Sessions", variant: "secondary" },
      { name: "Overview", variant: "primary" },
    ]);
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("href")).toBe("/console");
  });
});
