import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LkapLogo } from "@/components/shared/lkap-logo";
import { LKAP_MARK } from "@/components/shared/lkap-logo-geometry";

afterEach(cleanup);

/**
 * Our own logo (docs/ui/DESIGN-SYSTEM.md sections 5 and 7.1): an inline SVG
 * drawn from the brand tokens, named once, decorative beside visible text.
 */
describe("LkapLogo", () => {
  it("full: one image named LKAP, with the wordmark visible and the mark decorative", () => {
    const { container } = render(<LkapLogo />);
    const logo = screen.getByRole("img", { name: "LKAP" });
    expect(logo.getAttribute("data-variant")).toBe("full");
    expect(logo.textContent).toBe("LKAP");
    expect(container.querySelector('[data-slot="lkap-mark"]')?.getAttribute("aria-hidden")).toBe("true");
  });

  it("full with a product: the name and the visible words both read LKAP Console", () => {
    render(<LkapLogo product="Console" />);
    const logo = screen.getByRole("img", { name: "LKAP Console" });
    expect(logo.textContent).toBe("LKAP Console");
  });

  it("mark: decorative by default, named LKAP only when labelled", () => {
    const { container } = render(<LkapLogo variant="mark" />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(container.querySelector('[data-slot="lkap-logo"]')?.getAttribute("aria-hidden")).toBe("true");
    cleanup();
    render(<LkapLogo variant="mark" labelled />);
    expect(screen.getByRole("img", { name: "LKAP" }).textContent).toBe("");
  });

  it.each([
    ["sm", "size-5"],
    ["md", "size-7"],
    ["lg", "size-10"],
  ] as const)("draws the %s mark at %s", (size, cls) => {
    const { container } = render(<LkapLogo variant="mark" size={size} />);
    expect(container.querySelector('[data-slot="lkap-mark"]')?.getAttribute("class")).toContain(cls);
  });

  it("paints only with the brand tokens, from the shared geometry", () => {
    const { container } = render(<LkapLogo variant="mark" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe(`0 0 ${LKAP_MARK.size} ${LKAP_MARK.size}`);
    expect(svg?.innerHTML).not.toMatch(/#[0-9a-f]{3,6}\b|rgb\(|oklch\(/i);
    const tile = svg?.querySelector("rect");
    expect(tile?.getAttribute("class")).toBe("fill-brand");
    expect(tile?.getAttribute("rx")).toBe(String(LKAP_MARK.radius));
    const ink = svg?.querySelectorAll(".fill-brand-foreground") ?? [];
    expect(ink).toHaveLength(1 + LKAP_MARK.bars.length);
  });
});
