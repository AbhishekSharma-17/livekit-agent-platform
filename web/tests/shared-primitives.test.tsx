import * as React from "react";

import { act, fireEvent, render, screen } from "@testing-library/react";
import { BotIcon } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AGENT_STATE_LABEL,
  AGENT_UI_STATES,
  CapabilityBadge,
  CopyButton,
  COPY_FEEDBACK_MS,
  DescriptionList,
  EmptyState,
  Field,
  Icon,
  Kbd,
  METER_STATES,
  PageHeader,
  RelativeTime,
  ResponsiveTable,
  STATE_LABEL,
  Section,
  SectionRow,
  StateMeter,
  StatusChip,
  VendorMark,
  toMeterState,
  vendorMonogram,
  type CapabilityKind,
  type StatusTone,
} from "@/components/shared";

afterEach(() => {
  vi.useRealTimers();
});

describe("agent-state vocabulary", () => {
  it("labels every session state with the single vocabulary", () => {
    expect(AGENT_UI_STATES.map((state) => AGENT_STATE_LABEL[state])).toEqual([
      "Connecting",
      "Listening",
      "Thinking",
      "Speaking",
      "Reconnecting",
      "Failed",
      "Ended",
    ]);
  });

  it("maps reconnecting onto the connecting meter and passes the rest through", () => {
    expect(toMeterState("reconnecting")).toBe("connecting");
    expect(toMeterState("speaking")).toBe("speaking");
    expect(toMeterState("failed")).toBe("failed");
  });
});

describe("StateMeter", () => {
  it.each(METER_STATES)("renders %s with data-state and its aria-label", (state) => {
    const { container } = render(<StateMeter state={state} size="sm" />);
    const meter = container.querySelector('[data-slot="state-meter"]');
    expect(meter?.getAttribute("data-state")).toBe(state);
    expect(meter?.getAttribute("role")).toBe("img");
    expect(meter?.getAttribute("aria-label")).toBe(STATE_LABEL[state]);
  });

  it.each([
    ["xs", undefined, 2],
    ["sm", undefined, 4],
    ["md", undefined, 4],
    ["lg", undefined, 5],
    ["sm", 5, 5],
    ["lg", 4, 4],
    ["xs", 5, 2],
  ] as const)("size %s with bars=%s draws %i bars", (size, bars, expected) => {
    const { container } = render(<StateMeter state="listening" size={size} bars={bars} />);
    expect(container.querySelectorAll("[data-bar]")).toHaveLength(expected);
    expect(container.querySelector('[data-slot="state-meter"]')?.getAttribute("data-size")).toBe(size);
  });

  it("marks the outer bars as edges (listening breathes on them)", () => {
    const { container } = render(<StateMeter state="listening" size="md" />);
    const edges = Array.from(container.querySelectorAll("[data-bar]")).map((bar) => bar.hasAttribute("data-edge"));
    expect(edges).toEqual([true, false, false, true]);
  });

  it("renders a visible label when asked, without double-announcing it", () => {
    render(<StateMeter state="thinking" size="sm" label />);
    expect(screen.getByRole("img", { name: "Thinking" })).toBeTruthy();
    const text = screen.getByText("Thinking");
    expect(text.getAttribute("aria-hidden")).toBe("true");
  });

  it("switches to level-driven bars when a level is passed (clamped 0–1)", () => {
    const { container } = render(<StateMeter state="speaking" size="lg" level={2} />);
    const meter = container.querySelector('[data-slot="state-meter"]');
    expect(meter?.hasAttribute("data-level")).toBe(true);
    const middle = container.querySelectorAll<HTMLElement>("[data-bar]")[2];
    expect(middle.style.getPropertyValue("--level-scale")).toBe("1");
  });
});

describe("StatusChip", () => {
  it.each([
    ["neutral", "bg-muted"],
    ["info", "bg-info-soft"],
    ["success", "bg-success-soft"],
    ["warning", "bg-warning-soft"],
    ["danger", "bg-danger-soft"],
    ["live", "bg-brand-soft"],
  ] as const)("tone %s uses token classes (%s)", (tone: StatusTone, bg) => {
    const { container } = render(<StatusChip tone={tone}>Label</StatusChip>);
    const chip = container.querySelector('[data-slot="status-chip"]');
    expect(chip?.getAttribute("data-tone")).toBe(tone);
    expect(chip?.className).toContain(bg);
    expect(chip?.className).toContain("rounded-xs");
    expect(chip?.className).not.toMatch(/emerald|amber|sky|blue-|red-/);
  });

  it("live renders a decorative xs state meter", () => {
    const { container } = render(<StatusChip tone="live">Live</StatusChip>);
    const meter = container.querySelector('[data-slot="state-meter"]');
    expect(meter?.getAttribute("data-size")).toBe("xs");
    expect(meter?.closest('[aria-hidden="true"]')).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(container.textContent).toBe("Live");
  });

  it("shows a dot only when asked", () => {
    const { container, rerender } = render(<StatusChip tone="danger">Failed</StatusChip>);
    expect(container.querySelector(".bg-danger")).toBeNull();
    rerender(
      <StatusChip tone="danger" dot size="sm">
        Failed
      </StatusChip>,
    );
    expect(container.querySelector(".bg-danger")).toBeTruthy();
  });
});

describe("Icon", () => {
  it("is decorative by default with the 1.75 absolute stroke", () => {
    const { container } = render(<Icon as={BotIcon} size="lg" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.getAttribute("width")).toBe("20");
    expect(svg?.getAttribute("data-size")).toBe("lg");
  });

  it("gets a name and img role when labelled", () => {
    render(<Icon as={BotIcon} label="Agent" />);
    expect(screen.getByRole("img", { name: "Agent" })).toBeTruthy();
  });
});

describe("Field", () => {
  it("binds the label and wires hint + error onto the control", () => {
    render(
      <Field label="Name" htmlFor="agent-name" hint="Shown to callers" error="Name is required" required>
        <input id="agent-name" />
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-required")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe("agent-name-hint agent-name-error");
    expect(document.getElementById("agent-name-hint")?.textContent).toBe("Required · Shown to callers");
    expect(document.getElementById("agent-name-error")?.textContent).toBe("Name is required");
  });

  it("marks optional fields in the hint slot and leaves valid controls alone", () => {
    render(
      <Field label="Description" htmlFor="desc" optional>
        <textarea id="desc" aria-describedby="external" />
      </Field>,
    );
    const textarea = screen.getByLabelText("Description");
    expect(textarea.getAttribute("aria-invalid")).toBeNull();
    expect(textarea.getAttribute("aria-describedby")).toBe("external desc-hint");
    expect(document.getElementById("desc-hint")?.textContent).toBe("Optional");
    expect(document.getElementById("desc-error")).toBeNull();
  });

  it("lays out inline fields as label column + control column", () => {
    const { container } = render(
      <Field label="Let callers interrupt" htmlFor="interrupt" inline>
        <input id="interrupt" type="checkbox" />
      </Field>,
    );
    const field = container.querySelector('[data-slot="field"]');
    expect(field?.hasAttribute("data-inline")).toBe(true);
    expect(field?.className).toContain("grid-cols-[minmax(0,1fr)_auto]");
  });
});

describe("CopyButton", () => {
  it("copies, swaps to a check for 1.2 s and announces the result", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    render(<CopyButton value="https://example.test/s/claims" label="Copy public link" />);
    const button = screen.getByRole("button", { name: "Copy public link" });

    await act(async () => {
      fireEvent.click(button);
    });
    expect(writeText).toHaveBeenCalledWith("https://example.test/s/claims");
    expect(button.hasAttribute("data-copied")).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Copied");

    act(() => {
      vi.advanceTimersByTime(COPY_FEEDBACK_MS);
    });
    expect(button.hasAttribute("data-copied")).toBe(false);
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("announces a failure when the clipboard is unavailable", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    render(<CopyButton value="x" label="Copy id" size="xs" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy id" }));
    });
    expect(screen.getByRole("status").textContent).toBe("Couldn't copy");
  });
});

describe("ResponsiveTable", () => {
  interface Row {
    id: string;
    name: string;
  }
  const rows: Row[] = [
    { id: "a1", name: "Claims desk" },
    { id: "a2", name: "Front desk" },
  ];
  const columns = [
    { id: "name", header: "Agent", cell: (row: Row) => row.name },
    { id: "actions", header: "Actions", cell: () => <button type="button">Menu</button>, interactive: true },
  ];

  it("renders a table ≥ 768 px and a card list below, switched by CSS", () => {
    const { container } = render(
      <ResponsiveTable
        columns={columns}
        rows={rows}
        renderCard={(row) => <span>{row.name} card</span>}
        label="Agents"
      />,
    );
    const table = container.querySelector('[data-slot="responsive-table-table"]');
    const cards = container.querySelector('[data-slot="responsive-table-cards"]');
    expect(table?.className).toContain("hidden");
    expect(table?.className).toContain("md:block");
    expect(cards?.className).toContain("md:hidden");
    expect(screen.getByRole("table", { name: "Agents" })).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Agent", "Actions"]);
    expect(cards?.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("Front desk card")).toBeTruthy();
  });

  it("makes rows and cards link targets when rowHref is set", () => {
    const { container } = render(
      <ResponsiveTable
        columns={columns}
        rows={rows}
        renderCard={(row) => <span>{row.name}</span>}
        rowHref={(row) => `/console/agents/${row.id}`}
      />,
    );
    const rowLink = container.querySelector('[data-slot="responsive-table-table"] a');
    expect(rowLink?.getAttribute("href")).toBe("/console/agents/a1");
    expect(rowLink?.textContent).toBe("Claims desk");
    // Interactive cells are lifted above the stretched link.
    const actionCell = screen.getAllByRole("button", { name: "Menu" })[0].closest("td");
    expect(actionCell?.className).toContain("z-10");

    const cardLinks = container.querySelectorAll('[data-slot="responsive-table-cards"] a');
    expect(cardLinks).toHaveLength(2);
    const labelledBy = cardLinks[1].getAttribute("aria-labelledby") ?? "";
    expect(document.getElementById(labelledBy)?.textContent).toBe("Front desk");
    expect(cardLinks[1].getAttribute("href")).toBe("/console/agents/a2");
  });

  it("renders the empty slot when there are no rows", () => {
    render(
      <ResponsiveTable
        columns={columns}
        rows={[]}
        renderCard={() => null}
        empty={<EmptyState title="No agents yet" />}
      />,
    );
    expect(screen.getByText("No agents yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("EmptyState", () => {
  it("renders icon, title, description and one action", () => {
    const { container } = render(
      <EmptyState
        icon={BotIcon}
        title="No agents yet"
        description="An agent is a voice or video assistant."
        action={<button type="button">New agent</button>}
      />,
    );
    expect(screen.getByRole("heading", { name: "No agents yet" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "New agent" })).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("has a compact one-line variant", () => {
    const { container } = render(<EmptyState compact title="No HTTP tools yet" />);
    expect(container.querySelector('[data-slot="empty-state"]')?.hasAttribute("data-compact")).toBe(true);
    expect(screen.queryByRole("heading")).toBeNull();
  });
});

describe("PageHeader", () => {
  it("renders breadcrumbs, the h1, description and actions", () => {
    render(
      <PageHeader
        title="Stage9 Insurance"
        description="Claim intake"
        eyebrow="Agent"
        breadcrumbs={[{ label: "Agents", href: "/console/agents" }, { label: "Stage9 Insurance" }]}
        actions={<button type="button">Save</button>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Stage9 Insurance" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Agents" }).getAttribute("href")).toBe("/console/agents");
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    const current = screen.getAllByText("Stage9 Insurance").find((node) => node.getAttribute("aria-current") === "page");
    expect(current).toBeTruthy();
  });

  it("keeps the old { title, description, actions } call shape working", () => {
    const { container } = render(<PageHeader title="Sessions" description="Every session" sticky />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sessions");
    expect(container.querySelector("header")?.className).toContain("sticky");
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("Section, DescriptionList, VendorMark, CapabilityBadge, Kbd, RelativeTime", () => {
  it("Section labels its region by the title and separates rows with hairlines", () => {
    render(
      <Section id="voice" title="Voice" description="How the agent greets" aside={<span>aside</span>}>
        <SectionRow>Row one</SectionRow>
        <SectionRow compact>Row two</SectionRow>
      </Section>,
    );
    const region = screen.getByRole("region", { name: "Voice" });
    expect(region.id).toBe("voice");
    expect(region.querySelector('[data-slot="section-body"]')?.className).toContain("divide-y");
  });

  it("DescriptionList renders term/detail pairs with mono details on request", () => {
    const { container } = render(
      <DescriptionList
        columns={3}
        items={[
          { term: "Turns", detail: 12 },
          { term: "Room", detail: "lkap-2e788a2a", mono: true },
        ]}
      />,
    );
    const details = container.querySelectorAll("dd");
    expect(container.querySelectorAll("dt")[1].textContent).toBe("Room");
    expect(details[1].className).toContain("font-mono");
    expect(details[0].className).not.toContain("font-mono");
    expect(container.querySelector("dl")?.className).toContain("md:grid-cols-3");
  });

  it.each([
    ["Deepgram", "De"],
    ["ElevenLabs", "EL"],
    ["OpenAI", "OA"],
    ["livekit-inference", "LI"],
    ["LiveKit Inference", "LI"],
    ["", "?"],
  ])("vendorMonogram(%s) → %s", (vendor, expected) => {
    expect(vendorMonogram(vendor)).toBe(expected);
  });

  it("VendorMark is a named image with a stable tint", () => {
    const { container } = render(<VendorMark vendor="Deepgram" />);
    const mark = screen.getByRole("img", { name: "Deepgram" });
    expect(mark.textContent).toBe("De");
    const first = mark.getAttribute("style");
    const { container: second } = render(<VendorMark vendor="Deepgram" size="lg" />);
    expect(second.querySelector('[data-slot="vendor-mark"]')?.getAttribute("style")).toBe(first);
    expect(container.innerHTML).not.toContain("<img");
  });

  it.each([
    ["vision", undefined, "Vision"],
    ["voices", 12, "12 voices"],
    ["voices", 1, "1 voice"],
    ["no-key", undefined, "No key needed"],
    ["key-required", undefined, "Key required"],
    ["silent-tools", undefined, "Silent tools"],
  ] as const)("CapabilityBadge %s (count %s) reads %s", (kind: CapabilityKind, count, text) => {
    const { container } = render(<CapabilityBadge kind={kind} count={count} />);
    expect(container.textContent).toBe(text);
  });

  it("CapabilityBadge accepts custom text (key-set with a fingerprint)", () => {
    const { container } = render(<CapabilityBadge kind="key-set">Team key · 3f9a</CapabilityBadge>);
    expect(container.textContent).toBe("Team key · 3f9a");
    expect(container.querySelector('[data-kind="key-set"]')).toBeTruthy();
  });

  it("Kbd wraps the shadcn kbd", () => {
    const { container } = render(<Kbd>⌘K</Kbd>);
    expect(container.querySelector('kbd[data-slot="kbd"]')?.textContent).toBe("⌘K");
  });

  it("RelativeTime shows the relative phrase after mount with the exact time in title", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-19T03:08:10Z"));
    render(<RelativeTime iso="2026-09-19T03:04:05Z" />);
    const time = document.querySelector("time");
    expect(time?.textContent).toBe("4 min ago");
    expect(time?.getAttribute("dateTime")).toBe("2026-09-19T03:04:05.000Z");
    expect(time?.getAttribute("title")).toMatch(/^19 Sep, \d{2}:\d{2}$/);
  });

  it("RelativeTime withExact shows both; invalid input renders a dash", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-19T03:08:10Z"));
    const { rerender } = render(<RelativeTime iso="2026-09-19T03:04:05Z" withExact />);
    expect(document.querySelector("time")?.textContent).toMatch(/^19 Sep, \d{2}:\d{2} \(4 min ago\)$/);
    rerender(<RelativeTime iso="garbage" />);
    expect(document.querySelector("time")).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
  });
});
