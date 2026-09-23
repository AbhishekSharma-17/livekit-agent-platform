import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SessionUnavailable } from "@/components/session/session-unavailable";

afterEach(cleanup);

/** UI_UX_SPEC §5.7 — three pages, three jobs, one card shell. */
describe("SessionUnavailable", () => {
  it("tells a visitor with a bad link what to do", () => {
    render(<SessionUnavailable kind="not_found" slug="missing" />);
    expect(screen.getByText(/There's no agent at this address/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /open in console/i })).toBeNull();
  });

  it("offers the builder a way in only when the agent is a draft", () => {
    render(<SessionUnavailable kind="not_published" slug="draft-agent" />);
    expect(screen.getByText(/This agent isn't live yet/)).toBeTruthy();
    const link = screen.getByRole("link", { name: /open in console/i });
    expect(link.getAttribute("href")).toBe("/console/agents?q=draft-agent");
  });

  it("offers a retry when the service is unreachable", () => {
    render(<SessionUnavailable kind="unreachable" slug="whatever" />);
    expect(screen.getByText(/We couldn't reach the service/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /open in console/i })).toBeNull();
  });

  it("keeps the API's own message for builders only", () => {
    const detail = "The console's admin token is not accepted by the API.";
    render(
      <SessionUnavailable kind="unreachable" slug="s" detail={detail} testMode />,
    );
    expect(screen.getByText(detail)).toBeTruthy();

    cleanup();
    render(<SessionUnavailable kind="unreachable" slug="s" detail={detail} />);
    expect(screen.queryByText(detail)).toBeNull();
  });

  it("always leaves a way out", () => {
    render(<SessionUnavailable kind="not_found" slug="s" />);
    expect(screen.getByRole("link", { name: /home/i }).getAttribute("href")).toBe(
      "/",
    );
  });
});
