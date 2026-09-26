import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { SearchableSelect, type SearchableSelectOption } from "@/components/ui/searchable-select";

/**
 * `SearchableSelect` (Console -> Tools -> Apps' category filter and other long
 * pickers): a `Popover` + `Command` combobox in place of a native `<select>`.
 * jsdom needs the same shims `console-model-combobox.test.tsx` uses for the
 * same Radix Popover + cmdk combination — `:popover-open`/`:modal`,
 * `scrollIntoView` and `ResizeObserver`.
 */

const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
  Element.prototype.scrollIntoView = nativeScrollIntoView;
});
beforeEach(() => {
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
  vi.unstubAllGlobals();
});

const OPTIONS: SearchableSelectOption[] = [
  { value: "crm", label: "CRM", count: 12 },
  { value: "scheduling", label: "Scheduling", count: 3 },
  { value: "developer-tools", label: "Developer tools", count: 41 },
];

function Harness({
  initial = "",
  options = OPTIONS,
  onChangeSpy,
}: {
  initial?: string;
  options?: SearchableSelectOption[];
  onChangeSpy?: (value: string) => void;
}) {
  const [value, setValue] = React.useState(initial);
  return (
    <SearchableSelect
      aria-label="Category"
      allOption={{ value: "all", label: "All categories" }}
      options={options}
      value={value}
      onValueChange={(next) => {
        setValue(next);
        onChangeSpy?.(next);
      }}
    />
  );
}

// cmdk's own search input also carries `role="combobox"` (the ARIA combobox
// pattern) and the same accessible name (cmdk wires it to the same label),
// so once the popover is open `getByRole("combobox", {name})` matches both
// it and the trigger button — disambiguate by tag instead.
function trigger(name: string): HTMLElement {
  const candidates = screen.getAllByRole("combobox", { name });
  const found = candidates.find((el) => el.tagName === "BUTTON");
  if (!found) throw new Error(`no trigger button named "${name}"`);
  return found;
}

function open(name = "Category") {
  fireEvent.click(trigger(name));
}

describe("SearchableSelect", () => {
  it("shows 'All categories' on the trigger with nothing picked, and every option once opened", async () => {
    render(<Harness />);
    expect(trigger("Category").textContent).toContain("All categories");
    open();
    expect(await screen.findByText("CRM")).toBeTruthy();
    expect(screen.getByText("Scheduling")).toBeTruthy();
    expect(screen.getByText("Developer tools")).toBeTruthy();
  });

  it("renders each option's count badge", async () => {
    render(<Harness />);
    open();
    await screen.findByText("CRM");
    const row = screen.getByText("CRM").closest("[cmdk-item]") as HTMLElement;
    expect(within(row).getByText("12")).toBeTruthy();
  });

  it("typing narrows the list to matching options", async () => {
    render(<Harness />);
    open();
    const input = await screen.findByPlaceholderText("Search…");
    fireEvent.change(input, { target: { value: "sched" } });
    await waitFor(() => expect(screen.queryByText("CRM")).toBeNull());
    expect(screen.getByText("Scheduling")).toBeTruthy();
  });

  it("a pinned option (a 'Custom…' escape) stays visible under a search that matches nothing else", async () => {
    function PinnedHarness() {
      const [value, setValue] = React.useState("");
      return (
        <SearchableSelect
          aria-label="Category"
          groups={[
            { options: OPTIONS },
            { options: [{ value: "custom", label: "Other value…", pinned: true }] },
          ]}
          value={value}
          onValueChange={setValue}
        />
      );
    }
    render(<PinnedHarness />);
    open();
    const input = await screen.findByPlaceholderText("Search…");
    fireEvent.change(input, { target: { value: "nothing-like-this" } });
    expect(await screen.findByText("Nothing matches.")).toBeTruthy();
    expect(screen.getByText("Other value…")).toBeTruthy();
  });

  it("shows the empty state when nothing matches", async () => {
    render(<Harness />);
    open();
    const input = await screen.findByPlaceholderText("Search…");
    fireEvent.change(input, { target: { value: "nothing-like-this" } });
    expect(await screen.findByText("Nothing matches.")).toBeTruthy();
  });

  it("picking an option closes the popover, updates the trigger and fires onValueChange", async () => {
    const onChangeSpy = vi.fn();
    render(<Harness onChangeSpy={onChangeSpy} />);
    open();
    fireEvent.click(await screen.findByText("Scheduling"));
    expect(onChangeSpy).toHaveBeenCalledWith("scheduling");
    expect(trigger("Category").textContent).toContain("Scheduling");
    await waitFor(() => expect(screen.queryByPlaceholderText("Search…")).toBeNull());
  });

  it("keyboard: ArrowDown then Enter selects the highlighted option", async () => {
    const onChangeSpy = vi.fn();
    render(<Harness onChangeSpy={onChangeSpy} />);
    open();
    const input = await screen.findByPlaceholderText("Search…");
    // cmdk highlights the pinned "All categories" row by default; one
    // ArrowDown moves into the option list's first row ("CRM").
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChangeSpy).toHaveBeenCalledWith("crm");
  });

  it("stays visible while filtering and resets the value when picked ('All' pin)", async () => {
    const onChangeSpy = vi.fn();
    render(<Harness initial="scheduling" onChangeSpy={onChangeSpy} />);
    expect(trigger("Category").textContent).toContain("Scheduling");
    open();
    const input = await screen.findByPlaceholderText("Search…");
    fireEvent.change(input, { target: { value: "crm" } });
    // "All categories" never matches "crm" textually but stays pinned above the results.
    expect(screen.getByText("All categories")).toBeTruthy();
    fireEvent.click(screen.getByText("All categories"));
    expect(onChangeSpy).toHaveBeenCalledWith("all");
    expect(trigger("Category").textContent).toContain("All categories");
  });

  it("groups render under their own heading", async () => {
    function GroupHarness() {
      const [value, setValue] = React.useState("");
      return (
        <SearchableSelect
          aria-label="Agent"
          groups={[
            { heading: "Front desk", options: [{ value: "a1", label: "Alice" }] },
            { heading: "Backend", options: [{ value: "a2", label: "Bob" }] },
          ]}
          value={value}
          onValueChange={setValue}
          placeholder="Choose an agent"
        />
      );
    }
    render(<GroupHarness />);
    fireEvent.click(trigger("Agent"));
    expect(await screen.findByText("Front desk")).toBeTruthy();
    expect(screen.getByText("Backend")).toBeTruthy();
    expect(screen.getByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
  });

  it("multi-select toggles membership and keeps the popover open", async () => {
    function MultiHarness() {
      const [values, setValues] = React.useState<string[]>([]);
      return (
        <SearchableSelect
          aria-label="Categories"
          options={OPTIONS}
          multiple
          values={values}
          onValuesChange={setValues}
          value={null}
          onValueChange={() => {}}
          placeholder="Any category"
        />
      );
    }
    render(<MultiHarness />);
    fireEvent.click(trigger("Categories"));
    fireEvent.click(await screen.findByText("CRM"));
    // Still open: multi-select doesn't close on pick.
    expect(screen.getByPlaceholderText("Search…")).toBeTruthy();
    expect(trigger("Categories").textContent).toContain("CRM");
    fireEvent.click(screen.getByText("Scheduling"));
    expect(trigger("Categories").textContent).toContain("2 selected");
  });
});
