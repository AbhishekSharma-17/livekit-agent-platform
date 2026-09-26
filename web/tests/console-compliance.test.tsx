import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComplianceTab } from "@/components/console/settings/compliance-tab";
import type { ComplianceOut } from "@/contracts/lkap-contracts";

/**
 * Settings → Compliance (V5-17, PLAN-V5 V5-15/V5-17, D-V5-22): jurisdiction
 * radio cards with the api's own preset texts (never hand-copied, ask #86),
 * the two rewrite fields, the counsel-note acknowledgement, and the save
 * round trip (`PUT /v1/workspaces/{id}` `{settings: {compliance: {...}}}`,
 * merged one level down by the api — this test only checks what the console
 * sends).
 */

const PRESETS: ComplianceOut["presets"] = [
  {
    jurisdiction: "eu",
    label: "European Union",
    disclosure_text: "Just so you know, you're talking to an AI assistant, not a person.",
    recording_text: "We'd like to record this call so we keep an accurate record of it. Is that okay with you?",
    counsel_note: "EU law requires telling people when they are talking to an AI. Confirm this wording with counsel.",
  },
  {
    jurisdiction: "in",
    label: "India",
    disclosure_text: "Just so you know, you're talking to an AI assistant.",
    recording_text: "This call can be recorded so we have a record of it. Is it okay if we record it?",
    counsel_note: "India's data protection rules on notice and consent are being phased in. Confirm with counsel.",
  },
  {
    jurisdiction: "us",
    label: "United States",
    disclosure_text: "Just so you know, you're talking to an automated AI assistant.",
    recording_text: "We'd like to record this call for our records. Do we have your permission to record it?",
    counsel_note: "Some states require everyone on a call to agree before it is recorded: confirm with counsel.",
  },
];

let complianceSettings: ComplianceOut["settings"] = { jurisdiction: "in", disclosure_text: null, recording_text: null, counsel_note_ack: false };
let workspacePutBodies: Array<{ settings?: Record<string, unknown> }> = [];
let role: "owner" | "admin" | "member" = "owner";

function complianceOut(): ComplianceOut {
  const preset = PRESETS.find((p) => p.jurisdiction === complianceSettings.jurisdiction) ?? PRESETS[1];
  return {
    settings: complianceSettings,
    effective: {
      jurisdiction: complianceSettings.jurisdiction ?? "in",
      disclosure_text: complianceSettings.disclosure_text || preset.disclosure_text,
      recording_text: complianceSettings.recording_text || preset.recording_text,
    },
    presets: PRESETS,
  };
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            user: { id: "u1", email: "owner@local", name: "Owner", is_platform_admin: false },
            workspaces: [{ id: "w1", slug: "default", name: "Default", role }],
          }),
        } as Response;
      }
      if (method === "PUT" && url.split("?")[0].endsWith("/workspaces/w1")) {
        const body = init?.body ? (JSON.parse(init.body as string) as { settings?: Record<string, unknown> }) : {};
        workspacePutBodies.push(body);
        const incoming = (body.settings?.compliance ?? {}) as Partial<ComplianceOut["settings"]>;
        complianceSettings = { ...complianceSettings, ...incoming };
        return { ok: true, status: 200, json: async () => ({ id: "w1" }) } as Response;
      }
      if (url.split("?")[0].endsWith("/workspaces/w1/compliance")) {
        return { ok: true, status: 200, json: async () => complianceOut() } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }),
  );
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ComplianceTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  role = "owner";
  workspacePutBodies = [];
  complianceSettings = { jurisdiction: "in", disclosure_text: null, recording_text: null, counsel_note_ack: false };
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ComplianceTab", () => {
  it("shows the three jurisdictions with India selected by default and the api's own counsel note", async () => {
    renderTab();

    expect((await screen.findByRole("radio", { name: "India" })).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "European Union" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "United States" })).toBeTruthy();
    expect(screen.getByText(/India's data protection rules/)).toBeTruthy();
  });

  it("shows the preset wording as the hint, not hand-copied into the field", async () => {
    renderTab();
    await screen.findByRole("radio", { name: "India" });
    expect(screen.getByText(/This call can be recorded so we have a record of it/)).toBeTruthy();
  });

  it("saves the jurisdiction, both rewrites and the counsel acknowledgement", async () => {
    renderTab();
    await screen.findByRole("radio", { name: "India" });

    fireEvent.click(screen.getByRole("radio", { name: "European Union" }));
    fireEvent.change(screen.getByLabelText("AI disclosure line"), { target: { value: "We use an AI assistant here." } });
    fireEvent.click(screen.getByLabelText(/needs review by counsel/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(workspacePutBodies).toHaveLength(1));
    expect(workspacePutBodies[0].settings).toEqual({
      compliance: {
        jurisdiction: "eu",
        disclosure_text: "We use an AI assistant here.",
        recording_text: null,
        counsel_note_ack: true,
      },
    });
  });

  it("saves an empty rewrite as null (falls back to the preset)", async () => {
    complianceSettings = { jurisdiction: "us", disclosure_text: "Custom wording.", recording_text: null, counsel_note_ack: true };
    renderTab();
    const field = await screen.findByLabelText("AI disclosure line");
    expect((field as HTMLTextAreaElement).value).toBe("Custom wording.");

    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(workspacePutBodies).toHaveLength(1));
    expect(workspacePutBodies[0].settings?.compliance).toMatchObject({ disclosure_text: null });
  });

  it("has no jargon in the preset wording shown to the builder", async () => {
    renderTab();
    await screen.findByRole("radio", { name: "India" });
    const text = document.body.textContent ?? "";
    for (const jargon of ["RRF", "PKCE", "HMAC", "CIMD", "DCR"]) {
      expect(text).not.toContain(jargon);
    }
  });

  it("hides the Save button for a member without manage rights", async () => {
    role = "member";
    renderTab();
    await screen.findByRole("radio", { name: "India" });
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
