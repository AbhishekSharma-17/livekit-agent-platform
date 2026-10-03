import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClientName } from "@/components/console/settings/agent-keys-table";
import { AGENT_KEY_CLIENTS, SNIPPET_TABS, clientMarkKey } from "@/components/console/settings/snippets";
import { ModelName } from "@/components/shared/vendor-mark";
import { vendorMarkFor } from "@/components/shared/vendor-marks";

/**
 * V6-38 (UI-R6): logos wherever the console names a company or a product.
 * The mark table itself is covered by `vendor-marks.test.tsx`. These tests hold
 * the client lists and the small components the screens share.
 */

describe("client marks", () => {
  it("gives every client option and snippet tab a real mark, and Other MCP client the MCP mark", () => {
    for (const client of AGENT_KEY_CLIENTS) expect(vendorMarkFor(client.mark), client.id).not.toBeNull();
    for (const tab of SNIPPET_TABS) expect(vendorMarkFor(tab.mark), tab.id).not.toBeNull();
    expect(vendorMarkFor(AGENT_KEY_CLIENTS.find((c) => c.id === "other")!.mark)?.slug).toBe("modelcontextprotocol");
  });

  it.each([
    ["claude-code", "claude"],
    ["codex", "codex"],
    ["cursor", "cursor"],
    ["other", "modelcontextprotocol"],
    // MCP `clientInfo` names a product sends on connect
    ["cursor-vscode", "cursor"],
    ["codex-mcp-client", "codex"],
    ["claude-ai", "claude"],
    ["gemini-cli-mcp-client", "geminicli"],
    ["mcp-remote", "modelcontextprotocol"],
  ])("clientMarkKey(%s) resolves to the %s mark", (client, slug) => {
    expect(vendorMarkFor(clientMarkKey(client))?.slug).toBe(slug);
  });

  it.each(["my-script", "continue", "continue_search", "copilot", "vscode", "Visual Studio Code"])(
    "clientMarkKey(%s) has no mark (no bare continue or copilot key, Visual Studio Code keeps the monogram)",
    (client) => {
      expect(vendorMarkFor(clientMarkKey(client))).toBeNull();
    },
  );
});

describe("ClientName", () => {
  it("prints a console client's label with its mark before it", () => {
    const { container } = render(<ClientName client="claude-code" />);
    expect(container.textContent).toBe("Claude Code");
    expect(container.firstElementChild?.firstElementChild?.getAttribute("data-mark")).toBe("claude");
    expect(container.querySelector('[data-slot="vendor-mark"]')?.getAttribute("aria-hidden")).toBe("true");
  });

  it("marks an MCP clientInfo name and keeps its text", () => {
    const { container } = render(<ClientName client="cursor-vscode" />);
    expect(container.textContent).toBe("cursor-vscode");
    expect(container.querySelector('[data-slot="vendor-mark"]')?.getAttribute("data-mark")).toBe("cursor");
  });

  it("prints an unknown client alone, and no client as the empty value", () => {
    const unknown = render(<ClientName client="my-script" />);
    expect(unknown.container.textContent).toBe("my-script");
    expect(unknown.container.querySelector('[data-slot="vendor-mark"]')).toBeNull();
    const none = render(<ClientName client={null} />);
    expect(none.container.querySelector('[data-slot="vendor-mark"]')).toBeNull();
    expect(none.container.textContent).not.toBe("");
  });
});

describe("ModelName", () => {
  it("leads a maker/model id with the maker's mark and keeps the whole id", () => {
    const { container } = render(<ModelName model="deepgram/nova-3" />);
    expect(container.textContent).toBe("deepgram/nova-3");
    expect(container.querySelector('[data-slot="vendor-mark"]')?.getAttribute("data-mark")).toBe("deepgram");
  });

  it.each(["nova-3", "gpt-5-mini", "somebody-new/model-1"])("prints %s alone, with no mark", (model) => {
    const { container } = render(<ModelName model={model} />);
    expect(container.textContent).toBe(model);
    expect(container.querySelector('[data-slot="vendor-mark"]')).toBeNull();
  });
});
