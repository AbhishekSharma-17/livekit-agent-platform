import * as React from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RegistryForm } from "@/components/console/registry/registry-form";
import type { FieldSpec } from "@/contracts/lkap-contracts";

/**
 * The avatar catalog combobox V2-13 adds to `RegistryForm` (a `field.type
 * === "catalog"`, or an avatar provider's name-matched id field — see
 * `CatalogFieldContext`'s docstring for the name-convention heuristic and
 * the follow-up ask it flags). Covers the card's acceptance line "catalog
 * combobox falls back to free text when the adapter 404s".
 */

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const AVATAR_ID_FIELD: FieldSpec = {
  name: "avatar_id",
  label: "Avatar id",
  type: "string",
  default: "b9be11b8-89fb-4227-8f86-4a881393cbdb",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function Harness({ value = "" }: { value?: string }) {
  const [values, setValues] = React.useState<Record<string, string>>({ avatar_id: value });
  return (
    <RegistryForm
      fields={[AVATAR_ID_FIELD]}
      values={values}
      onChange={(name, next) => setValues((prev) => ({ ...prev, [name]: String(next) }))}
      catalogContext={{ providerId: "bey-avatar", kind: "avatar", catalog: { adapter: "bey_avatars", kinds: ["avatars"] }, credentialId: "cred-1" }}
    />
  );
}

describe("RegistryForm — avatar catalog field", () => {
  it("renders a Select of vendor items with a preview thumbnail when the vendor's item carries one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          kind: "avatars",
          items: [
            { id: "a1", label: "Aria", meta: { image_url: "https://cdn.example.com/a1.png" } },
            { id: "a2", label: "Bo" },
          ],
        }),
      })),
    );
    const { container } = renderWithClient(<Harness value="a1" />);
    expect(screen.getByRole("combobox")).toBeTruthy();
    // A decorative preview (`alt=""`) isn't exposed with role "img"; wait for
    // the catalog fetch (and the selected item it resolves) to land.
    await waitFor(() => expect(container.querySelector('img[src="https://cdn.example.com/a1.png"]')).toBeTruthy());
  });

  it("falls back to a free-text input when the catalog adapter 404s / errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404, json: async () => ({ error: { code: "not_found", message: "no adapter" } }) })),
    );
    renderWithClient(<Harness value="existing-custom-id" />);
    await waitFor(() => expect(screen.getByPlaceholderText(/Paste the id/)).toBeTruthy());
    expect((screen.getByPlaceholderText(/Paste the id/) as HTMLInputElement).value).toBe("existing-custom-id");
  });

  it("falls back to free text when the catalog has no items yet (no key set)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ kind: "avatars", items: [] }) })),
    );
    renderWithClient(<Harness />);
    await waitFor(() => expect(screen.getByPlaceholderText(/Paste the id/)).toBeTruthy());
  });

  it("lets the user switch back to the catalog picker from manual entry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ kind: "avatars", items: [{ id: "a1", label: "Aria" }] }),
      })),
    );
    renderWithClient(<Harness value="a-custom-id-not-in-list" />);
    // The catalog fetch is in flight at mount (a loading `Select`, not the
    // manual view yet — "unknown id" can only be decided once it resolves);
    // it then settles on manual entry because the id isn't in the list, with
    // the "switch back" escape since the catalog does have items.
    await waitFor(() => expect(screen.getByPlaceholderText(/Paste the id/)).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /Choose from the catalog instead/ }));
    expect(await screen.findByRole("combobox")).toBeTruthy();
  });
});
