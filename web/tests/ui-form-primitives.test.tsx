import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PlusIcon } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChipInput, validateEmail } from "@/components/shared/chip-input";
import { CheckboxRow, OptionCard, SwitchRow } from "@/components/shared/choice";
import { FieldRow, FormError } from "@/components/shared/field";
import { FileInput } from "@/components/shared/file-input";
import { PasswordInput, SecretInput } from "@/components/shared/password-input";
import { SearchField } from "@/components/shared/search-field";
import { Button, IconButton } from "@/components/ui/button";

afterEach(cleanup);

describe("Button (spec 6.1)", () => {
  it("renders variant-less buttons as secondary and keeps the caller's data-variant", () => {
    render(
      <>
        <Button>Cancel</Button>
        <Button variant="primary">Save</Button>
        <Button variant="outline">Old outline</Button>
      </>,
    );
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(cancel.getAttribute("data-variant")).toBe("default");
    expect(cancel.className).toContain("bg-card");
    expect(cancel.className).not.toContain("bg-brand");
    expect(screen.getByRole("button", { name: "Save" }).className).toContain("bg-brand");
    expect(screen.getByRole("button", { name: "Old outline" }).getAttribute("data-variant")).toBe("outline");
  });

  it("swaps a text button's label while busy and disables it", () => {
    render(
      <Button variant="primary" busy busyLabel="Saving…">
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.querySelector('[data-slot="spinner"]')).toBeNull();
  });

  it("swaps an icon button's icon for the spinner while busy", () => {
    render(
      <IconButton label="Add" busy>
        <PlusIcon />
      </IconButton>,
    );
    const button = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.querySelector('[data-slot="spinner"]')).not.toBeNull();
    expect(button.querySelector("svg")).toBeNull();
  });
});

describe("SearchField (spec 6.2)", () => {
  function Harness({ initial = "" }: { initial?: string }) {
    const [value, setValue] = React.useState(initial);
    return <SearchField aria-label="Search agents" value={value} onValueChange={setValue} />;
  }

  it("shows the clear button only with a value and clears on Escape", () => {
    render(<Harness />);
    const input = screen.getByRole("searchbox", { name: "Search agents" }) as HTMLInputElement;
    expect(screen.queryByRole("button", { name: "Clear search" })).toBeNull();
    fireEvent.change(input, { target: { value: "claims" } });
    expect(screen.getByRole("button", { name: "Clear search" })).toBeTruthy();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
    expect(screen.queryByRole("button", { name: "Clear search" })).toBeNull();
  });

  it("lets Escape through when empty, so a surrounding dialog can close", () => {
    const onKeyDown = vi.fn();
    render(
      <div onKeyDown={(event) => onKeyDown(event.key)}>
        <Harness />
      </div>,
    );
    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
    expect(onKeyDown).toHaveBeenCalledWith("Escape");
  });

  it("clears with the X button", () => {
    render(<Harness initial="front" />);
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
  });
});

describe("ChipInput (spec 6.2)", () => {
  function Harness() {
    const [values, setValues] = React.useState<string[]>([]);
    return <ChipInput aria-label="Invite" itemLabel="email" values={values} onValuesChange={setValues} validate={validateEmail} />;
  }

  const input = () => screen.getByRole("textbox", { name: "Invite" });
  const chips = () => Array.from(document.querySelectorAll('[data-slot="chip"]')).map((chip) => chip.textContent);

  it.each(["Enter", ",", ";"])("commits a chip on %s", (key) => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: "ada@example.com" } });
    fireEvent.keyDown(input(), { key });
    expect(chips()).toEqual(["aada@example.com"]);
    expect(screen.getByRole("status").textContent).toBe("Added ada@example.com.");
  });

  it("splits a pasted list and keeps invalid chips, flagged", () => {
    render(<Harness />);
    fireEvent.paste(input(), { clipboardData: { getData: () => "ada@example.com, not-an-email; bo@example.com" } });
    const nodes = document.querySelectorAll('[data-slot="chip"]');
    expect(nodes).toHaveLength(3);
    expect(nodes[1].hasAttribute("data-invalid")).toBe(true);
    expect(nodes[1].textContent).toContain("(Not an email address)");
    expect(input().getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Added 3 emails. not-an-email: Not an email address.");
  });

  it("removes a chip with its button and with Backspace, and announces it", () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: "a@b.co c@d.co" } });
    fireEvent.keyDown(input(), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Remove a@b.co" }));
    expect(screen.getByRole("status").textContent).toBe("Removed a@b.co.");
    fireEvent.keyDown(input(), { key: "Backspace" });
    expect(document.querySelectorAll('[data-slot="chip"]')).toHaveLength(0);
  });

  it("commits what is typed on blur and ignores duplicates", () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: "x@y.io" } });
    fireEvent.blur(input());
    fireEvent.change(input(), { target: { value: "x@y.io" } });
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(document.querySelectorAll('[data-slot="chip"]')).toHaveLength(1);
  });
});

describe("Password and secret inputs (spec 6.2)", () => {
  it("toggles an account password's visibility", () => {
    render(<PasswordInput aria-label="Password" defaultValue="hunter2" />);
    const field = screen.getByLabelText("Password") as HTMLInputElement;
    expect(field.type).toBe("password");
    const toggle = screen.getByRole("button", { name: "Show password" });
    fireEvent.click(toggle);
    expect(field.type).toBe("text");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps secrets write-only: masked, no reveal, empty when saved", () => {
    render(<SecretInput aria-label="API key" value="" onChange={() => {}} saved />);
    const field = screen.getByLabelText("API key") as HTMLInputElement;
    expect(field.type).toBe("password");
    expect(field.value).toBe("");
    expect(field.placeholder).toMatch(/Saved/);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("Choice controls (spec 6.2)", () => {
  it("wires checkbox rows, option cards and switch rows to their labels and descriptions", () => {
    render(
      <>
        <CheckboxRow label="Record calls" description="Stored for 30 days" />
        <OptionCard name="plan" value="a" title="Voice" description="Callers talk to the agent" />
        <SwitchRow label="Interruptions" description="Let callers talk over the agent" />
      </>,
    );
    const checkbox = screen.getByRole("checkbox", { name: "Record calls" });
    expect(document.getElementById(checkbox.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Stored for 30 days");
    const radio = screen.getByRole("radio", { name: /Voice/ });
    fireEvent.click(radio);
    expect((radio as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("switch", { name: "Interruptions" }).getAttribute("aria-describedby")).toBeTruthy();
  });

  it("renders field rows, the form error block and the file input", () => {
    const { container } = render(
      <>
        <FieldRow columns={3}>
          <span>a</span>
        </FieldRow>
        <FormError>That name is already taken.</FormError>
        <FormError />
        <FileInput aria-label="Upload" />
      </>,
    );
    expect(container.querySelector('[data-slot="field-row"]')?.className).toContain("grid-cols-1");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect((screen.getByLabelText("Upload") as HTMLInputElement).type).toBe("file");
  });
});
