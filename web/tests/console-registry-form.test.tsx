import * as React from "react";

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { defaultFieldValues, RegistryForm } from "@/components/console/registry/registry-form";
import type { ProviderSpec } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * Acceptance criterion (docs/IMPLEMENTATION_PLAN.md W1-WEB-CONSOLE):
 * "vitest renders `RegistryForm` for every provider in `providers.json`
 * without errors." Reads the committed contracts export directly rather
 * than a copy, so this test can never drift from what `lkap_contracts`
 * actually generates (contracts/** is owned by W0-CONTRACTS; read-only
 * here).
 */
const providers = (providersJson as { providers: ProviderSpec[] }).providers;

describe("RegistryForm", () => {
  it("has at least the 18 MVP providers from the registry fixture", () => {
    const mvp = providers.filter((p) => p.status === "mvp");
    expect(mvp.length).toBeGreaterThanOrEqual(18);
  });

  it.each(providers.map((spec) => [spec.id, spec] as const))(
    "renders non-secret fields for %s without throwing",
    (_id, spec) => {
      const values = defaultFieldValues(spec.fields ?? []);
      const { container } = render(
        <RegistryForm fields={spec.fields ?? []} values={values} onChange={() => {}} idPrefix={spec.id} />,
      );
      expect(container).toBeTruthy();
    },
  );

  it.each(providers.map((spec) => [spec.id, spec] as const))(
    "renders secret fields for %s without throwing, masked",
    (_id, spec) => {
      const values = defaultFieldValues(spec.secret_fields ?? []);
      const { container } = render(
        <RegistryForm
          fields={spec.secret_fields ?? []}
          values={values}
          onChange={() => {}}
          secretsMasked
          idPrefix={`${spec.id}-secret`}
        />,
      );
      expect(container).toBeTruthy();
      for (const input of Array.from(container.querySelectorAll("input"))) {
        const field = (spec.secret_fields ?? []).find((f) => input.id.endsWith(f.name));
        if (field?.type === "secret") {
          expect(input.getAttribute("type")).toBe("password");
        }
      }
    },
  );

  it("respects a field's condition against a sibling value", () => {
    const fields: ProviderSpec["fields"] = [
      { name: "mode", label: "Mode", type: "enum", options: ["a", "b"], required: false },
      { name: "extra", label: "Extra", type: "string", condition: "mode=b", required: false },
    ];
    const { container: hidden } = render(
      <RegistryForm fields={fields ?? []} values={{ mode: "a" }} onChange={() => {}} idPrefix="cond" />,
    );
    expect(hidden.querySelector('label[for="cond-extra"]')).toBeNull();

    const { container: shown } = render(
      <RegistryForm fields={fields ?? []} values={{ mode: "b" }} onChange={() => {}} idPrefix="cond2" />,
    );
    expect(shown.querySelector('label[for="cond2-extra"]')).not.toBeNull();
  });

  it("marks required fields with the word Required, not an asterisk", () => {
    const fields: ProviderSpec["fields"] = [{ name: "region", label: "Region", type: "string", required: true }];
    const { container } = render(<RegistryForm fields={fields ?? []} values={{ region: "" }} onChange={() => {}} idPrefix="req" />);
    expect(container.textContent).toContain("Required");
    expect(container.textContent).not.toContain("*");
    expect(container.querySelector("#req-region")?.getAttribute("aria-required")).toBe("true");
  });

  it("renders a voice select from capabilities.voices, with a custom escape", () => {
    const fields: ProviderSpec["fields"] = [{ name: "voice", label: "Voice", type: "string", required: false }];
    const { container, rerender } = render(
      <RegistryForm fields={fields ?? []} values={{ voice: "" }} onChange={() => {}} idPrefix="v" voices={["Ashley", "Brooke"]} />,
    );
    const trigger = container.querySelector("#v-voice");
    expect(trigger?.getAttribute("role")).toBe("combobox");

    // No voices known → free text.
    rerender(<RegistryForm fields={fields ?? []} values={{ voice: "" }} onChange={() => {}} idPrefix="v" voices={[]} />);
    expect(container.querySelector("#v-voice")?.tagName).toBe("INPUT");
  });

  it("keeps an unknown stored voice editable as free text", () => {
    const fields: ProviderSpec["fields"] = [{ name: "voice", label: "Voice", type: "string", required: false }];
    const { container } = render(
      <RegistryForm fields={fields ?? []} values={{ voice: "my-cloned-voice" }} onChange={() => {}} idPrefix="cv" voices={["Ashley"]} />,
    );
    const input = container.querySelector("#cv-voice") as HTMLInputElement;
    expect(input.tagName).toBe("INPUT");
    expect(input.value).toBe("my-cloned-voice");
    expect(screen.getByRole("button", { name: "Pick from list" })).toBeTruthy();
  });

  it("renders language as a select of common BCP-47 codes", () => {
    const fields: ProviderSpec["fields"] = [{ name: "language", label: "Language", type: "string", default: "en" }];
    const { container } = render(<RegistryForm fields={fields ?? []} values={{ language: "en" }} onChange={() => {}} idPrefix="l" />);
    const trigger = container.querySelector("#l-language");
    expect(trigger?.getAttribute("role")).toBe("combobox");
    expect(trigger?.textContent).toContain("English · en");
  });

  it("gives number fields a decimal keypad", () => {
    const onChange = vi.fn();
    const fields: ProviderSpec["fields"] = [{ name: "temperature", label: "Temperature", type: "number", default: 0.7 }];
    const { container } = render(<RegistryForm fields={fields ?? []} values={{ temperature: 0.7 }} onChange={onChange} idPrefix="n" />);
    const input = container.querySelector("#n-temperature") as HTMLInputElement;
    expect(input.getAttribute("inputmode")).toBe("decimal");
    fireEvent.change(input, { target: { value: "0.3" } });
    expect(onChange).toHaveBeenCalledWith("temperature", 0.3);
  });

  it("formats json fields and reports invalid JSON inline", () => {
    const onChange = vi.fn();
    const fields: ProviderSpec["fields"] = [{ name: "extra", label: "Extra", type: "json" }];
    const { rerender } = render(<RegistryForm fields={fields ?? []} values={{ extra: '{"a":1}' }} onChange={onChange} idPrefix="j" />);
    fireEvent.click(screen.getByRole("button", { name: "Format" }));
    expect(onChange).toHaveBeenCalledWith("extra", '{\n  "a": 1\n}');

    rerender(<RegistryForm fields={fields ?? []} values={{ extra: "{nope" }} onChange={onChange} idPrefix="j" />);
    fireEvent.click(screen.getByRole("button", { name: "Format" }));
    expect(screen.getByText(/Not valid JSON/)).toBeTruthy();
  });

  it("renders v2 catalog and file fields as text inputs with a hint", () => {
    const fields: ProviderSpec["fields"] = [
      { name: "avatar_id", label: "Avatar", type: "catalog", catalog_kind: "avatars" },
      { name: "credentials_file", label: "Credentials file", type: "file" },
    ];
    const { container } = render(<RegistryForm fields={fields ?? []} values={{}} onChange={() => {}} idPrefix="c" />);
    expect((container.querySelector("#c-avatar_id") as HTMLInputElement).type).toBe("text");
    expect((container.querySelector("#c-credentials_file") as HTMLInputElement).type).toBe("text");
    expect(container.textContent).toContain("Paste the id");
  });

  it("uses 'Leave blank to keep' for masked secrets and drops the Required marker", () => {
    const fields: ProviderSpec["fields"] = [{ name: "api_key", label: "API key", type: "secret", required: true }];
    const { container } = render(
      <RegistryForm fields={fields ?? []} values={{}} onChange={() => {}} secretsMasked idPrefix="m" />,
    );
    expect(container.textContent).toContain("Leave blank to keep");
    expect(container.textContent).not.toContain("Required");
  });
});
