import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SimpleSelect, type SimpleSelectOption } from "@/components/ui/select";

/** docs/ui/DESIGN-SYSTEM.md section 6.3: the one custom select, a drop-in for native `<select>`. */
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OPTIONS: SimpleSelectOption[] = [
  { value: "", label: "No trunk" },
  { value: "t1", label: "Main line", group: "Trunks" },
  { value: "t2", label: "Overflow", group: "Trunks" },
];

function Harness({ onChange }: { onChange?: (value: string) => void }) {
  const [value, setValue] = React.useState("");
  return (
    <>
      <label htmlFor="trunk">Trunk</label>
      <SimpleSelect
        id="trunk"
        value={value}
        options={OPTIONS}
        onValueChange={(next) => {
          setValue(next);
          onChange?.(next);
        }}
      />
    </>
  );
}

describe("SimpleSelect", () => {
  it("is named by its label, shows the empty-string option and styles the trigger per spec", () => {
    render(<Harness />);
    const trigger = screen.getByRole("combobox", { name: "Trunk" });
    expect(trigger.textContent).toContain("No trunk");
    expect(trigger.className).toContain("h-9");
    expect(trigger.className).toContain("border-input");
    expect(trigger.querySelector("svg")?.getAttribute("class")).toContain("lucide-chevrons-up-down");
  });

  it("opens from the keyboard, groups options and maps values back to plain strings", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const trigger = screen.getByRole("combobox", { name: "Trunk" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeTruthy();
    expect(screen.getByText("Trunks")).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "Overflow" }));
    expect(onChange).toHaveBeenCalledWith("t2");
    expect(screen.getByRole("combobox", { name: "Trunk" }).textContent).toContain("Overflow");

    fireEvent.keyDown(screen.getByRole("combobox", { name: "Trunk" }), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: "No trunk" }));
    expect(onChange).toHaveBeenLastCalledWith("");
  });
});
