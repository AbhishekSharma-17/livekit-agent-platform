import { describe, expect, it } from "vitest";

import { widgetSnippet } from "@/components/console/agents/test-chat/add-to-website-dialog";

describe("widgetSnippet", () => {
  it("embeds the agent's slug and the current origin as widget.js's data-agent script tag", () => {
    const snippet = widgetSnippet("my-agent", "https://lkap.example");
    expect(snippet).toBe('<script src="https://lkap.example/widget.js" data-agent="my-agent"></script>');
  });

  it("matches the shape widget.js's own tests expect (data-agent + a /widget.js src)", () => {
    const snippet = widgetSnippet("insurance-claim", "https://console.example");
    expect(snippet).toContain('data-agent="insurance-claim"');
    expect(snippet).toContain("https://console.example/widget.js");
  });
});
