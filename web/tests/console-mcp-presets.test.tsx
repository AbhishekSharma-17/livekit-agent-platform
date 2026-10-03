import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AVAILABLE_MCP_PRESETS, MCP_PRESETS, mcpPresetById } from "@/components/console/tools/mcp-presets";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";

/**
 * MCP presets (V5-21; `docs/v5/PLAN-V5.md`'s V5-21 card acceptance): every
 * shipped preset has an `https` url and a non-empty `auth_matrix`; the two
 * unverified ones never render; Stripe offers Header + Sign in; Slack offers
 * only "Your own OAuth app".
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function renderWithClient(ui: React.ReactElement) {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function stubFetch() {
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function pickPreset(name: string) {
  fireEvent.click(screen.getByRole("combobox", { name: "Start from a preset" }));
  await screen.findByPlaceholderText("Search presets");
  fireEvent.click(await screen.findByText(name));
}

describe("MCP presets — data shape", () => {
  it("every available preset has an https url and a non-empty auth_matrix", () => {
    expect(AVAILABLE_MCP_PRESETS.length).toBeGreaterThan(0);
    for (const preset of AVAILABLE_MCP_PRESETS) {
      expect(preset.status).toBe("available");
      expect(preset.url.startsWith("https://")).toBe(true);
      expect(preset.auth_matrix.length).toBeGreaterThan(0);
    }
  });

  it("never renders the two unverified presets", () => {
    const ids = AVAILABLE_MCP_PRESETS.map((p) => p.id);
    expect(ids).not.toContain("zendesk");
    expect(ids).not.toContain("salesforce");
    // They still exist as data (D-V5-10: hidden, not deleted).
    expect(mcpPresetById("zendesk")?.status).toBe("unverified");
    expect(mcpPresetById("salesforce")?.status).toBe("unverified");
  });

  it("has exactly the eighteen unverified-excluded ids MCP_PRESETS ships", () => {
    // 19 total records (17 available + 2 unverified) — a sanity count so a
    // future edit that silently drops a preset fails a test, not a demo.
    expect(MCP_PRESETS.length).toBe(19);
    expect(AVAILABLE_MCP_PRESETS.length).toBe(17);
  });

  it("Stripe offers Header and Sign in with the vendor", () => {
    const stripe = mcpPresetById("stripe");
    expect(stripe?.auth_matrix).toEqual(expect.arrayContaining(["header", "oauth"]));
  });

  it("Slack offers only Your own OAuth app", () => {
    const slack = mcpPresetById("slack");
    expect(slack?.auth_matrix).toEqual(["own_oauth"]);
  });
});

describe("MCP presets — dialog integration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("leads the dialog title with the Model Context Protocol mark and keeps its words", async () => {
    stubFetch();
    renderWithClient(
      <McpToolEditorDialog agentId={null} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(screen.getByText("New MCP server"));
    const title = await screen.findByRole("heading", { name: "New MCP server" });
    expect(title.querySelector('[data-slot="vendor-mark"]')?.getAttribute("data-mark")).toBe("modelcontextprotocol");
    expect(title.querySelector('[data-slot="vendor-mark"]')?.getAttribute("aria-hidden")).toBe("true");
  });

  it(
    "choosing Stripe offers Header and Sign in with the vendor, not Your own OAuth app",
    async () => {
      stubFetch();
      renderWithClient(
        <McpToolEditorDialog agentId={null} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
      );
      fireEvent.click(screen.getByText("New MCP server"));
      await pickPreset("Stripe");

      expect(await screen.findByText("Header (API key)")).toBeTruthy();
      expect(screen.getByText("Sign in with the vendor")).toBeTruthy();
      expect(screen.queryByText("Your own OAuth app")).toBeNull();
      // The url and name are prefilled from the preset.
      expect((screen.getByLabelText("URL") as HTMLInputElement).value).toBe("https://mcp.stripe.com");
    },
    // Popover + Command (cmdk) interaction inside a Dialog is measurably slower in
    // jsdom than the rest of this suite (`vitest.config.ts`'s own note on
    // dialog-heavy tests); a generous per-test ceiling avoids flaking under a
    // loaded, fully-parallel run without masking a real hang.
    60_000,
  );

  it(
    "choosing Slack offers only Your own OAuth app and asks for a client id",
    async () => {
      stubFetch();
      renderWithClient(
        <McpToolEditorDialog agentId={null} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
      );
      fireEvent.click(screen.getByText("New MCP server"));
      await pickPreset("Slack");

      expect(await screen.findByText("Your own OAuth app")).toBeTruthy();
      expect(screen.queryByText("Header (API key)")).toBeNull();
      expect(screen.queryByText("Sign in with the vendor")).toBeNull();
      // The single option is pre-selected, so the client-id field is already showing.
      expect(await screen.findByLabelText("Client id")).toBeTruthy();
      // No saved tool yet — Connect/Test are both gated behind a save.
      expect(screen.getByText("Save the server first, then sign in.")).toBeTruthy();
      expect(screen.getByText("Save the server first, then test the connection.")).toBeTruthy();
    },
    60_000,
  );

  it("no jargon in the auth mode copy", async () => {
    stubFetch();
    renderWithClient(
      <McpToolEditorDialog agentId={null} secretBagSpec={undefined} onSaved={vi.fn()} trigger={<button>New MCP server</button>} />,
    );
    fireEvent.click(screen.getByText("New MCP server"));
    const dialog = await screen.findByRole("dialog");
    const bodyText = dialog.textContent ?? "";
    for (const jargon of ["DCR", "CIMD", "PKCE"]) {
      expect(bodyText).not.toContain(jargon);
    }
  });
});
