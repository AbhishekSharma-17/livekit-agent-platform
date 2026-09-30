import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BotIcon } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState, NoMatches } from "@/components/shared/empty-state";
import { LoadingRow } from "@/components/shared/loading-state";
import { ProgressSteps, StepList } from "@/components/shared/progress-steps";
import { LifecycleBadge, StatusPill } from "@/components/shared/status-chip";
import { LIFECYCLE, humanizeStatus, lifecycleStatus } from "@/components/shared/status-map";
import { Tag, TagList } from "@/components/shared/tag";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { SkeletonText } from "@/components/ui/skeleton";

afterEach(cleanup);

describe("Alert (spec 6.5)", () => {
  it.each([
    ["info", "status", "lucide-info"],
    ["success", "status", "lucide-circle-check"],
    ["warning", "status", "lucide-triangle-alert"],
    ["danger", "alert", "lucide-circle-alert"],
    ["brand", "status", "lucide-info"],
    ["neutral", "status", "lucide-info"],
  ] as const)("%s uses role %s and its icon", (tone, role, iconClass) => {
    render(
      <Alert tone={tone} title="Heads up" actions={<button type="button">Retry</button>}>
        Body copy
      </Alert>,
    );
    const alert = screen.getByRole(role);
    expect(alert.getAttribute("data-tone")).toBe(tone);
    expect(alert.querySelector("svg")?.getAttribute("class")).toContain(iconClass);
    expect(screen.getByText("Heads up")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("keeps the legacy variant API (children bring the icon)", () => {
    render(<Alert variant="warning">Legacy</Alert>);
    expect(screen.getByRole("status").textContent).toBe("Legacy");
  });
});

describe("EmptyState and NoMatches (spec 6.5)", () => {
  it("draws the dashed card with a 40 px icon tile and one action", () => {
    const { container } = render(
      <EmptyState icon={BotIcon} title="No agents yet" description="Create one to start taking calls." action={<button type="button">New agent</button>} />,
    );
    const empty = container.querySelector('[data-slot="empty-state"]');
    expect(empty?.className).toContain("border-dashed");
    expect(empty?.querySelector(".size-10")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "No agents yet" })).toBeTruthy();
  });

  it("no-matches names the query and clears the filters", () => {
    const onClear = vi.fn();
    render(<NoMatches items="agents" query=" front " onClear={onClear} />);
    expect(screen.getByRole("heading", { name: "No agents match “front”" })).toBeTruthy();
    expect(screen.getByText("Try a different name, word or category.")).toBeTruthy();
    expect(document.querySelector(".lucide-search-x")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onClear).toHaveBeenCalled();
  });
});

describe("Loading, skeleton and progress (spec 6.5)", () => {
  it("skeleton text has a ragged last line", () => {
    const { container } = render(<SkeletonText lines={3} />);
    const lines = container.querySelectorAll('[data-slot="skeleton"]');
    expect(lines).toHaveLength(3);
    expect(lines[2].className).toContain("w-3/5");
  });

  it("loading row is a status region with a spinner and a label", () => {
    render(<LoadingRow label="Loading documents" />);
    const row = screen.getByRole("status");
    expect(row.textContent).toBe("Loading documents");
    expect(row.querySelector('[data-slot="spinner"]')).toBeTruthy();
  });

  it("progress steps expose the bar, mark the current step and announce it", () => {
    render(
      <ProgressSteps
        label="Indexing progress"
        steps={[
          { id: "a", label: "Reading files", status: "done" },
          { id: "b", label: "Embedding chunks", status: "current" },
          { id: "c", label: "Publishing", status: "upcoming" },
        ]}
      />,
    );
    const bar = screen.getByRole("progressbar", { name: "Indexing progress" });
    expect(bar.getAttribute("aria-valuenow")).toBe("33");
    expect(bar.getAttribute("aria-valuemax")).toBe("100");
    const current = document.querySelector('[aria-current="step"]');
    expect(current?.textContent).toContain("Embedding chunks");
    expect(screen.getByRole("status").textContent).toBe("Step 2 of 3: Embedding chunks");
  });

  it("step list says each state in words", () => {
    render(<StepList steps={[{ id: "a", label: "Upload", status: "failed" }]} />);
    expect(screen.getByText("(Failed)")).toBeTruthy();
  });
});

describe("Status pill, lifecycle map, badge and tag (spec 6.6)", () => {
  it("maps api states to human labels and tones in one place", () => {
    expect(lifecycleStatus("needs_review")).toEqual({ tone: "warning", label: "Ready to review" });
    expect(lifecycleStatus("join_failed")).toEqual({ tone: "danger", label: "Couldn't join" });
    expect(lifecycleStatus("live")).toEqual({ tone: "live", label: "Live" });
    expect(lifecycleStatus("In progress")).toEqual({ tone: "info", label: "In progress" });
    expect(lifecycleStatus("needs_client_registration")).toEqual({ tone: "neutral", label: "Needs client registration" });
    expect(lifecycleStatus(undefined).label).toBe("Unknown");
    expect(humanizeStatus("not-connected")).toBe("Not connected");
  });

  it("gives every mapped state a sentence-case human label", () => {
    for (const [state, status] of Object.entries(LIFECYCLE)) {
      expect(status.label, state).toMatch(/^[A-Z][^_]*$/);
    }
  });

  it("lifecycle badge renders the mapped word, tone and dot", () => {
    const { container } = render(<LifecycleBadge state="indexing" />);
    const pill = container.querySelector('[data-slot="status-chip"]');
    expect(pill?.getAttribute("data-tone")).toBe("info");
    expect(pill?.textContent).toBe("Indexing");
    expect(pill?.querySelector('[data-slot="status-dot"]')).toBeTruthy();
  });

  it("status pill is 22 px by default", () => {
    const { container } = render(<StatusPill tone="success">Ready</StatusPill>);
    expect(container.firstElementChild?.className).toContain("h-[22px]");
  });

  it("badge is a 22 px hairline pill; tag is squarer on the card fill", () => {
    const { container } = render(
      <>
        <Badge tone="warning">Beta</Badge>
        <TagList>
          <Tag>Billing</Tag>
        </TagList>
      </>,
    );
    const badge = container.querySelector('[data-slot="badge"]');
    expect(badge?.className).toContain("rounded-pill");
    expect(badge?.className).toContain("bg-warning-subtle");
    const tag = container.querySelector('[data-slot="tag"]');
    expect(tag?.className).toContain("rounded-sm");
    expect(tag?.className).toContain("bg-card");
  });
});
