import * as React from "react";

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

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
});
