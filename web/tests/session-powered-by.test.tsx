import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SessionCard, SessionCardScreen } from "@/components/session/session-card";

describe("Powered by LKAP on the caller page cards", () => {
  it("shows the LKAP mark and name under the card", () => {
    render(
      <SessionCardScreen>
        <SessionCard>Card</SessionCard>
      </SessionCardScreen>,
    );
    const line = screen.getByTestId("powered-by-lkap");
    expect(line.textContent).toBe("Powered byLKAP");
    // Plain text, never a link that would take a caller away from the call.
    expect(line.querySelector("a")).toBeNull();
    expect(line.querySelector("svg")).not.toBeNull();
  });

  it("can be turned off", () => {
    render(
      <SessionCardScreen attribution={false}>
        <SessionCard>Card</SessionCard>
      </SessionCardScreen>,
    );
    expect(screen.queryByTestId("powered-by-lkap")).toBeNull();
  });
});
